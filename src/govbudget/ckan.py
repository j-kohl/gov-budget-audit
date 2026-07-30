"""Minimal CKAN Action API client.

Covers both open.canada.ca and donneesquebec.ca, which run the same CKAN
software and therefore the same API shape.

Deliberately GET-only. CKAN's Action API accepts POST with a JSON body, but the
federal portal's cache and WAF treat POST inconsistently and it is not needed
for any read we do. Every call here is a GET with query parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "gov-budget-audit (+https://github.com/j-kohl/gov-budget-audit)"
DEFAULT_TIMEOUT = 60.0


class CkanError(RuntimeError):
    """CKAN returned success: false, or a malformed envelope."""


@dataclass(frozen=True)
class Resource:
    """A downloadable file attached to a CKAN dataset."""

    id: str
    name: str
    url: str
    format: str
    last_modified: str | None = None
    size: int | None = None

    @property
    def format_lower(self) -> str:
        return (self.format or "").strip().lower()


@dataclass(frozen=True)
class Package:
    """A CKAN dataset."""

    id: str
    name: str
    title: str
    notes: str = ""
    resources: list[Resource] = field(default_factory=list)

    def resources_matching(
        self, *, formats: set[str] | None = None, name_contains: str | None = None
    ) -> list[Resource]:
        """Filter resources by format and/or a case-insensitive name fragment."""
        matched = self.resources
        if formats:
            wanted = {f.lower() for f in formats}
            matched = [r for r in matched if r.format_lower in wanted]
        if name_contains:
            needle = name_contains.lower()
            matched = [r for r in matched if needle in (r.name or "").lower() or needle in r.url.lower()]
        return matched


class CkanClient:
    """Read-only CKAN Action API client."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CkanClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def action(self, action: str, **params: Any) -> Any:
        """Call a CKAN action and return its `result`."""
        url = f"{self.base_url}/{action}"
        log.debug("CKAN GET %s %s", url, params)
        response = self._client.get(url, params=params)
        response.raise_for_status()

        try:
            envelope = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type")
            raise CkanError(f"{action}: response was not JSON (got {content_type})") from exc

        if not isinstance(envelope, dict) or "result" not in envelope:
            raise CkanError(f"{action}: unexpected envelope keys {list(envelope)[:6]}")
        if envelope.get("success") is False:
            raise CkanError(f"{action}: {envelope.get('error')}")

        return envelope["result"]

    # -- typed helpers ----------------------------------------------------

    def package_search(self, query: str, *, rows: int = 20, start: int = 0) -> list[Package]:
        result = self.action("package_search", q=query, rows=rows, start=start)
        return [_to_package(entry) for entry in result.get("results", [])]

    def package_show(self, package_id: str) -> Package:
        return _to_package(self.action("package_show", id=package_id))

    def recently_changed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Activity stream of recently updated datasets — the incremental-sync hook."""
        result = self.action("recently_changed_packages_activity_list", limit=limit)
        return result if isinstance(result, list) else []


def _to_package(payload: dict[str, Any]) -> Package:
    return Package(
        id=payload.get("id", ""),
        name=payload.get("name", ""),
        title=payload.get("title", "") or payload.get("title_translated", {}).get("en", ""),
        notes=payload.get("notes", "") or "",
        resources=[
            Resource(
                id=r.get("id", ""),
                name=_resource_name(r),
                url=r.get("url", ""),
                format=r.get("format", "") or "",
                last_modified=r.get("last_modified") or r.get("created"),
                size=r.get("size") if isinstance(r.get("size"), int) else None,
            )
            for r in payload.get("resources", [])
        ],
    )


def _resource_name(resource: dict[str, Any]) -> str:
    """CKAN resource names are sometimes plain strings, sometimes {lang: value}."""
    name = resource.get("name")
    if isinstance(name, dict):
        return name.get("fr") or name.get("en") or next(iter(name.values()), "")
    return name or ""
