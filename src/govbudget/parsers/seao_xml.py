"""Parse legacy SEAO XML (2009 to March 2021) into ContractAward records.

SEAO's pre-OCDS XML has no published machine-readable schema, and the exact tag
spellings vary across the decade the files span. Rather than hard-code one
guess, this parser:

1. normalizes every tag (namespace, accents, case, punctuation all stripped) so
   'NuméroSEAO', 'NUMERO_SEAO' and '{ns}numero-seao' collapse to 'numeroseao';
2. resolves each canonical field against an ordered tuple of candidate tags, so
   adding a newly-observed spelling is a one-line change to FIELD_MAP;
3. records anything it could not map into ContractAward.unmapped.

Run `govbudget seao:inspect <file>` against a real file to see the actual
element paths and their frequencies, then correct FIELD_MAP from the output.
That command exists precisely because these mappings are unverified.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterator
from typing import Any

from lxml import etree

from ..models import ContractAward
from ._util import clean_text, normalize_tag, parse_amount, parse_date, parse_int

log = logging.getLogger(__name__)

#: Elements that plausibly delimit one contract notice.
RECORD_TAGS = ("avis", "contrat", "contract", "release", "item")

#: Nested elements that repeat once per supplier on an award.
SUPPLIER_TAGS = ("fournisseur", "contractant", "adjudicataire", "soumissionnaire", "supplier")

#: Canonical field -> candidate normalized tags, in preference order.
FIELD_MAP: dict[str, tuple[str, ...]] = {
    "notice_number": ("numeroseao", "noseao", "numeroavis", "numero", "id"),
    "title": ("titre", "titreavis", "objet", "nomcontrat"),
    "description": ("description", "descriptionavis", "precisions"),
    "buyer_name": ("organisme", "nomorganisme", "donneurdouvrage", "acheteur", "entiteacheteuse"),
    "buyer_id": ("numeroorganisme", "idorganisme", "codeorganisme"),
    "buyer_category": ("categorieorganisme", "typeorganisme", "reseau", "secteur"),
    "procurement_method": ("typeavis", "natureavis", "modeadjudication", "typeadjudication"),
    "procurement_category": ("categorieseao", "categorie", "naturecontrat", "typecontrat"),
    "unspsc_code": ("unspsc", "codeunspsc", "classification"),
    "publication_date": ("datepublication", "datepub", "datediffusion", "datesaisie"),
    "award_date": ("dateadjudication", "datefinale", "dateoctroi", "datecontrat", "datefermeture"),
    "contract_start": ("datedebutcontrat", "datedebut"),
    "contract_end": ("datefincontrat", "datefin"),
    "number_of_bidders": ("nombresoumissions", "nbsoumissions", "nombresoumissionnaires"),
}

#: Amount fields, most authoritative first: a final settled amount beats the
#: originally contracted amount, which beats the bid.
AMOUNT_FIELDS = (
    "montantfinal",
    "montanttotalcontrat",
    "montantcontrat",
    "montantentente",
    "montant",
    "montantsoumis",
    "prix",
)

SUPPLIER_NAME_FIELDS = ("nomorganisation", "nomfournisseur", "nom", "raisonsociale", "name")
SUPPLIER_NEQ_FIELDS = ("neq", "numeroentreprise", "numeroentreprisequebec")
SUPPLIER_CITY_FIELDS = ("ville", "municipalite", "city")
SUPPLIER_REGION_FIELDS = ("province", "region", "regionadministrative")


def parse(
    payload: bytes | str,
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> list[ContractAward]:
    """Parse a SEAO XML document into contract awards."""
    return list(iter_parse(payload, source_id=source_id, content_hash=content_hash))


def iter_parse(
    payload: bytes | str,
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> Iterator[ContractAward]:
    """Stream awards from a SEAO XML document.

    Streaming matters: the yearly files cover every contract in Quebec for a
    year and are large enough that building a full tree is wasteful.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not data.strip():
        return

    record_tag = _detect_record_tag(data)
    if record_tag is None:
        log.warning("no repeating record element found; document may not be SEAO XML")
        return

    # iterparse takes recovery options directly; it rejects a `parser` object.
    context = etree.iterparse(_as_stream(data), events=("end",), recover=True, huge_tree=True)

    for _, element in context:
        if normalize_tag(element.tag) != record_tag:
            continue
        yield from _record_to_awards(element, source_id=source_id, content_hash=content_hash)
        element.clear()
        # Drop already-processed siblings so memory stays flat across the file.
        parent = element.getparent()
        if parent is not None:
            while element.getprevious() is not None:
                del parent[0]


def _as_stream(data: bytes):
    from io import BytesIO

    return BytesIO(data)


def _detect_record_tag(data: bytes) -> str | None:
    """Find which element repeats once per contract notice.

    Prefers a known candidate from RECORD_TAGS; otherwise falls back to the most
    frequent element that has element children, which is the record row in every
    tabular XML dump we have seen.
    """
    counts: Counter[str] = Counter()
    has_children: set[str] = set()

    context = etree.iterparse(_as_stream(data), events=("end",), recover=True, huge_tree=True)
    scanned = 0
    for _, element in context:
        tag = normalize_tag(element.tag)
        counts[tag] += 1
        if len(element) > 0:
            has_children.add(tag)
        scanned += 1
        # A few thousand elements is plenty to identify the repeating row.
        if scanned > 20000:
            break

    for candidate in RECORD_TAGS:
        if counts.get(candidate, 0) > 0 and candidate in has_children:
            return candidate

    container = {t for t in has_children if counts[t] > 1}
    if not container:
        return None
    return max(container, key=lambda tag: counts[tag])


def _record_to_awards(
    element: etree._Element, *, source_id: str, content_hash: str
) -> Iterator[ContractAward]:
    fields, suppliers, unmapped = _extract(element)

    base = {
        "source_id": source_id,
        "source_content_hash": content_hash,
        "source_format": "seao-xml",
        "notice_number": _first(fields, FIELD_MAP["notice_number"]),
        "title": _first(fields, FIELD_MAP["title"]),
        "description": _first(fields, FIELD_MAP["description"]),
        "buyer_name": _first(fields, FIELD_MAP["buyer_name"]),
        "buyer_id": _first(fields, FIELD_MAP["buyer_id"]),
        "buyer_category": _first(fields, FIELD_MAP["buyer_category"]),
        "procurement_method": _first(fields, FIELD_MAP["procurement_method"]),
        "procurement_category": _first(fields, FIELD_MAP["procurement_category"]),
        "unspsc_code": _first(fields, FIELD_MAP["unspsc_code"]),
        "publication_date": parse_date(_first(fields, FIELD_MAP["publication_date"])),
        "award_date": parse_date(_first(fields, FIELD_MAP["award_date"])),
        "contract_start": parse_date(_first(fields, FIELD_MAP["contract_start"])),
        "contract_end": parse_date(_first(fields, FIELD_MAP["contract_end"])),
        "number_of_bidders": parse_int(_first(fields, FIELD_MAP["number_of_bidders"])),
        "currency": "CAD",
    }
    record_amount = parse_amount(_first(fields, AMOUNT_FIELDS))

    if not suppliers:
        # No supplier block: still worth emitting if there is an amount, since
        # some notices carry the award inline.
        if record_amount is not None or base["notice_number"]:
            yield ContractAward(**base, amount=record_amount, unmapped=unmapped)
        return

    for supplier in suppliers:
        yield ContractAward(
            **base,
            amount=parse_amount(_first(supplier, AMOUNT_FIELDS)) or record_amount,
            supplier_name=_first(supplier, SUPPLIER_NAME_FIELDS),
            supplier_neq=_first(supplier, SUPPLIER_NEQ_FIELDS),
            supplier_city=_first(supplier, SUPPLIER_CITY_FIELDS),
            supplier_region=_first(supplier, SUPPLIER_REGION_FIELDS),
            unmapped=unmapped,
        )


def _extract(
    element: etree._Element,
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, Any]]:
    """Split a record into its own leaf fields and its nested supplier blocks."""
    fields: dict[str, str] = {}
    suppliers: list[dict[str, str]] = []
    mapped_tags = _all_known_tags()
    unmapped: dict[str, Any] = {}

    def collect_leaves(node: etree._Element, into: dict[str, str]) -> None:
        for child in node:
            if not isinstance(child.tag, str):  # comments, processing instructions
                continue
            tag = normalize_tag(child.tag)
            if len(child) > 0:
                collect_leaves(child, into)
                continue
            text = clean_text(child.text)
            if text and tag not in into:
                into[tag] = text

    for child in element:
        if not isinstance(child.tag, str):
            continue
        tag = normalize_tag(child.tag)

        if tag in SUPPLIER_TAGS:
            block: dict[str, str] = {}
            collect_leaves(child, block)
            if block:
                suppliers.append(block)
            continue

        # A wrapper such as <Fournisseurs> holding <Fournisseur> children.
        nested = [c for c in child if isinstance(c.tag, str) and normalize_tag(c.tag) in SUPPLIER_TAGS]
        if nested:
            for supplier_element in nested:
                block = {}
                collect_leaves(supplier_element, block)
                if block:
                    suppliers.append(block)
            continue

        if len(child) > 0:
            collect_leaves(child, fields)
        else:
            text = clean_text(child.text)
            if text and tag not in fields:
                fields[tag] = text

    for tag, value in fields.items():
        if tag not in mapped_tags:
            unmapped[tag] = value

    return fields, suppliers, unmapped


def _all_known_tags() -> set[str]:
    known: set[str] = set()
    for candidates in FIELD_MAP.values():
        known.update(candidates)
    known.update(AMOUNT_FIELDS)
    return known


def _first(source: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        value = source.get(candidate)
        if value:
            return value
    return None


# -- structure inspection -------------------------------------------------


def inspect_structure(payload: bytes | str, *, max_samples: int = 3) -> dict[str, Any]:
    """Report the element paths a document actually contains.

    Returns paths with their frequency and a few sample values, plus which tags
    FIELD_MAP does not currently cover. This is the tool for correcting the
    mappings above against a real file.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(data, parser=parser)
    if root is None:
        return {"error": "document did not parse"}

    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    def walk(node: etree._Element, path: str) -> None:
        for child in node:
            if not isinstance(child.tag, str):
                continue
            tag = normalize_tag(child.tag)
            child_path = f"{path}/{tag}" if path else tag
            counts[child_path] += 1
            if len(child) == 0:
                text = clean_text(child.text)
                if text and len(samples[child_path]) < max_samples:
                    samples[child_path].append(text[:120])
            else:
                walk(child, child_path)

    root_tag = normalize_tag(root.tag)
    counts[root_tag] += 1
    walk(root, root_tag)

    known = _all_known_tags() | set(SUPPLIER_NAME_FIELDS) | set(SUPPLIER_NEQ_FIELDS)
    known |= set(SUPPLIER_CITY_FIELDS) | set(SUPPLIER_REGION_FIELDS) | set(SUPPLIER_TAGS)
    leaf_tags = {path.rsplit("/", 1)[-1] for path in counts if path in samples}

    return {
        "root": root_tag,
        "detected_record_tag": _detect_record_tag(data),
        "paths": [
            {"path": path, "count": count, "samples": samples.get(path, [])}
            for path, count in counts.most_common()
        ],
        "unmapped_leaf_tags": sorted(leaf_tags - known),
    }
