"""Write parsed records to Parquet, and load the staging layer into DuckDB.

Parquet sits between the raw store and any query engine. Keeping it as files
rather than rows in a transactional database means a parser fix is a re-run over
the raw store, not a migration.

Each source file produces one Parquet file named by the raw content hash, so
re-ingesting the same bytes overwrites in place and never duplicates.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import asdict, fields
from pathlib import Path

import polars as pl

from .models import ContractAward
from .storage import DEFAULT_DATA_DIR

log = logging.getLogger(__name__)

STAGING_DIR = DEFAULT_DATA_DIR / "staging"

#: Explicit schema. Without it, a batch where every value in a column is null
#: infers as pl.Null and then fails to concatenate with a batch where it is not.
AWARD_SCHEMA: dict[str, pl.DataType] = {
    "source_id": pl.Utf8,
    "source_content_hash": pl.Utf8,
    "source_format": pl.Utf8,
    "ocid": pl.Utf8,
    "notice_number": pl.Utf8,
    "release_id": pl.Utf8,
    "award_id": pl.Utf8,
    "buyer_name": pl.Utf8,
    "buyer_id": pl.Utf8,
    "buyer_category": pl.Utf8,
    "title": pl.Utf8,
    "description": pl.Utf8,
    "procurement_method": pl.Utf8,
    "procurement_category": pl.Utf8,
    "unspsc_code": pl.Utf8,
    "publication_date": pl.Date,
    "award_date": pl.Date,
    "contract_start": pl.Date,
    "contract_end": pl.Date,
    "amount": pl.Float64,
    "currency": pl.Utf8,
    "supplier_name": pl.Utf8,
    "supplier_neq": pl.Utf8,
    "supplier_city": pl.Utf8,
    "supplier_region": pl.Utf8,
    "number_of_bidders": pl.Int64,
    "unmapped": pl.Utf8,
    "dedupe_key": pl.Utf8,
}


def awards_to_frame(awards: Sequence[ContractAward]) -> pl.DataFrame:
    """Convert award records to a DataFrame with a stable schema."""
    if not awards:
        return pl.DataFrame(schema=AWARD_SCHEMA)

    known = {f.name for f in fields(ContractAward)}
    rows = []
    for award in awards:
        row = {k: v for k, v in asdict(award).items() if k in known}
        # `unmapped` is a dict of whatever the parser could not place; keep it
        # as JSON so the column stays a scalar and survives a Parquet round trip.
        row["unmapped"] = json.dumps(row.get("unmapped") or {}, ensure_ascii=False, sort_keys=True)
        row["dedupe_key"] = award.key()
        rows.append(row)

    return pl.DataFrame(rows, schema=AWARD_SCHEMA)


def write_awards(
    awards: Sequence[ContractAward],
    *,
    source_id: str,
    content_hash: str,
    staging_dir: Path | None = None,
) -> Path | None:
    """Write one source file's awards to Parquet. Returns None when empty."""
    if not awards:
        return None

    directory = (staging_dir or STAGING_DIR) / source_id / "awards"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{content_hash or 'unhashed'}.parquet"

    frame = awards_to_frame(awards)
    frame.write_parquet(path, compression="zstd")
    log.info("wrote %d awards to %s", frame.height, path)
    return path


def load_awards(source_id: str = "seao", staging_dir: Path | None = None) -> pl.DataFrame:
    """Read every staged Parquet file for a source into one frame."""
    directory = (staging_dir or STAGING_DIR) / source_id / "awards"
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return pl.DataFrame(schema=AWARD_SCHEMA)
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def deduplicate(frame: pl.DataFrame) -> pl.DataFrame:
    """Drop repeated awards, keeping the most recently published row.

    The weekly and monthly SEAO drops overlap, and the same award is reissued
    when a notice is amended. Without this, totals inflate.
    """
    if frame.is_empty():
        return frame
    return (
        frame.sort("publication_date", descending=True, nulls_last=True)
        .unique(subset=["dedupe_key"], keep="first")
        .sort("award_date", descending=True, nulls_last=True)
    )


def summarize(frame: pl.DataFrame) -> dict[str, object]:
    """Headline figures for a staged frame, for CLI output and sanity checks."""
    if frame.is_empty():
        return {"rows": 0}

    non_null_amounts = frame.filter(pl.col("amount").is_not_null())
    return {
        "rows": frame.height,
        "unique_awards": frame["dedupe_key"].n_unique(),
        "with_amount": non_null_amounts.height,
        "total_value": float(non_null_amounts["amount"].sum() or 0.0),
        "distinct_buyers": frame["buyer_name"].n_unique(),
        "distinct_suppliers": frame["supplier_name"].n_unique(),
        "date_min": str(frame["award_date"].min()),
        "date_max": str(frame["award_date"].max()),
        "by_format": dict(frame["source_format"].value_counts().iter_rows()),
    }


def load_duckdb(
    database: Path | None = None,
    *,
    source_id: str = "seao",
    staging_dir: Path | None = None,
) -> Path:
    """Register the staged Parquet as a DuckDB table for querying.

    DuckDB reads Parquet directly, so this creates a view over the files rather
    than copying them — the table stays current as new files are staged.
    """
    import duckdb

    directory = (staging_dir or STAGING_DIR) / source_id / "awards"
    db_path = database or (DEFAULT_DATA_DIR / "govbudget.duckdb")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pattern = str(directory / "*.parquet")
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            f"CREATE OR REPLACE VIEW {source_id}_awards AS SELECT * FROM read_parquet('{pattern}')"
        )
    finally:
        connection.close()
    return db_path


def iter_award_batches(
    batches: Iterable[tuple[str, Sequence[ContractAward]]],
    *,
    source_id: str,
    staging_dir: Path | None = None,
) -> list[Path]:
    """Write several (content_hash, awards) batches, returning the paths written."""
    written = []
    for content_hash, awards in batches:
        path = write_awards(
            awards, source_id=source_id, content_hash=content_hash, staging_dir=staging_dir
        )
        if path:
            written.append(path)
    return written
