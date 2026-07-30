"""Budget de dépenses ingester — Quebec expenditure credits by programme.

Published as CSV on Données Québec, not only as the Conseil du trésor PDF
volumes. Verified against the live dataset: six years of "Budgets et crédits des
ministères et organismes", 2021-22 through 2026-27.

The CSV carries Quebec's full expenditure taxonomy — PORTEFEUILLE, PROGRAMME,
ELEMENT, SUPERCATEGORIE, TYPE_DE_CREDITS — which is what makes the crosswalk in
`govbudget.taxonomy` possible without any PDF extraction.

Two quirks that a naive reader gets wrong:

**Column names embed the fiscal year and drift between years.** 2026-27 has
`BUDGET_DE_DEPENSES_26_27` and `SUPERCATEGORIE`; 2021-22 has
`DEPENSES_SANS_CREDIT_21_22` (singular) and `SUPER_CATEGORIE` (underscored).
Columns are therefore resolved by normalized prefix, and the fiscal year is read
back out of the suffix — which conveniently yields the same `YYYY-YY` shape GC
InfoBase uses, so the two join without a mapping.

**The money columns are components of a total, not alternatives.** Verified on
2026-27: `BUDGET_DE_DEPENSES + DEPENSES_SANS_CREDITS + BUDGET_INVESTISSEMENT`
equals `CREDITS_TOTAUX` on every one of 904 rows. They are emitted as separate
measures so nothing is lost, and summing across measures would double-count.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Iterator

from ..ckan import CkanClient, Package, Resource
from ..models import BudgetLine
from ..registry import get_source
from ..storage import RawFile, RawStore
from ..taxonomy import quebec_appropriation, quebec_economic

log = logging.getLogger(__name__)

SOURCE_ID = "qc_budget_depenses"
JURISDICTION = "qc"

#: Resource titles that carry the main ministry credits table.
MAIN_TABLE_PREFIX = "budgets et crédits"

#: Money columns, by normalized prefix -> measure name. `authorities` is the
#: total; the others are its components. Never sum across them.
MEASURE_COLUMNS = {
    "CREDITSTOTAUX": "authorities",
    "BUDGETDEDEPENSES": "expenditure_budget",
    "BUDGETINVESTISSEMENT": "investment_budget",
    "DEPENSESSANSCREDIT": "spending_without_credits",
}

#: Dimension columns, by normalized name.
DIMENSION_COLUMNS = {
    "PORTEFEUILLE": "portefeuille",
    "PROGRAMME": "programme",
    "ELEMENT": "element",
    "SUPERCATEGORIE": "supercategorie",
    "TYPEDECREDITS": "type_de_credits",
}

#: 'PROGRAMME' values are '0050.01 Soutien aux activités', 'ELEMENT' adds a
#: third segment. The leading code is the stable identifier.
_CODE = re.compile(r"^([\d.]+)\s+(.*)$")
_YEAR_SUFFIX = re.compile(r"_(\d{2})_(\d{2})$")


#: Spellings that differ between years, mapped to one canonical stem.
#: An explicit table rather than a suffix rule: a blanket "strip a trailing S
#: after CREDITS" also turns TYPE_DE_CREDITS into TYPEDECREDIT, which silently
#: loses the voted/statutory dimension.
_COLUMN_ALIASES = {
    "DEPENSESSANSCREDITS": "DEPENSESSANSCREDIT",
    "SUPERCATEGORIES": "SUPERCATEGORIE",
}


def _normalize(column: str) -> str:
    """Strip the year suffix and underscores, then resolve spelling drift."""
    stem = _YEAR_SUFFIX.sub("", column.strip().upper()).replace("_", "")
    return _COLUMN_ALIASES.get(stem, stem)


def fiscal_year_from_columns(columns: list[str]) -> str | None:
    """Recover 'YYYY-YY' from a column suffix like '_26_27'.

    The file name is not reliable across years; the column suffix is, and it
    yields the same shape GC InfoBase publishes so the two tables align.
    """
    for column in columns:
        match = _YEAR_SUFFIX.search(column.strip())
        if match:
            start, end = match.groups()
            century = "20" if int(start) < 90 else "19"
            return f"{century}{start}-{end}"
    return None


def _split_code(value: str | None) -> tuple[str | None, str | None]:
    """Split '0050.01 Soutien aux activités' into its code and its label."""
    text = (value or "").strip()
    if not text:
        return None, None
    match = _CODE.match(text)
    return (match.group(1), match.group(2).strip()) if match else (None, text)


def _amount(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).replace(",", ".").replace(" ", "").replace("\xa0", ""))
    except ValueError:
        return None


def discover(*, client: CkanClient | None = None) -> Package:
    source = get_source(SOURCE_ID)
    api = source.ckan_api
    if not api:
        raise ValueError(f"registry entry {SOURCE_ID} has no ckan_api")
    owns = client is None
    api_client = client or CkanClient(api)
    try:
        query = source.raw.get("discovery_query", "budget de dépenses")
        for package in api_client.package_search(query, rows=10):
            if package.title.strip().lower() == "budget de dépenses":
                return package
        raise LookupError(
            f"Budget de dépenses dataset not found on {api} for query {query!r}"
        )
    finally:
        if owns:
            api_client.close()


def main_tables(package: Package) -> list[Resource]:
    """The per-year ministry credits tables, newest first."""
    matches = [
        r for r in package.resources
        if r.format_lower == "csv"
        and (r.name or "").strip().lower().startswith(MAIN_TABLE_PREFIX)
    ]
    return sorted(matches, key=lambda r: r.name or "", reverse=True)


def parse(data: bytes, content_hash: str) -> list[BudgetLine]:
    """Parse one year's credits table into budget lines."""
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:8000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    columns = list(reader.fieldnames or [])
    if not columns:
        return []

    fiscal_year = fiscal_year_from_columns(columns)
    if fiscal_year is None:
        log.warning("no fiscal year suffix found in columns: %s", columns[:6])
        return []

    resolved = {_normalize(c): c for c in columns}
    dimensions = {
        key: resolved[norm] for norm, key in DIMENSION_COLUMNS.items() if norm in resolved
    }
    measures = {
        resolved[norm]: measure for norm, measure in MEASURE_COLUMNS.items() if norm in resolved
    }
    if not measures:
        log.warning("no recognisable money columns in %s", columns)
        return []

    lines: list[BudgetLine] = []
    for row in reader:
        portefeuille = (row.get(dimensions.get("portefeuille", ""), "") or "").strip()
        if not portefeuille:
            continue

        programme_code, programme_label = _split_code(row.get(dimensions.get("programme", "")))
        element_code, element_label = _split_code(row.get(dimensions.get("element", "")))
        supercategorie = (row.get(dimensions.get("supercategorie", ""), "") or "").strip()
        credits_type = (row.get(dimensions.get("type_de_credits", ""), "") or "").strip()

        detail = {"table": "budgets_et_credits"}
        if element_code:
            detail["element_code"] = element_code
        if element_label:
            detail["element"] = element_label
        if supercategorie:
            detail["supercategorie"] = supercategorie
        if credits_type:
            detail["type_de_credits"] = credits_type

        for column, measure in measures.items():
            amount = _amount(row.get(column))
            # Zero rows are real here — a programme can carry a nil credit for a
            # category — but they add nothing to any total, so they are dropped.
            if amount is None or amount == 0:
                continue
            lines.append(
                BudgetLine(
                    source_id=SOURCE_ID,
                    source_content_hash=content_hash,
                    jurisdiction=JURISDICTION,
                    fiscal_year=fiscal_year,
                    organization=portefeuille,
                    programme=programme_label,
                    programme_id=programme_code,
                    measure=measure,
                    amount=amount,
                    economic_category=quebec_economic(supercategorie),
                    economic_source_label=supercategorie or None,
                    appropriation=quebec_appropriation(credits_type),
                    dimensions=detail,
                )
            )
    return lines


def ingest(
    *,
    store: RawStore,
    client: CkanClient | None = None,
    years: int | None = None,
    force: bool = False,
) -> Iterator[tuple[Resource, RawFile, list[BudgetLine]]]:
    """Fetch and parse each published year of the credits table."""
    package = discover(client=client)
    resources = main_tables(package)
    if years:
        resources = resources[:years]
    if not resources:
        log.warning("no 'Budgets et crédits' resources found in %s", package.name)

    for resource in resources:
        log.info("fetching %s", resource.name)
        try:
            entry = store.fetch(resource.url, SOURCE_ID, force=force)
        except Exception:
            log.exception("fetch failed for %s", resource.url)
            continue

        lines = parse(store.read(entry), entry.content_hash)
        log.info("%s -> %d budget lines", resource.name, len(lines))
        yield resource, entry, lines
