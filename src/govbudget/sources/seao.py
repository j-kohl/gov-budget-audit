"""SEAO ingester — Quebec public contracts, 2009 to present.

SEAO is published through Données Québec rather than scraped from seao.ca. The
files come in two eras that meet in March 2021:

    2009 -> 2021-03   yearly XML, no published schema
    2021-03 -> now    JSON based on the Open Contracting Data Standard

plus weekly (hebdo_) and monthly (mensuel_) delta drops for recent periods.

Resource URLs are never hard-coded. They are discovered through the CKAN API at
run time, because the file naming has changed over the years and a hard-coded
list would rot. Discovery is also the only way to pick up newly published
deltas.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from ..ckan import CkanClient, Package, Resource
from ..models import ContractAward
from ..parsers import ocds, seao_xml
from ..registry import get_source
from ..storage import RawFile, RawStore

log = logging.getLogger(__name__)

SOURCE_ID = "seao"

#: The OCDS era begins with the March 2021 publications.
OCDS_ERA_START = (2021, 3)

_YEAR = re.compile(r"(20\d{2})")
_MONTH = re.compile(r"20\d{2}[-_]?(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class SeaoResource:
    """A SEAO file with the metadata needed to route it to the right parser."""

    resource: Resource
    #: 'xml' or 'ocds'
    era: str
    #: 'annuel', 'mensuel' or 'hebdo'
    cadence: str
    year: int | None
    month: int | None

    @property
    def name(self) -> str:
        return self.resource.name or self.resource.url.rsplit("/", 1)[-1]

    @property
    def url(self) -> str:
        return self.resource.url

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
        query = source.raw.get("discovery_query", "SEAO")
        packages = api_client.package_search(query, rows=25)
        package = _best_match(packages)
        if package is None:
            raise LookupError(
                f"no SEAO dataset found on {api} for query {query!r}. "
                "Check the portal manually and set `dataset_id` in the registry."
            )
        # package_search results sometimes omit resources; re-read the package.
        if not package.resources:
            package = api_client.package_show(package.id or package.name)
        return package, [classify(resource) for resource in package.resources if resource.url]
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
        # Prefer datasets that actually carry XML/JSON release files.
        formats = {r.format_lower for r in package.resources}
        if formats & {"xml", "json"}:
            points += 5
        return points, len(package.resources)

    best = max(packages, key=score)
    return best if score(best)[0] > 0 else None


def classify(resource: Resource) -> SeaoResource:
    """Route a resource to an era and cadence from its name, URL and format."""
    haystack = f"{resource.name} {resource.url}".lower()
    fmt = resource.format_lower

    if "hebdo" in haystack:
        cadence = "hebdo"
    elif "mensuel" in haystack:
        cadence = "mensuel"
    else:
        cadence = "annuel"

    year_match = _YEAR.search(haystack)
    year = int(year_match.group(1)) if year_match else None
    month_match = _MONTH.search(haystack)
    month = int(month_match.group(1)) if month_match else None

    # Format is the primary signal; period only decides ambiguous cases.
    if fmt in {"json", "ocds"} or "ocds" in haystack:
        era = "ocds"
    elif fmt in {"xml"}:
        era = "xml"
    elif year is not None and (year, month or 1) >= OCDS_ERA_START:
        era = "ocds"
    else:
        era = "xml"

    return SeaoResource(resource=resource, era=era, cadence=cadence, year=year, month=month)


def select(
    resources: list[SeaoResource],
    *,
    era: str | None = None,
    cadence: str | None = None,
    year: int | None = None,
    limit: int | None = None,
) -> list[SeaoResource]:
    """Filter discovered resources, newest first."""
    selected = resources
    if era:
        selected = [r for r in selected if r.era == era]
    if cadence:
        selected = [r for r in selected if r.cadence == cadence]
    if year:
        selected = [r for r in selected if r.year == year]
    selected = sorted(selected, key=lambda r: (r.year or 0, r.month or 0), reverse=True)
    return selected[:limit] if limit else selected


def parse_raw(entry: RawFile, data: bytes, era: str) -> list[ContractAward]:
    """Parse one fetched file, transparently unpacking zip archives."""
    awards: list[ContractAward] = []
    for name, payload in _iter_members(entry.url, data):
        member_era = _era_for_member(name, era)
        try:
            if member_era == "ocds":
                awards.extend(
                    ocds.parse(payload, source_id=SOURCE_ID, content_hash=entry.content_hash)
                )
            else:
                awards.extend(
                    seao_xml.parse(payload, source_id=SOURCE_ID, content_hash=entry.content_hash)
                )
        except Exception:
            log.exception("failed to parse %s from %s", name, entry.url)
    return awards


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
) -> Iterator[tuple[SeaoResource, RawFile, list[ContractAward]]]:
    """Fetch and parse each selected resource.

    Yields per resource so the caller can write incrementally rather than
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
        awards = parse_raw(entry, data, item.era)
        log.info("%s -> %d awards", item.name, len(awards))
        yield item, entry, awards
