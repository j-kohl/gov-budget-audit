"""GC InfoBase ingester — federal authorities and expenditures.

GC InfoBase's entire backing data is published as CSVs on open.canada.ca under
dataset `a35cf382-690c-4221-a971-cf0fd189a46f`, so nothing here scrapes the
InfoBase UI. Verified against the live dataset: 48 CSV resources, published as
English/French pairs of the same 24 tables.

Three of those tables carry the spending facts this project needs:

    Public Accounts – Expenditures by Standard Object   economic dimension
    Public Accounts – Authorities and Expenditures by Vote
                                                        appropriation dimension
    Public Accounts – Authorities and Expenditures by Program
                                                        programme dimension

Each is keyed on `org_id` and `fy_ef` (the fiscal year as 'YYYY-YY'), which is
what lets them sit in one BudgetLine table without a join.

Two things worth knowing about this source:

**Authorities and expenditures are different measures.** The vote table
publishes both: what Parliament approved, and what was actually spent. The gap
is lapsed spending and it is real. They are emitted as separate rows with
`measure` set, never summed.

**InfoBase restates history.** It adjusts prior-year series for departmental
mergers and renames, so its figures are more comparable across years than raw
Public Accounts — and must not be mixed with an unadjusted series.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterator
from dataclasses import dataclass

from ..ckan import CkanClient, Package, Resource
from ..models import BudgetLine
from ..registry import get_source
from ..storage import RawFile, RawStore
from ..taxonomy import FEDERAL_APPROPRIATION, federal_economic

log = logging.getLogger(__name__)

SOURCE_ID = "gc_infobase"
JURISDICTION = "ca-federal"
DATASET_ID = "a35cf382-690c-4221-a971-cf0fd189a46f"


@dataclass(frozen=True)
class InfobaseTable:
    """One of the InfoBase CSVs this ingester knows how to read."""

    key: str
    #: Exact resource name on open.canada.ca. The French twin is skipped.
    resource_name: str
    description: str


TABLES: tuple[InfobaseTable, ...] = (
    InfobaseTable(
        "standard_object",
        "Public Accounts of Canada – Expenditures by Standard Object",
        "Expenditures by economic classification (Personnel, Transfer payments, …)",
    ),
    InfobaseTable(
        "vote",
        "Public Accounts of Canada – Authorities and Expenditures by Vote",
        "Authorities and expenditures by vote, voted and statutory",
    ),
)


def discover(*, client: CkanClient | None = None) -> Package:
    """Fetch the InfoBase dataset from open.canada.ca."""
    source = get_source(SOURCE_ID)
    api = source.ckan_api
    if not api:
        raise ValueError(f"registry entry {SOURCE_ID} has no ckan_api")

    owns = client is None
    api_client = client or CkanClient(api)
    try:
        return api_client.package_show(source.raw.get("dataset_id", DATASET_ID))
    finally:
        if owns:
            api_client.close()


def find_resource(package: Package, table: InfobaseTable) -> Resource | None:
    """Locate a table's CSV.

    Resources come in English/French pairs sharing a name, so the first CSV
    match is taken — they carry identical data with translated labels, and the
    parsers key on codes rather than labels.
    """
    wanted = table.resource_name.strip().lower()
    for resource in package.resources:
        if resource.format_lower != "csv":
            continue
        if (resource.name or "").strip().lower() == wanted:
            return resource
    # Fall back to a looser match: the portal occasionally reflows these titles.
    stem = wanted.split("–")[-1].strip()
    for resource in package.resources:
        if resource.format_lower == "csv" and stem and stem in (resource.name or "").lower():
            return resource
    return None


def _rows(data: bytes) -> list[dict[str, str]]:
    """Decode a CSV, tolerating a BOM and either delimiter."""
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def _amount(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def parse_standard_object(data: bytes, content_hash: str) -> Iterator[BudgetLine]:
    """Expenditures by standard object — the federal economic dimension."""
    for row in _rows(data):
        amount = _amount(row.get("expenditures"))
        if amount is None:
            continue
        label = (row.get("sobj_en") or row.get("sobj_fr") or "").strip()
        yield BudgetLine(
            source_id=SOURCE_ID,
            source_content_hash=content_hash,
            jurisdiction=JURISDICTION,
            fiscal_year=(row.get("fy_ef") or "").strip(),
            organization=(row.get("org_name") or "").strip(),
            organization_id=(row.get("org_id") or "").strip() or None,
            measure="expenditures",
            amount=amount,
            economic_category=federal_economic(label),
            economic_source_label=label or None,
            dimensions={"table": "standard_object"},
        )


def parse_vote(data: bytes, content_hash: str) -> Iterator[BudgetLine]:
    """Authorities and expenditures by vote.

    Emits two rows per source row. Authorities are what Parliament approved and
    expenditures are what was spent; the difference is lapsed spending, so
    collapsing them into one number would erase it.
    """
    for row in _rows(data):
        voted = (row.get("voted_or_statutory") or "").strip().lower()
        description = (row.get("description") or "").strip()
        base = dict(
            source_id=SOURCE_ID,
            source_content_hash=content_hash,
            jurisdiction=JURISDICTION,
            fiscal_year=(row.get("fy_ef") or "").strip(),
            organization=(row.get("org_name") or "").strip(),
            organization_id=(row.get("org_id") or "").strip() or None,
            programme=description or None,
            appropriation=FEDERAL_APPROPRIATION.get(voted),
            dimensions={"table": "vote", "vote": description},
        )
        for measure, column in (("authorities", "authorities"), ("expenditures", "expenditures")):
            amount = _amount(row.get(column))
            if amount is None:
                continue
            yield BudgetLine(**base, measure=measure, amount=amount)


PARSERS = {
    "standard_object": parse_standard_object,
    "vote": parse_vote,
}


def ingest(
    tables: tuple[InfobaseTable, ...] = TABLES,
    *,
    store: RawStore,
    client: CkanClient | None = None,
    force: bool = False,
) -> Iterator[tuple[InfobaseTable, RawFile, list[BudgetLine]]]:
    """Fetch and parse each known InfoBase table."""
    package = discover(client=client)
    for table in tables:
        resource = find_resource(package, table)
        if resource is None:
            log.warning("resource not found on the portal: %s", table.resource_name)
            continue

        log.info("fetching %s", table.resource_name)
        try:
            entry = store.fetch(resource.url, SOURCE_ID, force=force)
        except Exception:
            log.exception("fetch failed for %s", resource.url)
            continue

        lines = list(PARSERS[table.key](store.read(entry), entry.content_hash))
        log.info("%s -> %d budget lines", table.key, len(lines))
        yield table, entry, lines
