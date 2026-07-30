"""Access to sources/registry.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "sources" / "registry.json"


@dataclass(frozen=True)
class Source:
    """One entry in the registry."""

    id: str
    name: str
    jurisdiction: str
    tier: int
    access: str
    covers: str
    raw: dict[str, Any]

    @property
    def ckan_api(self) -> str | None:
        return self.raw.get("ckan_api")

    @property
    def portal(self) -> str | None:
        return self.raw.get("portal")

    @property
    def notes(self) -> str:
        return self.raw.get("notes", "")

    @property
    def verified(self) -> bool:
        return bool(self.raw.get("verified", False))


@dataclass(frozen=True)
class Caveat:
    id: str
    severity: str
    summary: str


@cache
def load_registry(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))


def sources(path: Path | None = None) -> list[Source]:
    return [
        Source(
            id=entry["id"],
            name=entry["name"],
            jurisdiction=entry["jurisdiction"],
            tier=entry["tier"],
            access=entry["access"],
            covers=entry["covers"],
            raw=entry,
        )
        for entry in load_registry(path)["sources"]
    ]


def get_source(source_id: str, path: Path | None = None) -> Source:
    for source in sources(path):
        if source.id == source_id:
            return source
    known = ", ".join(s.id for s in sources(path))
    raise KeyError(f"unknown source {source_id!r}; registry has: {known}")


def caveats(path: Path | None = None) -> list[Caveat]:
    return [Caveat(**entry) for entry in load_registry(path)["caveats"]]
