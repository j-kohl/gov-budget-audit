"""Parse SEAO XML into contract awards, final amounts and supplementary expenses.

Written against "Format XML pour les données ouvertes du SEAO" (Secrétariat du
Conseil du trésor, 1 December 2014), which SEAO publishes alongside the data on
Données Québec, and verified against real monthly archives. The code tables live
in `govbudget.seao_codes`.

Each monthly archive holds six files:

    Avis_*.xml               notices, their buyer, and every bidder
    Contrats_*.xml           final settled amount per contract
    Depenses_*.xml           spending beyond the original contract
    *Revisions_*.xml         corrections to previously published records

All use `<export>` as the root with lowercase, unaccented tag names. The record
element differs per file, so parsing routes on what it finds rather than on the
filename, which keeps it working for the yearly archives whose members are named
differently.

Three things in this format will silently corrupt a spending total:

1. `<fournisseurs>` lists **every bidder**, not just the winner. Only
   `<adjudicataire>1</adjudicataire>` is an award.
2. `<montanttotalcontrat>` is frequently `0.000000` while `<montantcontrat>`
   holds the real figure, so naive field-preference picks zero.
3. `<montantssoumisunite>` says what the amount *is*: dollars, but also $/km,
   percent, points or dollars-per-hour. Only unit 1 may be summed.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterator
from io import BytesIO
from typing import Any

from lxml import etree

from ..models import ContractAward, ContractExpense, ContractFinal
from ..seao_codes import AMOUNT_UNIT, DELIVERY_REGION, NATURE, NOTICE_TYPE, label
from ._util import clean_text, normalize_tag, parse_amount, parse_date

log = logging.getLogger(__name__)

#: Record elements across the file types.
RECORD_TAGS = ("avis", "contrat")

SUPPLIER_TAG = "fournisseur"
EXPENSE_TAG = "depense"

#: Amount fields on a supplier, most authoritative first. Zeros are rejected
#: during lookup — see `_amount`.
SUPPLIER_AMOUNT_FIELDS = ("montanttotalcontrat", "montantcontrat", "montantsoumis")

#: Notice fields covered by the 2014 specification. Anything else lands in
#: `unmapped` so a schema change shows up rather than being dropped.
KNOWN_NOTICE_FIELDS = frozenset(
    {
        "numeroseao", "numero", "organisme", "municipal", "adresse1", "adresse2",
        "ville", "province", "pays", "codepostal", "titre", "type", "nature",
        "precision", "datepublication", "datefermeture", "datesaisieouverture",
        "datesaisieadjudication", "dateadjudication", "regionlivraison",
        "unspscprincipale", "disposition", "hyperlienseao",
        # Present on essentially every notice but absent from the 2014
        # specification. Carries SEAO's own category taxonomy, e.g.
        # 'C03 - Autres travaux de construction'.
        "categorieseao",
    }
)

KNOWN_FINAL_FIELDS = frozenset(
    {
        "numeroseao", "numero", "datefinale", "datepublicationfinale",
        "montantfinal", "nomcontractant", "neqcontractant",
    }
)

KNOWN_EXPENSE_FIELDS = frozenset(
    {
        "datedepense", "datepublicationdepense", "montantdepense",
        "description", "nomcontractant", "neqcontractant",
    }
)


def parse(
    payload: bytes | str,
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> list[ContractAward]:
    """Parse an Avis file into contract awards."""
    awards, _, _ = parse_all(payload, source_id=source_id, content_hash=content_hash)
    return awards


def parse_all(
    payload: bytes | str,
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> tuple[list[ContractAward], list[ContractFinal], list[ContractExpense]]:
    """Parse any SEAO XML file, returning whichever record types it contains."""
    awards: list[ContractAward] = []
    finals: list[ContractFinal] = []
    expenses: list[ContractExpense] = []

    for kind, element in _iter_records(payload):
        if kind == "contrat":
            finals.append(_to_final(element, source_id, content_hash))
        elif kind == "depense":
            expenses.extend(_to_expenses(element, source_id, content_hash))
        else:
            awards.extend(_to_awards(element, source_id, content_hash))

    return awards, finals, expenses


def iter_parse(
    payload: bytes | str,
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> Iterator[ContractAward]:
    """Stream awards from an Avis file.

    The yearly archives are large enough that the notice files are streamed and
    cleared as they go rather than held as a tree.
    """
    for kind, element in _iter_records(payload):
        if kind == "avis":
            yield from _to_awards(element, source_id, content_hash)


def _iter_records(payload: bytes | str) -> Iterator[tuple[str, etree._Element]]:
    """Yield (kind, element) per record, clearing processed nodes as it goes.

    `kind` is 'avis', 'contrat' or 'depense'. An <avis> carrying <depenses>
    comes from the expenses file and is reported as 'depense'.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not data.strip():
        return

    context = etree.iterparse(BytesIO(data), events=("end",), recover=True, huge_tree=True)
    for _, element in context:
        tag = normalize_tag(element.tag)
        if tag not in RECORD_TAGS:
            continue

        if tag == "contrat":
            yield "contrat", element
        else:
            has_expenses = any(
                normalize_tag(child.tag) == "depenses"
                for child in element
                if isinstance(child.tag, str)
            )
            yield ("depense" if has_expenses else "avis"), element

        element.clear()
        parent = element.getparent()
        if parent is not None:
            while element.getprevious() is not None:
                del parent[0]


# -- field access ---------------------------------------------------------


def _leaves(element: etree._Element, *, skip: set[str] | None = None) -> dict[str, str]:
    """Direct child leaf values, keyed by normalized tag."""
    skip = skip or set()
    values: dict[str, str] = {}
    for child in element:
        if not isinstance(child.tag, str):
            continue
        tag = normalize_tag(child.tag)
        if tag in skip or len(child) > 0:
            continue
        text = clean_text(child.text)
        if text:
            values[tag] = text
    return values


def _children(element: etree._Element, wrapper: str, item: str) -> list[etree._Element]:
    """Find repeated <item> elements, whether or not they sit inside <wrapper>."""
    found: list[etree._Element] = []
    for child in element:
        if not isinstance(child.tag, str):
            continue
        tag = normalize_tag(child.tag)
        if tag == item:
            found.append(child)
        elif tag == wrapper:
            found.extend(
                grandchild
                for grandchild in child
                if isinstance(grandchild.tag, str) and normalize_tag(grandchild.tag) == item
            )
    return found


def _amount(values: dict[str, str], fields: tuple[str, ...]) -> float | None:
    """First non-zero amount among `fields`, falling back to zero if that is all.

    Zero is skipped deliberately: SEAO writes 0.000000 in `montanttotalcontrat`
    to mean "not applicable", so honouring it would pick zero over the real
    figure sitting in the next field.
    """
    fallback: float | None = None
    for name in fields:
        amount = parse_amount(values.get(name))
        if amount is None:
            continue
        if amount != 0:
            return amount
        if fallback is None:
            fallback = amount
    return fallback


def _flag(value: str | None) -> bool | None:
    """SEAO 0/1 flags, where empty means 'not disclosed' rather than false."""
    text = clean_text(value)
    if text in {"1", "0"}:
        return text == "1"
    return None


# -- record builders ------------------------------------------------------


def _to_awards(element: etree._Element, source_id: str, content_hash: str) -> list[ContractAward]:
    notice = _leaves(element, skip={"fournisseurs"})
    suppliers = _children(element, "fournisseurs", SUPPLIER_TAG)

    notice_type = notice.get("type")
    nature = notice.get("nature")
    region = notice.get("regionlivraison")

    base: dict[str, Any] = {
        "source_id": source_id,
        "source_content_hash": content_hash,
        "source_format": "seao-xml",
        "notice_number": notice.get("numeroseao"),
        "buyer_reference": notice.get("numero"),
        "buyer_name": notice.get("organisme"),
        "buyer_city": notice.get("ville"),
        "buyer_region": notice.get("province"),
        "is_municipal": _flag(notice.get("municipal")),
        "title": notice.get("titre"),
        "description": notice.get("precision"),
        "notice_type_code": notice_type,
        "notice_type_label": label(NOTICE_TYPE, notice_type),
        "nature_code": nature,
        "nature_label": label(NATURE, nature),
        "procurement_method": label(NOTICE_TYPE, notice_type),
        "procurement_category": label(NATURE, nature),
        "seao_category": notice.get("categorieseao"),
        "unspsc_code": notice.get("unspscprincipale"),
        "delivery_region_code": region,
        "delivery_region_label": label(DELIVERY_REGION, region),
        "disposition_code": notice.get("disposition"),
        "publication_date": parse_date(notice.get("datepublication")),
        "closing_date": parse_date(notice.get("datefermeture")),
        "award_date": parse_date(notice.get("dateadjudication")),
        "currency": "CAD",
        "number_of_bidders": len(suppliers) or None,
        "seao_url": notice.get("hyperlienseao"),
        "unmapped": {k: v for k, v in notice.items() if k not in KNOWN_NOTICE_FIELDS},
    }

    if not suppliers:
        return [ContractAward(**base)]

    awards = []
    for supplier_element in suppliers:
        supplier = _leaves(supplier_element)
        unit = supplier.get("montantssoumisunite")
        awards.append(
            ContractAward(
                **base,
                supplier_name=supplier.get("nomorganisation"),
                supplier_neq=supplier.get("neq"),
                supplier_city=supplier.get("ville"),
                supplier_region=supplier.get("province"),
                supplier_country=supplier.get("pays"),
                supplier_postal_code=supplier.get("codepostal"),
                is_winner=_flag(supplier.get("adjudicataire")),
                is_compliant=_flag(supplier.get("conforme")),
                is_eligible=_flag(supplier.get("admissible")),
                amount=_amount(supplier, SUPPLIER_AMOUNT_FIELDS),
                amount_unit_code=unit,
                amount_unit_label=label(AMOUNT_UNIT, unit),
            )
        )
    return awards


def _to_final(element: etree._Element, source_id: str, content_hash: str) -> ContractFinal:
    values = _leaves(element)
    return ContractFinal(
        source_id=source_id,
        source_content_hash=content_hash,
        source_format="seao-xml",
        notice_number=values.get("numeroseao"),
        buyer_reference=values.get("numero"),
        final_date=parse_date(values.get("datefinale")),
        final_publication_date=parse_date(values.get("datepublicationfinale")),
        final_amount=parse_amount(values.get("montantfinal")),
        supplier_name=values.get("nomcontractant"),
        supplier_neq=values.get("neqcontractant"),
        unmapped={k: v for k, v in values.items() if k not in KNOWN_FINAL_FIELDS},
    )


def _to_expenses(
    element: etree._Element, source_id: str, content_hash: str
) -> list[ContractExpense]:
    notice = _leaves(element, skip={"depenses"})
    expenses = []
    for expense_element in _children(element, "depenses", EXPENSE_TAG):
        values = _leaves(expense_element)
        expenses.append(
            ContractExpense(
                source_id=source_id,
                source_content_hash=content_hash,
                source_format="seao-xml",
                notice_number=notice.get("numeroseao"),
                buyer_reference=notice.get("numero"),
                expense_date=parse_date(values.get("datedepense")),
                expense_publication_date=parse_date(values.get("datepublicationdepense")),
                amount=parse_amount(values.get("montantdepense")),
                description=values.get("description"),
                supplier_name=values.get("nomcontractant"),
                supplier_neq=values.get("neqcontractant"),
                unmapped={k: v for k, v in values.items() if k not in KNOWN_EXPENSE_FIELDS},
            )
        )
    return expenses


# -- structure inspection -------------------------------------------------


def inspect_structure(payload: bytes | str, *, max_samples: int = 3) -> dict[str, Any]:
    """Report the element paths a document actually contains.

    A first-contact tool for archives whose layout differs from the 2014
    specification — the earliest yearly files predate it.
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

    leaf_paths = {p for p in counts if p in samples}
    leaf_tags = {p.rsplit("/", 1)[-1] for p in leaf_paths}
    known = KNOWN_NOTICE_FIELDS | KNOWN_FINAL_FIELDS | KNOWN_EXPENSE_FIELDS
    known |= {
        "nomorganisation", "neq", "admissible", "conforme", "adjudicataire",
        "montantsoumis", "montantssoumisunite", "montantcontrat",
        "montanttotalcontrat",
    }

    return {
        "root": root_tag,
        "record_tags_present": [t for t in RECORD_TAGS if any(p.endswith(t) for p in counts)],
        "paths": [
            {"path": path, "count": count, "samples": samples.get(path, [])}
            for path, count in counts.most_common()
        ],
        "unmapped_leaf_tags": sorted(leaf_tags - known),
    }
