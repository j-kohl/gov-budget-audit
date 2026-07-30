"""Comptes publics du Québec — audited actual expenditure.

The counterpart to the Budget de dépenses. That source publishes *credits*
(what the Assemblée authorized); this one publishes what was actually spent, so
together they give Quebec the same plan-versus-actual pair GC InfoBase provides
federally — and let the dashboard compare federal actuals against Quebec
actuals rather than against a plan.

Published as CSV on Données Québec under "Comptes publics du gouvernement –
Volume 2", not PDF-only. Two tables per year are ingested:

    Dépenses et investissements du fonds général      actual spending by programme
    Dépenses de transfert ... par bénéficiaires       transfers by recipient type

Four things differ from the Budget de dépenses and would silently corrupt the
figures if carried over unchanged:

* **Semicolon-delimited**, where the Budget de dépenses uses commas.
* **Amounts are space-grouped** — `3 368 000`, sometimes with a non-breaking
  space.
* **Supercatégories carry no leading code.** 'Transferts' here, '5 Transfert'
  there, and the plural differs. A code-keyed lookup sends every row to
  'other' — silently, because that is a valid category.
* **Appropriation wording differs**: 'Annuels' here, 'Votés' there. A third
  value, 'Ne nécessitant pas de crédits', is neither and stays unmapped.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import re
from collections.abc import Iterator

from ..ckan import CkanClient, Package, Resource
from ..models import BudgetLine
from ..parsers._util import parse_amount
from ..registry import get_source
from ..storage import RawFile, RawStore
from ..taxonomy import quebec_appropriation, quebec_economic

log = logging.getLogger(__name__)

SOURCE_ID = "qc_comptes_publics"
JURISDICTION = "qc"

MAIN_PREFIX = "dépenses et investissements du fonds général"
BENEFICIARY_PREFIX = "dépenses de transfert du fonds général par bénéficiaires"

#: 'Dépenses' is expenditure, 'Investissements' capital outlay. Kept as separate
#: measures: they are different flows and the source reports them apart.
REPARTITION_MEASURE = {
    "dépenses": "expenditures",
    "depenses": "expenditures",
    "investissements": "investment",
}

_YEAR = re.compile(r"(20\d{2})\s*[-–]\s*(20\d{2}|\d{2})")

#: Column names differ between publication years. 2024-25 writes
#: 'Portefeuille'; earlier files write 'Nom_portefeuille' and add
#: 'Numero_portefeuille', drop 'Type_de_credits', and use commas rather than
#: semicolons. Resolved by canonical name so a rename does not silently yield
#: an empty parse.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "portefeuille": ("Portefeuille", "Nom_portefeuille"),
    "portefeuille_id": ("Numero_portefeuille",),
    "programme_id": ("Programme",),
    "programme": ("Nom_programme",),
    "element_id": ("Element",),
    "element": ("Nom_element",),
    "repartition": ("Repartition",),
    "supercategorie": ("Supercategorie", "Super_categorie"),
    "type_de_credits": ("Type_de_credits",),
    "montant": ("Montant",),
    "beneficiaire": ("Beneficiaire",),
}


def _resolve(columns: list[str]) -> dict[str, str]:
    """Map canonical field names onto whatever this file actually calls them."""
    present = {c.strip().lower(): c for c in columns}
    resolved: dict[str, str] = {}
    for key, candidates in _COLUMN_ALIASES.items():
        for candidate in candidates:
            if candidate.lower() in present:
                resolved[key] = present[candidate.lower()]
                break
    return resolved


def fiscal_year_from_name(name: str) -> str | None:
    """Recover 'YYYY-YY' from a resource title like '... 2024-2025'.

    Normalized to the two-digit end form so it matches GC InfoBase's `fy_ef`
    and the Budget de dépenses years already staged.
    """
    match = _YEAR.search(name or "")
    if not match:
        return None
    start, end = match.groups()
    return f"{start}-{end[-2:]}"


def discover(*, client: CkanClient | None = None) -> Package:
    source = get_source(SOURCE_ID)
    api = source.ckan_api
    if not api:
        raise ValueError(f"registry entry {SOURCE_ID} has no ckan_api")
    owns = client is None
    api_client = client or CkanClient(api)
    try:
        query = source.raw.get("discovery_query", "comptes publics du gouvernement")
        for package in api_client.package_search(query, rows=10):
            if package.title.strip().lower().endswith("volume 2"):
                return package
        raise LookupError(f"Comptes publics Volume 2 not found on {api}")
    finally:
        if owns:
            api_client.close()


def _matching(package: Package, prefix: str) -> list[Resource]:
    # Resource titles occasionally carry a stray leading space on this portal.
    return sorted(
        (r for r in package.resources
         if r.format_lower == "csv" and (r.name or "").strip().lower().startswith(prefix)),
        key=lambda r: (r.name or "").strip(),
        reverse=True,
    )


def _decode(data: bytes) -> str:
    """Decode a CSV, honouring the byte-order mark.

    The 2024-25 files are UTF-8; the earlier ones are UTF-16 with a BOM. Decoded
    as UTF-8 they come back with a null byte between every character, so the
    column names match nothing and the parse yields zero rows without error.
    """
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _rows(data: bytes) -> list[dict[str, str]]:
    text = _decode(data)
    sample = text[:8000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    # newline="" is required: without it StringIO translates line endings and
    # csv breaks on fields that legitimately contain a newline, which the
    # Comptes publics beneficiary table does.
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter))


def parse_expenditures(data: bytes, content_hash: str, fiscal_year: str) -> list[BudgetLine]:
    """Parse the general fund actual spending table."""
    rows = _rows(data)
    if not rows:
        return []
    columns = _resolve(list(rows[0].keys()))
    missing = {"portefeuille", "montant"} - set(columns)
    if missing:
        # Loud, not silent: a renamed column would otherwise produce zero rows
        # from a file that parsed perfectly well.
        raise ValueError(
            f"Comptes publics expenditure table is missing {sorted(missing)}; "
            f"columns present: {sorted(rows[0].keys())}"
        )

    def field(row: dict[str, str], key: str) -> str:
        return (row.get(columns.get(key, ""), "") or "").strip()

    lines: list[BudgetLine] = []
    for row in rows:
        portefeuille = field(row, "portefeuille")
        amount = parse_amount(field(row, "montant"))
        if not portefeuille or amount is None or amount == 0:
            continue

        repartition = field(row, "repartition")
        measure = REPARTITION_MEASURE.get(repartition.lower())
        if measure is None:
            log.debug("unknown Repartition %r; treating as expenditure", repartition)
            measure = "expenditures"

        supercategorie = field(row, "supercategorie")
        detail = {"table": "comptes_publics", "repartition": repartition}
        if field(row, "element"):
            detail["element"] = field(row, "element")
        if field(row, "element_id"):
            detail["element_code"] = field(row, "element_id")
        if supercategorie:
            detail["supercategorie"] = supercategorie
        credits_type = field(row, "type_de_credits")
        if credits_type:
            detail["type_de_credits"] = credits_type

        lines.append(
            BudgetLine(
                source_id=SOURCE_ID,
                source_content_hash=content_hash,
                jurisdiction=JURISDICTION,
                fiscal_year=fiscal_year,
                organization=portefeuille,
                programme=field(row, "programme") or None,
                programme_id=field(row, "programme_id") or None,
                measure=measure,
                amount=amount,
                economic_category=quebec_economic(supercategorie),
                economic_source_label=supercategorie or None,
                appropriation=quebec_appropriation(credits_type),
                dimensions=detail,
            )
        )
    return lines


def parse_beneficiaries(data: bytes, content_hash: str, fiscal_year: str) -> list[BudgetLine]:
    """Parse transfer spending by recipient type.

    The Quebec analogue of the federal transfer payments table: it says who
    received the transfers, not merely that they were transfers. Overlaps the
    expenditure table's Transferts rows — the same money cut a different way —
    so the two must not be summed.
    """
    rows = _rows(data)
    if not rows:
        return []
    columns = _resolve(list(rows[0].keys()))

    def field(row: dict[str, str], key: str) -> str:
        return (row.get(columns.get(key, ""), "") or "").strip()

    lines: list[BudgetLine] = []
    for row in rows:
        portefeuille = field(row, "portefeuille")
        beneficiary = field(row, "beneficiaire")
        amount = parse_amount(field(row, "montant"))
        if not portefeuille or amount is None or amount == 0:
            continue

        lines.append(
            BudgetLine(
                source_id=SOURCE_ID,
                source_content_hash=content_hash,
                jurisdiction=JURISDICTION,
                fiscal_year=fiscal_year,
                organization=portefeuille,
                programme=beneficiary or None,
                measure="expenditures",
                amount=amount,
                economic_category="transfers",
                economic_source_label="Transferts",
                dimensions={"table": "beneficiaires", "beneficiaire": beneficiary},
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
    """Fetch and parse each published year of both Comptes publics tables."""
    package = discover(client=client)
    jobs: list[tuple[Resource, str]] = []
    for resource in _matching(package, MAIN_PREFIX):
        jobs.append((resource, "expenditures"))
    for resource in _matching(package, BENEFICIARY_PREFIX):
        jobs.append((resource, "beneficiaries"))

    if years:
        keep = sorted(
            {fiscal_year_from_name(r.name or "") for r, _ in jobs} - {None}, reverse=True
        )[:years]
        jobs = [j for j in jobs if fiscal_year_from_name(j[0].name or "") in keep]

    if not jobs:
        log.warning("no Comptes publics CSV resources found in %s", package.name)

    for resource, kind in jobs:
        fiscal_year = fiscal_year_from_name(resource.name or "")
        if not fiscal_year:
            log.warning("no fiscal year in resource title %r; skipping", resource.name)
            continue

        log.info("fetching %s", (resource.name or "").strip())
        try:
            entry = store.fetch(resource.url, SOURCE_ID, force=force)
        except Exception:
            log.exception("fetch failed for %s", resource.url)
            continue

        data = store.read(entry)
        parser = parse_expenditures if kind == "expenditures" else parse_beneficiaries
        lines = parser(data, entry.content_hash, fiscal_year)
        log.info("%s -> %d budget lines", (resource.name or "").strip(), len(lines))
        yield resource, entry, lines
