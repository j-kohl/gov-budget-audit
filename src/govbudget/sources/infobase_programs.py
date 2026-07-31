"""GC InfoBase programme-level spending — the drill-down layer.

The tables already ingested are flat: spending by standard object, or by vote,
or by transfer programme, each keyed only on organization. None of them cross
those dimensions, so "how much of Personnel went to *this* programme" is
unanswerable from them.

`Federal Programs - Spending by Vote` does cross them. Every row carries
organization, programme, vote and standard object together — 123,570 rows — and
that is what makes a real drill-down possible.

It is a dimensional model rather than a flat file, so four tables are needed:

    Federal Programs - Spending by Vote   facts: org x programme x vote x object
    Federal Programs                      programme names and hierarchy
    Federal Organizations                 department names
    Reference Values                      decodes standard_object_id and codes

Without the lookups every row comes out as numeric ids.

Two things to know about this source:

**`year` is a single number, not a range.** '2010' means fiscal 2010-11. Every
other InfoBase table publishes 'YYYY-YY', so it is normalized on the way in or
the same year fails to join.

**Programmes are versioned by year.** The same `program_id` can be renamed or
reorganized between years, so names are resolved per (year, id) and fall back to
the most recent name seen for that id.
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
from ..taxonomy import federal_economic

log = logging.getLogger(__name__)

SOURCE_ID = "gc_infobase"
JURISDICTION = "ca-federal"
DATASET_ID = "a35cf382-690c-4221-a971-cf0fd189a46f"

#: Voted spending only — the file is literally programs_voted_spending.csv.
#: Statutory spending lives in its own table and is more than half the total:
#: for 2023-24 the voted table alone is $214.7B against $474.9B of actual
#: expenditure. Ingesting one without the other understates by 55%.
FACTS_TABLE = "Federal Programs - Spending by Vote"
STATUTORY_TABLE = "Federal Programs - Statutory Spending"
PROGRAMS_TABLE = "Federal Programs"
ORGANIZATIONS_TABLE = "Federal Organizations"
REFERENCE_TABLE = "Reference Values"

#: Vote type ids. 1 is voted, 2 statutory, per the InfoBase reference values.
VOTE_TYPE = {"1": "voted", "2": "statutory"}


@dataclass
class Lookups:
    """Decoded dimension tables."""

    standard_object: dict[str, str]
    programme: dict[str, str]
    organization: dict[str, str]

    def program_name(self, program_id: str, program_code: str) -> str | None:
        return self.programme.get(program_id) or (program_code or None)


def fiscal_year(year: str | None) -> str:
    """'2010' -> '2010-11', matching every other InfoBase table."""
    text = (year or "").strip()
    if not text.isdigit() or len(text) != 4:
        return text
    return f"{text}-{str(int(text) + 1)[-2:]}"


def _rows(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter))


def _amount(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _resource(package: Package, name: str) -> Resource | None:
    wanted = name.strip().lower()
    for resource in package.resources:
        if resource.format_lower == "csv" and (resource.name or "").strip().lower() == wanted:
            return resource
    return None


def build_lookups(store: RawStore, package: Package, *, force: bool = False) -> Lookups:
    """Fetch and decode the three dimension tables."""
    standard_object: dict[str, str] = {}
    programme: dict[str, str] = {}
    organization: dict[str, str] = {}

    reference = _resource(package, REFERENCE_TABLE)
    if reference is None:
        raise LookupError(f"{REFERENCE_TABLE} not found; ids cannot be decoded")
    for row in _rows(store.read(store.fetch(reference.url, SOURCE_ID, force=force))):
        if (row.get("type") or "").strip() == "standard_object":
            standard_object[(row.get("id") or "").strip()] = (row.get("name_en") or "").strip()

    programs = _resource(package, PROGRAMS_TABLE)
    if programs is not None:
        # Later years overwrite earlier ones, so a renamed programme resolves to
        # its most recent name rather than its first.
        for row in sorted(
            _rows(store.read(store.fetch(programs.url, SOURCE_ID, force=force))),
            key=lambda r: (r.get("year") or ""),
        ):
            name = (row.get("name_en") or "").strip()
            if name:
                programme[(row.get("id") or "").strip()] = name

    orgs = _resource(package, ORGANIZATIONS_TABLE)
    if orgs is not None:
        for row in sorted(
            _rows(store.read(store.fetch(orgs.url, SOURCE_ID, force=force))),
            key=lambda r: (r.get("year") or ""),
        ):
            name = (row.get("applied_title_en") or row.get("legal_title_en") or "").strip()
            if name:
                organization[(row.get("id") or "").strip()] = name

    log.info(
        "lookups: %d standard objects, %d programmes, %d organizations",
        len(standard_object), len(programme), len(organization),
    )
    if not standard_object:
        raise LookupError("Reference Values carried no standard_object rows")
    return Lookups(standard_object, programme, organization)


def parse_facts(
    data: bytes, content_hash: str, lookups: Lookups, *, statutory: bool = False
) -> list[BudgetLine]:
    """Parse a programme x standard object fact table.

    The voted and statutory tables share a shape; the statutory one carries
    `statutory_code_id` where the voted one carries `vote_number` and
    `vote_type_id`.
    """
    rows = _rows(data)
    if not rows:
        return []
    required = {"program_id", "standard_object_id", "expenditure"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(
            f"{FACTS_TABLE} is missing {sorted(missing)}; columns: {sorted(rows[0])}"
        )

    lines: list[BudgetLine] = []
    for row in rows:
        amount = _amount(row.get("expenditure"))
        if amount is None or amount == 0:
            continue

        program_id = (row.get("program_id") or "").strip()
        program_code = (row.get("program_code") or "").strip()
        org_id = (row.get("organization_id") or "").strip()
        sobj_id = (row.get("standard_object_id") or "").strip()
        sobj = lookups.standard_object.get(sobj_id, "")
        vote_number = (row.get("vote_number") or "").strip()

        detail = {"table": "programs_statutory" if statutory else "programs_by_vote"}
        if vote_number:
            detail["vote_number"] = vote_number
        if (row.get("statutory_code_id") or "").strip():
            detail["statutory_code"] = row["statutory_code_id"].strip()
        if program_code:
            detail["program_code"] = program_code
        if (row.get("dept_code") or "").strip():
            detail["dept_code"] = row["dept_code"].strip()
        if (row.get("program_type") or "").strip():
            detail["program_type"] = row["program_type"].strip()

        lines.append(
            BudgetLine(
                source_id=SOURCE_ID,
                source_content_hash=content_hash,
                jurisdiction=JURISDICTION,
                fiscal_year=fiscal_year(row.get("year")),
                organization=lookups.organization.get(org_id, org_id or "unknown"),
                organization_id=org_id or None,
                programme=lookups.program_name(program_id, program_code),
                programme_id=program_id or None,
                measure="expenditures",
                amount=amount,
                economic_category=federal_economic(sobj),
                economic_source_label=sobj or None,
                appropriation=(
                    "statutory" if statutory
                    else VOTE_TYPE.get((row.get("vote_type_id") or "").strip())
                ),
                dimensions=detail,
            )
        )
    return lines


def discover(*, client: CkanClient | None = None) -> Package:
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


def ingest(
    *,
    store: RawStore,
    client: CkanClient | None = None,
    force: bool = False,
) -> Iterator[tuple[str, RawFile, list[BudgetLine]]]:
    """Fetch the dimension tables, then the facts."""
    package = discover(client=client)
    lookups = build_lookups(store, package, force=force)

    for name, is_statutory in ((FACTS_TABLE, False), (STATUTORY_TABLE, True)):
        facts = _resource(package, name)
        if facts is None:
            raise LookupError(
                f"{name} not found on the portal. Both the voted and statutory "
                "tables are required — either alone understates federal spending "
                "by more than half."
            )
        entry = store.fetch(facts.url, SOURCE_ID, force=force)
        lines = parse_facts(
            store.read(entry), entry.content_hash, lookups, statutory=is_statutory
        )
        log.info("%s -> %d budget lines", name, len(lines))
        yield name, entry, lines
