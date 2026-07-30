"""Parse Open Contracting Data Standard releases into ContractAward records.

SEAO has published OCDS-based JSON since March 2021. This parser targets the
standard rather than SEAO specifically, so it should also serve any other OCDS
publisher added later.

One record is emitted per (award, supplier) pair. An award with three suppliers
becomes three rows: splitting here keeps supplier-level aggregation honest, at
the cost of needing `award_id` in the deduplication key so award values are not
triple-counted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from ..models import ContractAward
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
    document = _load(payload)
    return list(_iter_awards(document, source_id=source_id, content_hash=content_hash))


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

    if "releases" in document and isinstance(document["releases"], list):
        for release in document["releases"]:
            if isinstance(release, dict):
                yield release
        return

    if "records" in document and isinstance(document["records"], list):
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

    # A bare release.
    if "ocid" in document:
        yield document


def _iter_awards(document: Any, *, source_id: str, content_hash: str) -> Iterator[ContractAward]:
    for release in _iter_releases(document):
        parties = _index_parties(release)
        buyer_name, buyer_id = _buyer(release, parties)
        tender = release.get("tender") or {}

        contracts_by_award = _contracts_by_award(release)
        awards = release.get("awards") or []

        if not awards:
            # Tender-only releases carry no award; skip rather than emit a row
            # with a null supplier that would look like an award of $0.
            continue

        for award in awards:
            if not isinstance(award, dict):
                continue
            suppliers = award.get("suppliers") or [{}]
            contract = contracts_by_award.get(award.get("id"), {})
            period = award.get("contractPeriod") or contract.get("period") or {}
            value = award.get("value") or contract.get("value") or {}

            for supplier in suppliers:
                supplier = supplier if isinstance(supplier, dict) else {}
                party = parties.get(supplier.get("id", ""), {})
                address = party.get("address") or {}

                yield ContractAward(
                    source_id=source_id,
                    source_content_hash=content_hash,
                    source_format="ocds-json",
                    ocid=clean_text(release.get("ocid")),
                    notice_number=_notice_number(release, tender),
                    release_id=clean_text(release.get("id")),
                    award_id=clean_text(award.get("id")),
                    buyer_name=buyer_name,
                    buyer_id=buyer_id,
                    buyer_category=None,
                    title=clean_text(award.get("title") or tender.get("title")),
                    description=clean_text(award.get("description") or tender.get("description")),
                    procurement_method=clean_text(tender.get("procurementMethod")),
                    procurement_category=clean_text(
                        tender.get("mainProcurementCategory") or tender.get("procurementCategory")
                    ),
                    unspsc_code=_classification(tender),
                    publication_date=parse_date(release.get("date")),
                    award_date=parse_date(award.get("date")),
                    contract_start=parse_date(period.get("startDate")),
                    contract_end=parse_date(period.get("endDate")),
                    amount=parse_amount(value.get("amount")),
                    currency=clean_text(value.get("currency")) or "CAD",
                    supplier_name=clean_text(supplier.get("name") or party.get("name")),
                    supplier_neq=_identifier(party, supplier),
                    supplier_city=clean_text(address.get("locality")),
                    supplier_region=clean_text(address.get("region")),
                    number_of_bidders=parse_int(tender.get("numberOfTenderers")),
                )


def _index_parties(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        party["id"]: party
        for party in release.get("parties") or []
        if isinstance(party, dict) and party.get("id")
    }


def _buyer(release: dict[str, Any], parties: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    buyer = release.get("buyer")
    if isinstance(buyer, dict) and (buyer.get("name") or buyer.get("id")):
        name = clean_text(buyer.get("name"))
        buyer_id = clean_text(buyer.get("id"))
        if not name and buyer_id and buyer_id in parties:
            name = clean_text(parties[buyer_id].get("name"))
        return name, buyer_id

    # Fall back to the parties list when `buyer` is absent.
    for party in parties.values():
        roles = {str(r) for r in party.get("roles") or []}
        if roles & BUYER_ROLES:
            return clean_text(party.get("name")), clean_text(party.get("id"))
    return None, None


def _contracts_by_award(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for contract in release.get("contracts") or []:
        if isinstance(contract, dict) and contract.get("awardID"):
            mapping[contract["awardID"]] = contract
    return mapping


def _notice_number(release: dict[str, Any], tender: dict[str, Any]) -> str | None:
    """SEAO's notice number, which survives the 2021 schema change.

    Not a standard OCDS field, so check the places publishers put it before
    falling back to the tail of the OCID.
    """
    for candidate in (tender.get("id"), release.get("id")):
        text = clean_text(candidate)
        if text:
            return text
    ocid = clean_text(release.get("ocid"))
    return ocid.rsplit("-", 1)[-1] if ocid and "-" in ocid else ocid


def _identifier(party: dict[str, Any], supplier: dict[str, Any]) -> str | None:
    """Extract a legal entity identifier, preferring Quebec's NEQ."""
    for holder in (party, supplier):
        identifier = holder.get("identifier")
        if isinstance(identifier, dict) and identifier.get("id"):
            return clean_text(identifier["id"])
    return None


def _classification(tender: dict[str, Any]) -> str | None:
    for item in tender.get("items") or []:
        if not isinstance(item, dict):
            continue
        classification = item.get("classification")
        if isinstance(classification, dict) and classification.get("id"):
            return clean_text(classification["id"])
    return None
