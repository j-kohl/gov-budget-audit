"""Parse Open Contracting Data Standard releases into contract records.

SEAO has published OCDS-based JSON since June 2021. Verified against real
monthly release packages (`mensuel_20260601_20260630.json`, 20,499 releases).

Targets the standard rather than SEAO specifically where possible, but four
things are publisher conventions worth knowing:

1. **The SEAO notice number is in the OCID**, not in `tender.id`. The OCID looks
   like `ocds-ec9k95-1740136` and `tender.id` is the buyer's own reference
   (`VM-2022-02(G)`). Using `tender.id` as the notice number breaks the join to
   the XML-era records.
2. **NEQ lives in `parties[].details.neq`**, not in the standard `identifier`
   object, so the usual OCDS extraction misses it entirely.
3. **`awards[].value` and `contracts[].value` differ** — awarded versus final
   settled amount, the same distinction the XML draws between Avis and Contrats.
   They are emitted as separate records rather than collapsed.
4. **Only winners appear.** Unlike the XML, OCDS publishes no losing bids, so
   every award row is a winner and `is_winner` is set True to keep totals
   consistent across the two eras.

Releases carry tags — `tender`, `award`, `contract`, `awardUpdate`,
`contractUpdate`, `contractTermination`, `tenderCancellation`. The same OCID
recurs as it is revised, so downstream deduplication on publication date is
required.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from ..models import ContractAward, ContractFinal
from ..seao_codes import COMPETITIVENESS, COMPETITIVENESS_LABEL, label
from ._util import clean_text, parse_amount, parse_date, parse_int

log = logging.getLogger(__name__)

#: OCDS party roles that identify the purchasing body.
BUYER_ROLES = {"buyer", "procuringEntity"}


def parse(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> list[ContractAward]:
    """Parse an OCDS package into contract awards."""
    awards, _ = parse_all(payload, source_id=source_id, content_hash=content_hash)
    return awards


def parse_all(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    source_id: str = "seao",
    content_hash: str = "",
) -> tuple[list[ContractAward], list[ContractFinal]]:
    """Parse an OCDS package into awards and final contract amounts."""
    document = _load(payload)
    awards: list[ContractAward] = []
    finals: list[ContractFinal] = []

    for release in _iter_releases(document):
        parsed_awards, parsed_finals = _release_records(release, source_id, content_hash)
        awards.extend(parsed_awards)
        finals.extend(parsed_finals)

    return awards, finals


def _load(payload: bytes | str | dict[str, Any] | list[Any]) -> Any:
    if isinstance(payload, (dict, list)):
        return payload
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    text = text.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some publishers ship newline-delimited releases rather than a package.
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("skipping unparseable JSON line (%d chars)", len(line))
        return records


def _iter_releases(document: Any) -> Iterator[dict[str, Any]]:
    """Yield releases from any of the OCDS container shapes."""
    if isinstance(document, list):
        for entry in document:
            yield from _iter_releases(entry)
        return
    if not isinstance(document, dict):
        return

    if isinstance(document.get("releases"), list):
        for release in document["releases"]:
            if isinstance(release, dict):
                yield release
        return

    if isinstance(document.get("records"), list):
        for record in document["records"]:
            if not isinstance(record, dict):
                continue
            compiled = record.get("compiledRelease")
            if isinstance(compiled, dict):
                yield compiled
            for release in record.get("releases", []) or []:
                if isinstance(release, dict):
                    yield release
        return

    if "ocid" in document:
        yield document


def _release_records(
    release: dict[str, Any], source_id: str, content_hash: str
) -> tuple[list[ContractAward], list[ContractFinal]]:
    parties = _index_parties(release)
    buyer_name, buyer_id, buyer_party = _buyer(release, parties)
    tender = release.get("tender") or {}
    ocid = clean_text(release.get("ocid"))
    notice_number = _notice_number(ocid)
    buyer_address = (buyer_party or {}).get("address") or {}
    tags = [str(t) for t in release.get("tag") or []]

    contracts_by_award = _contracts_by_award(release)

    base: dict[str, Any] = {
        "source_id": source_id,
        "source_content_hash": content_hash,
        "source_format": "ocds-json",
        "ocid": ocid,
        "notice_number": notice_number,
        "buyer_reference": clean_text(tender.get("id")),
        "release_id": clean_text(release.get("id")),
        "buyer_name": buyer_name,
        "buyer_id": buyer_id,
        "buyer_city": clean_text(buyer_address.get("locality")),
        "buyer_region": clean_text(buyer_address.get("region")),
        "is_municipal": _detail_flag(buyer_party, "municipal"),
        # Keep the standard vocabulary in the code field so it harmonizes with
        # the XML <type> codes; the human label goes in procurement_method.
        "notice_type_code": clean_text(tender.get("procurementMethod")),
        "notice_type_label": clean_text(tender.get("procurementMethodDetails")),
        "competitiveness": label(COMPETITIVENESS, clean_text(tender.get("procurementMethod"))),
        "competitiveness_label": label(
            COMPETITIVENESS_LABEL,
            label(COMPETITIVENESS, clean_text(tender.get("procurementMethod"))),
        ),
        "procurement_method": clean_text(
            tender.get("procurementMethodDetails") or tender.get("procurementMethod")
        ),
        "procurement_category": clean_text(
            tender.get("mainProcurementCategory") or tender.get("procurementCategory")
        ),
        "seao_category": _item_description(tender),
        "unspsc_code": _classification(tender),
        "publication_date": parse_date(release.get("date")),
        "closing_date": parse_date((tender.get("tenderPeriod") or {}).get("endDate")),
        "number_of_bidders": parse_int(tender.get("numberOfTenderers")),
        "currency": "CAD",
        # OCDS publishes winners only; the XML era publishes every bidder. Set
        # this so a single is_winner filter works across both.
        "is_winner": True,
        "unmapped": {"tags": tags} if tags else {},
    }

    awards: list[ContractAward] = []
    finals: list[ContractFinal] = []

    for award in release.get("awards") or []:
        if not isinstance(award, dict):
            continue
        contract = contracts_by_award.get(award.get("id"), {})
        period = award.get("contractPeriod") or contract.get("period") or {}
        value = award.get("value") or {}

        for supplier in award.get("suppliers") or [{}]:
            supplier = supplier if isinstance(supplier, dict) else {}
            party = parties.get(supplier.get("id", ""), {})
            address = party.get("address") or {}

            awards.append(
                ContractAward(
                    **base,
                    award_id=clean_text(award.get("id")),
                    title=clean_text(award.get("title") or tender.get("title")),
                    description=clean_text(award.get("description") or tender.get("description")),
                    award_date=parse_date(award.get("date")),
                    contract_start=parse_date(period.get("startDate")),
                    contract_end=parse_date(period.get("endDate")),
                    amount=parse_amount(value.get("amount")),
                    supplier_name=clean_text(supplier.get("name") or party.get("name")),
                    supplier_neq=_neq(party, supplier),
                    supplier_city=clean_text(address.get("locality")),
                    supplier_region=clean_text(address.get("region")),
                    supplier_country=clean_text(address.get("countryName")),
                    supplier_postal_code=clean_text(address.get("postalCode")),
                )
            )

    # `contracts[].value` is the settled amount, distinct from the award value.
    for contract in release.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        value = contract.get("value") or {}
        if parse_amount(value.get("amount")) is None:
            continue
        supplier_name, supplier_neq = _contract_supplier(
            release, contract.get("awardID"), parties
        )
        period = contract.get("period") or {}
        finals.append(
            ContractFinal(
                source_id=source_id,
                source_content_hash=content_hash,
                source_format="ocds-json",
                notice_number=notice_number,
                buyer_reference=clean_text(tender.get("id")),
                final_date=parse_date(contract.get("dateSigned") or period.get("endDate")),
                final_publication_date=parse_date(release.get("date")),
                final_amount=parse_amount(value.get("amount")),
                supplier_name=supplier_name,
                supplier_neq=supplier_neq,
                unmapped={"status": clean_text(contract.get("status"))} if contract.get("status") else {},
            )
        )

    return awards, finals


def _index_parties(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        party["id"]: party
        for party in release.get("parties") or []
        if isinstance(party, dict) and party.get("id")
    }


def _buyer(
    release: dict[str, Any], parties: dict[str, dict[str, Any]]
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    buyer = release.get("buyer")
    if isinstance(buyer, dict) and (buyer.get("name") or buyer.get("id")):
        buyer_id = clean_text(buyer.get("id"))
        party = parties.get(buyer_id or "", {})
        name = clean_text(buyer.get("name")) or clean_text(party.get("name"))
        return name, buyer_id, party or None

    for party in parties.values():
        if {str(r) for r in party.get("roles") or []} & BUYER_ROLES:
            return clean_text(party.get("name")), clean_text(party.get("id")), party
    return None, None, None


def _contracts_by_award(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for contract in release.get("contracts") or []:
        if isinstance(contract, dict) and contract.get("awardID"):
            mapping[contract["awardID"]] = contract
    return mapping


def _contract_supplier(
    release: dict[str, Any], award_id: Any, parties: dict[str, dict[str, Any]]
) -> tuple[str | None, str | None]:
    """Resolve a contract's supplier through its award."""
    for award in release.get("awards") or []:
        if not isinstance(award, dict) or award.get("id") != award_id:
            continue
        for supplier in award.get("suppliers") or []:
            if isinstance(supplier, dict):
                party = parties.get(supplier.get("id", ""), {})
                return clean_text(supplier.get("name") or party.get("name")), _neq(party, supplier)
    return None, None


def _notice_number(ocid: str | None) -> str | None:
    """Extract the SEAO notice number from the OCID.

    SEAO OCIDs are `ocds-<prefix>-<numeroseao>`, so the trailing segment is the
    join key to the XML-era records. `tender.id` is the buyer's own reference
    and is not comparable across organizations.
    """
    if not ocid:
        return None
    tail = ocid.rsplit("-", 1)[-1]
    return tail if tail != ocid else ocid


def _neq(party: dict[str, Any], supplier: dict[str, Any]) -> str | None:
    """Extract the Quebec business number.

    SEAO puts it in `details.neq` rather than the standard `identifier` object,
    so both are checked.
    """
    for holder in (party, supplier):
        details = holder.get("details")
        if isinstance(details, dict) and details.get("neq"):
            return clean_text(details["neq"])
        identifier = holder.get("identifier")
        if isinstance(identifier, dict) and identifier.get("id"):
            return clean_text(identifier["id"])
    return None


def _detail_flag(party: dict[str, Any] | None, key: str) -> bool | None:
    if not party:
        return None
    details = party.get("details")
    if not isinstance(details, dict):
        return None
    value = clean_text(details.get(key))
    if value in {"0", "1"}:
        return value == "1"
    return None


def _classification(tender: dict[str, Any]) -> str | None:
    for item in tender.get("items") or []:
        if isinstance(item, dict):
            classification = item.get("classification")
            if isinstance(classification, dict) and classification.get("id"):
                return clean_text(classification["id"])
    return None


def _item_description(tender: dict[str, Any]) -> str | None:
    """SEAO's own category, carried as the item description (e.g. 'S8 - …')."""
    for item in tender.get("items") or []:
        if isinstance(item, dict) and item.get("description"):
            return clean_text(item["description"])
    return None
