"""SEAO ingester — Quebec public contracts, 2009 to present.

SEAO is published through Données Québec rather than scraped from seao.ca.
Verified against the live dataset (418 resources as of July 2026), which is laid
out as:

    Année 2009 … Année 2020       yearly XML archives (.zip)
    Janvier 2021 … Mai 2024       monthly XML archives, named in French (.zip)
    mensuel_YYYYMMDD_YYYYMMDD     monthly OCDS JSON, from June 2021
    hebdo_YYYYMMDD_YYYYMMDD       weekly OCDS JSON
    3 PDFs                        the format specifications and an FAQ

Two things this layout implies that the naming does not make obvious:

**XML and JSON overlap.** The XML archives run to May 2024 and the OCDS JSON
starts June 2021, so three years are published in both formats. Ingesting both
double-counts. `select` therefore takes one format at a time, and the overlap is
worth diffing once to validate the crosswalk rather than merging blindly.

**Each XML archive holds six files.** Notices, final contract amounts and
supplementary expenses, each with a matching Revisions file. They are three
different facts, so the ingester stages three datasets rather than one.

Resource URLs are never hard-coded — they are discovered through the CKAN API at
run time, which is also how new weekly drops are picked up.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from ..ckan import CkanClient, Package, Resource
from ..models import ContractAward, ContractExpense, ContractFinal
from ..parsers import ocds, seao_xml
from ..registry import get_source
from ..storage import RawFile, RawStore

log = logging.getLogger(__name__)

SOURCE_ID = "seao"

#: Known dataset id on Données Québec. Used directly when present, with search
#: as the fallback so a re-slug does not break the ingester.
DATASET_ID = "d23b2e02-085d-43e5-9e6e-e1d558ebfdd5"

#: Formats that are documentation rather than data.
DOC_FORMATS = frozenset({"pdf", "doc", "docx", "html", "txt"})

FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

_YEAR = re.compile(r"(20\d{2})")
#: Matches the YYYYMMDD in hebdo_/mensuel_ filenames.
_YYYYMMDD = re.compile(r"(20\d{2})(0[1-9]|1[0-2])(\d{2})")


@dataclass(frozen=True)
class SeaoResource:
    """A SEAO file with the metadata needed to route it to the right parser."""

    resource: Resource
    #: 'xml' or 'ocds'
    era: str
    #: 'annuel', 'mensuel', 'hebdo' or 'doc'
    cadence: str
    year: int | None
    month: int | None

    @property
    def name(self) -> str:
        return self.resource.name or self.resource.url.rsplit("/", 1)[-1]

    @property
    def url(self) -> str:
        return self.resource.url

    @property
    def is_data(self) -> bool:
        return self.cadence != "doc"

    def __str__(self) -> str:
        period = f"{self.year or '?'}" + (f"-{self.month:02d}" if self.month else "")
        return f"{self.name} [{self.era}/{self.cadence} {period}]"


def discover(*, client: CkanClient | None = None) -> tuple[Package, list[SeaoResource]]:
    """Locate the SEAO dataset on Données Québec and classify its resources."""
    source = get_source(SOURCE_ID)
    api = source.ckan_api
    if not api:
        raise ValueError(f"registry entry {SOURCE_ID} has no ckan_api")

    owns_client = client is None
    api_client = client or CkanClient(api)
    try:
        package = None
        dataset_id = source.raw.get("dataset_id", DATASET_ID)
        if dataset_id:
            try:
                package = api_client.package_show(dataset_id)
            except Exception:
                log.warning("dataset id %s did not resolve; falling back to search", dataset_id)

        if package is None:
            query = source.raw.get("discovery_query", "SEAO")
            package = _best_match(api_client.package_search(query, rows=25))
        if package is None:
            raise LookupError(
                f"no SEAO dataset found on {api}. Check the portal and update "
                "`dataset_id` in sources/registry.json."
            )
        if not package.resources:
            package = api_client.package_show(package.id or package.name)
        return package, [classify(r) for r in package.resources if r.url]
    finally:
        if owns_client:
            api_client.close()


def _best_match(packages: list[Package]) -> Package | None:
    """Pick the dataset most likely to be the SEAO contract release files."""
    if not packages:
        return None

    def score(package: Package) -> tuple[int, int]:
        haystack = f"{package.name} {package.title}".lower()
        points = 0
        if "seao" in haystack:
            points += 10
        if "contrat" in haystack or "appel" in haystack:
            points += 3
        if {r.format_lower for r in package.resources} & {"xml", "json"}:
            points += 5
        return points, len(package.resources)

    best = max(packages, key=score)
    return best if score(best)[0] > 0 else None


def classify(resource: Resource) -> SeaoResource:
    """Route a resource to an era and cadence from its name, URL and format."""
    name = resource.name or ""
    haystack = f"{name} {resource.url}".lower()
    fmt = resource.format_lower

    # Documentation: the format specs and the FAQ live in the same dataset.
    if fmt in DOC_FORMATS:
        return SeaoResource(resource=resource, era="doc", cadence="doc", year=None, month=None)

    year: int | None = None
    month: int | None = None

    period = _YYYYMMDD.search(haystack)
    if period:
        year, month = int(period.group(1)), int(period.group(2))
    else:
        year_match = _YEAR.search(haystack)
        year = int(year_match.group(1)) if year_match else None
        # Monthly XML archives are named in French: 'Mai 2024', 'avril_2021'.
        normalized = _strip_accents(haystack)
        for label, number in FRENCH_MONTHS.items():
            if re.search(rf"\b{label}\b", normalized):
                month = number
                break

    if "hebdo" in haystack:
        cadence = "hebdo"
    elif "mensuel" in haystack or month is not None:
        cadence = "mensuel"
    else:
        cadence = "annuel"

    # Format is the primary signal. The zip bundle of JSON files is declared as
    # 'zip', so fall back to what the name says it contains.
    if fmt in {"json", "ocds"} or "json" in haystack or "ocds" in haystack:
        era = "ocds"
    elif fmt == "xml" or "xml" in haystack:
        era = "xml"
    else:
        era = "xml"

    return SeaoResource(resource=resource, era=era, cadence=cadence, year=year, month=month)


def _strip_accents(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def parse_period(text: str | None) -> tuple[int, int] | None:
    """Parse a 'YYYY-MM' or 'YYYY' boundary into a comparable (year, month)."""
    if not text:
        return None
    parts = str(text).split("-")
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range in period {text!r}")
    return year, month


def select(
    resources: list[SeaoResource],
    *,
    era: str | None = None,
    cadence: str | None = None,
    year: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    include_docs: bool = False,
) -> list[SeaoResource]:
    """Filter discovered resources, newest first. Documentation excluded.

    `since` and `until` take 'YYYY-MM' and are inclusive. They exist to express
    the non-overlapping backfill: XML up to 2021-05, OCDS from 2021-06. A yearly
    archive is treated as month 1 for `since` and month 12 for `until`, so
    'Année 2020' falls inside `--until 2021-05` as a whole.
    """
    selected = [r for r in resources if include_docs or r.is_data]
    if era:
        selected = [r for r in selected if r.era == era]
    if cadence:
        selected = [r for r in selected if r.cadence == cadence]
    if year:
        selected = [r for r in selected if r.year == year]

    lower = parse_period(since)
    upper = parse_period(until)
    if lower:
        # A yearly archive counts as starting in January.
        selected = [r for r in selected if r.year and (r.year, r.month or 1) >= lower]
    if upper:
        # A yearly archive counts as ending in December, so it is included whole.
        selected = [r for r in selected if r.year and (r.year, r.month or 12) <= upper]

    selected = sorted(selected, key=lambda r: (r.year or 0, r.month or 0), reverse=True)
    return selected[:limit] if limit else selected


ParsedBundle = tuple[list[ContractAward], list[ContractFinal], list[ContractExpense]]


def parse_raw(entry: RawFile, data: bytes, era: str, *, include_revisions: bool = False) -> ParsedBundle:
    """Parse one fetched file, unpacking zip archives and routing each member.

    Revisions files are skipped by default. They restate records already present
    in the main files, so including them without deduplicating on publication
    date double-counts.
    """
    awards: list[ContractAward] = []
    finals: list[ContractFinal] = []
    expenses: list[ContractExpense] = []

    for name, payload in _iter_members(entry.url, data):
        if not include_revisions and "revision" in name.lower():
            log.debug("skipping revisions member %s", name)
            continue

        member_era = _era_for_member(name, era)
        try:
            if member_era == "ocds":
                a, f = ocds.parse_all(
                    payload, source_id=SOURCE_ID, content_hash=entry.content_hash
                )
                awards.extend(a)
                finals.extend(f)
            else:
                a, f, e = seao_xml.parse_all(
                    payload, source_id=SOURCE_ID, content_hash=entry.content_hash
                )
                awards.extend(a)
                finals.extend(f)
                expenses.extend(e)
        except Exception:
            log.exception("failed to parse %s from %s", name, entry.url)

    return awards, finals, expenses


def _iter_members(url: str, data: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield (name, bytes) for a file, expanding a zip archive if it is one."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    yield info.filename, archive.read(info)
                return
        except zipfile.BadZipFile:
            log.warning("%s looks like a zip but did not open; treating as raw", url)
    yield url.rsplit("/", 1)[-1], data


def _era_for_member(name: str, default: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".json"):
        return "ocds"
    if lowered.endswith(".xml"):
        return "xml"
    return default


def ingest(
    selected: list[SeaoResource],
    *,
    store: RawStore,
    force: bool = False,
    include_revisions: bool = False,
) -> Iterator[tuple[SeaoResource, RawFile, ParsedBundle]]:
    """Fetch and parse each selected resource.

    Yields per resource so the caller can stage incrementally rather than
    holding a decade of contracts in memory.
    """
    for item in selected:
        log.info("fetching %s", item)
        try:
            entry = store.fetch(item.url, SOURCE_ID, force=force)
        except Exception:
            log.exception("fetch failed for %s", item.url)
            continue

        data = store.read(entry)
        bundle = parse_raw(entry, data, item.era, include_revisions=include_revisions)
        log.info(
            "%s -> %d awards, %d finals, %d expenses",
            item.name, len(bundle[0]), len(bundle[1]), len(bundle[2]),
        )
        yield item, entry, bundle
