"""Content-addressed landing zone for raw source files.

Raw files are stored verbatim, named by the SHA-256 of their bytes, and never
mutated. Parsing reads from here, so a parser change can be replayed over the
whole history without re-fetching anything — which matters when a source
publishes a decade of files and rate-limits you.

Layout:
    data/raw/<source_id>/<hash[:2]>/<hash><ext>
    data/raw/<source_id>/manifest.jsonl

The manifest is append-only. One line per successful fetch, recording the URL,
the content hash, and the HTTP validators needed to make the next fetch
conditional.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = Path(os.environ.get("GOVBUDGET_DATA_DIR", REPO_ROOT / "data"))

USER_AGENT = "gov-budget-audit (+https://github.com/j-kohl/gov-budget-audit)"
CHUNK_SIZE = 1 << 20


@dataclass
class RawFile:
    """A file that has landed in the raw store."""

    source_id: str
    url: str
    content_hash: str
    size_bytes: int
    fetched_at: str
    relative_path: str
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    #: True when a conditional request confirmed we already had these bytes.
    from_cache: bool = False

    def path(self, store: RawStore) -> Path:
        return store.root / self.relative_path


class RawStore:
    """Append-only, content-addressed store for fetched files."""

    def __init__(self, data_dir: Path | None = None, *, client: httpx.Client | None = None):
        self.root = (data_dir or DEFAULT_DATA_DIR) / "raw"
        self.root.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> RawStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- manifest ---------------------------------------------------------

    def manifest_path(self, source_id: str) -> Path:
        return self.root / source_id / "manifest.jsonl"

    def manifest(self, source_id: str) -> list[RawFile]:
        path = self.manifest_path(source_id)
        if not path.exists():
            return []
        entries: list[RawFile] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed manifest line in %s", path)
                continue
            payload.pop("from_cache", None)
            entries.append(RawFile(**payload, from_cache=False))
        return entries

    def _last_seen(self, source_id: str, url: str) -> RawFile | None:
        """Most recent manifest entry for a URL, for conditional requests."""
        matches = [entry for entry in self.manifest(source_id) if entry.url == url]
        return matches[-1] if matches else None

    def _append_manifest(self, entry: RawFile) -> None:
        path = self.manifest_path(entry.source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = asdict(entry)
        record.pop("from_cache", None)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # -- fetching ---------------------------------------------------------

    def fetch(self, url: str, source_id: str, *, force: bool = False) -> RawFile:
        """Download a URL into the store, skipping the transfer when unchanged.

        Uses If-None-Match / If-Modified-Since from the last recorded fetch. A
        304 returns the existing entry with `from_cache=True`.
        """
        previous = None if force else self._last_seen(source_id, url)
        headers: dict[str, str] = {}
        if previous:
            if previous.etag:
                headers["If-None-Match"] = previous.etag
            if previous.last_modified:
                headers["If-Modified-Since"] = previous.last_modified

        with self._client.stream("GET", url, headers=headers) as response:
            if response.status_code == 304 and previous is not None:
                log.info("unchanged, skipping download: %s", url)
                return RawFile(**{**asdict(previous), "from_cache": True})
            response.raise_for_status()

            digest = hashlib.sha256()
            temp_path = self.root / source_id / f".incoming-{os.getpid()}-{id(response)}"
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with temp_path.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    digest.update(chunk)
                    handle.write(chunk)
                    size += len(chunk)

            content_hash = digest.hexdigest()
            content_type = response.headers.get("Content-Type")
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")

        relative = Path(source_id) / content_hash[:2] / f"{content_hash}{_extension(url)}"
        final_path = self.root / relative
        final_path.parent.mkdir(parents=True, exist_ok=True)

        if final_path.exists():
            # Same bytes already stored, reached via a different URL or re-fetch.
            temp_path.unlink(missing_ok=True)
        else:
            temp_path.replace(final_path)

        entry = RawFile(
            source_id=source_id,
            url=url,
            content_hash=content_hash,
            size_bytes=size,
            fetched_at=datetime.now(UTC).isoformat(),
            relative_path=str(relative),
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
        )
        self._append_manifest(entry)
        log.info("stored %s (%s bytes) from %s", content_hash[:12], size, url)
        return entry

    def read(self, entry: RawFile) -> bytes:
        return (self.root / entry.relative_path).read_bytes()


def _extension(url: str) -> str:
    """Best-effort file extension from a URL path, for human browsability only."""
    suffix = Path(urlparse(url).path).suffix.lower()
    # Guard against query-string junk and absurd suffixes being treated as extensions.
    if suffix and len(suffix) <= 6 and suffix[1:].isalnum():
        return suffix
    return ""
