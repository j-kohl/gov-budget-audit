"""Write parsed records to Parquet, and load the staging layer into DuckDB.

Parquet sits between the raw store and any query engine. Keeping it as files
rather than rows in a transactional database means a parser fix is a re-run over
the raw store, not a migration.

Each source file produces one Parquet file per record type, named by the raw
content hash, so re-ingesting the same bytes overwrites in place and never
duplicates.

The three SEAO record types are staged into separate datasets — `awards`,
`finals` and `expenses` — because they are different facts that happen to share
a join key. See `govbudget.models` for why.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, fields
from pathlib import Path

import polars as pl

from .models import BudgetLine, ContractAward, ContractExpense, ContractFinal
from .seao_codes import SUMMABLE_UNITS
from .storage import DEFAULT_DATA_DIR

log = logging.getLogger(__name__)

STAGING_DIR = DEFAULT_DATA_DIR / "staging"

#: Explicit schemas. Without them, a batch where every value in a column is null
#: infers as pl.Null and then fails to concatenate with a batch where it is not.
AWARD_SCHEMA: dict[str, pl.DataType] = {
    "source_id": pl.Utf8,
    "source_content_hash": pl.Utf8,
    "source_format": pl.Utf8,
    "notice_number": pl.Utf8,
    "buyer_reference": pl.Utf8,
    "ocid": pl.Utf8,
    "release_id": pl.Utf8,
    "award_id": pl.Utf8,
    "buyer_name": pl.Utf8,
    "buyer_id": pl.Utf8,
    "buyer_city": pl.Utf8,
    "buyer_region": pl.Utf8,
    "is_municipal": pl.Boolean,
    "title": pl.Utf8,
    "description": pl.Utf8,
    "notice_type_code": pl.Utf8,
    "notice_type_label": pl.Utf8,
    "competitiveness": pl.Utf8,
    "competitiveness_label": pl.Utf8,
    "nature_code": pl.Utf8,
    "nature_label": pl.Utf8,
    "procurement_method": pl.Utf8,
    "procurement_category": pl.Utf8,
    "seao_category": pl.Utf8,
    "unspsc_code": pl.Utf8,
    "delivery_region_code": pl.Utf8,
    "delivery_region_label": pl.Utf8,
    "disposition_code": pl.Utf8,
    "publication_date": pl.Date,
    "closing_date": pl.Date,
    "award_date": pl.Date,
    "contract_start": pl.Date,
    "contract_end": pl.Date,
    "amount": pl.Float64,
    "amount_unit_code": pl.Utf8,
    "amount_unit_label": pl.Utf8,
    "currency": pl.Utf8,
    "supplier_name": pl.Utf8,
    "supplier_neq": pl.Utf8,
    "supplier_city": pl.Utf8,
    "supplier_region": pl.Utf8,
    "supplier_country": pl.Utf8,
    "supplier_postal_code": pl.Utf8,
    "is_winner": pl.Boolean,
    "is_compliant": pl.Boolean,
    "is_eligible": pl.Boolean,
    "number_of_bidders": pl.Int64,
    "seao_url": pl.Utf8,
    "unmapped": pl.Utf8,
    "dedupe_key": pl.Utf8,
    #: True when `amount` is a plain CAD figure. Materialized so SQL callers
    #: cannot forget the unit filter.
    "is_summable": pl.Boolean,
}

FINAL_SCHEMA: dict[str, pl.DataType] = {
    "source_id": pl.Utf8,
    "source_content_hash": pl.Utf8,
    "source_format": pl.Utf8,
    "notice_number": pl.Utf8,
    "buyer_reference": pl.Utf8,
    "final_date": pl.Date,
    "final_publication_date": pl.Date,
    "final_amount": pl.Float64,
    "supplier_name": pl.Utf8,
    "supplier_neq": pl.Utf8,
    "unmapped": pl.Utf8,
    "dedupe_key": pl.Utf8,
}

EXPENSE_SCHEMA: dict[str, pl.DataType] = {
    "source_id": pl.Utf8,
    "source_content_hash": pl.Utf8,
    "source_format": pl.Utf8,
    "notice_number": pl.Utf8,
    "buyer_reference": pl.Utf8,
    "expense_date": pl.Date,
    "expense_publication_date": pl.Date,
    "amount": pl.Float64,
    "description": pl.Utf8,
    "supplier_name": pl.Utf8,
    "supplier_neq": pl.Utf8,
    "unmapped": pl.Utf8,
    "dedupe_key": pl.Utf8,
}

BUDGET_SCHEMA: dict[str, pl.DataType] = {
    "source_id": pl.Utf8,
    "source_content_hash": pl.Utf8,
    "jurisdiction": pl.Utf8,
    "fiscal_year": pl.Utf8,
    "organization": pl.Utf8,
    "organization_id": pl.Utf8,
    "programme": pl.Utf8,
    "programme_id": pl.Utf8,
    "measure": pl.Utf8,
    "amount": pl.Float64,
    "currency": pl.Utf8,
    "economic_category": pl.Utf8,
    "economic_source_label": pl.Utf8,
    "appropriation": pl.Utf8,
    "dimensions": pl.Utf8,
    "dedupe_key": pl.Utf8,
}

DATASETS: dict[str, tuple[type, dict[str, pl.DataType]]] = {
    "budget_lines": (BudgetLine, BUDGET_SCHEMA),
    "awards": (ContractAward, AWARD_SCHEMA),
    "finals": (ContractFinal, FINAL_SCHEMA),
    "expenses": (ContractExpense, EXPENSE_SCHEMA),
}


def to_frame(records: Sequence[object], dataset: str) -> pl.DataFrame:
    """Convert records to a DataFrame with a stable schema."""
    record_type, schema = DATASETS[dataset]
    if not records:
        return pl.DataFrame(schema=schema)

    known = {f.name for f in fields(record_type)}
    rows = []
    for record in records:
        row = {k: v for k, v in asdict(record).items() if k in known}
        # `unmapped` holds whatever the parser could not place; keep it as JSON
        # so the column stays scalar and survives a Parquet round trip.
        for blob in ("unmapped", "dimensions"):
            if blob in schema:
                row[blob] = json.dumps(row.get(blob) or {}, ensure_ascii=False, sort_keys=True)
        row["dedupe_key"] = record.key()
        if dataset == "awards":
            row["is_summable"] = record.is_summable
        rows.append(row)

    return pl.DataFrame(rows, schema=schema)


def awards_to_frame(awards: Sequence[ContractAward]) -> pl.DataFrame:
    """Backwards-compatible alias for the awards dataset."""
    return to_frame(awards, "awards")


def write_records(
    records: Sequence[object],
    *,
    dataset: str,
    source_id: str,
    content_hash: str,
    staging_dir: Path | None = None,
) -> Path | None:
    """Write one source file's records to Parquet. Returns None when empty."""
    if not records:
        return None

    directory = (staging_dir or STAGING_DIR) / source_id / dataset
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{content_hash or 'unhashed'}.parquet"

    frame = to_frame(records, dataset)
    frame.write_parquet(path, compression="zstd")
    log.info("wrote %d %s to %s", frame.height, dataset, path.name)
    return path


def write_awards(
    awards: Sequence[ContractAward],
    *,
    source_id: str,
    content_hash: str,
    staging_dir: Path | None = None,
) -> Path | None:
    return write_records(
        awards,
        dataset="awards",
        source_id=source_id,
        content_hash=content_hash,
        staging_dir=staging_dir,
    )


def load(
    dataset: str = "awards", source_id: str = "seao", staging_dir: Path | None = None
) -> pl.DataFrame:
    """Read every staged Parquet file for a dataset into one frame."""
    _, schema = DATASETS[dataset]
    directory = (staging_dir or STAGING_DIR) / source_id / dataset
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return pl.DataFrame(schema=schema)
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def load_awards(source_id: str = "seao", staging_dir: Path | None = None) -> pl.DataFrame:
    return load("awards", source_id, staging_dir)


def deduplicate(frame: pl.DataFrame, *, sort_by: str = "publication_date") -> pl.DataFrame:
    """Drop repeated records, keeping the most recently published row.

    The weekly and monthly SEAO drops overlap, the monthly archives re-publish
    notices whenever they are revised, and the Revisions files restate earlier
    records. Without this, totals inflate.
    """
    if frame.is_empty():
        return frame
    if sort_by not in frame.columns:
        sort_by = next(
            (c for c in ("publication_date", "final_publication_date",
                         "expense_publication_date") if c in frame.columns),
            frame.columns[0],
        )
    return frame.sort(sort_by, descending=True, nulls_last=True).unique(
        subset=["dedupe_key"], keep="first"
    )


def summarize(frame: pl.DataFrame) -> dict[str, object]:
    """Headline figures for staged awards.

    Totals count winning bids with a summable unit only. Anything else — losing
    bids, and amounts denominated in %/points/per-km — is reported separately
    rather than folded in, because adding them produces a number that means
    nothing.
    """
    if frame.is_empty():
        return {"rows": 0}

    stats: dict[str, object] = {
        "rows": frame.height,
        "unique_keys": frame["dedupe_key"].n_unique(),
    }

    if "is_winner" in frame.columns:
        winners = frame.filter(pl.col("is_winner"))
        stats["bids"] = frame.height
        stats["winning_bids"] = winners.height

        summable = winners.filter(pl.col("is_summable") & pl.col("amount").is_not_null())
        excluded = winners.filter(~pl.col("is_summable") & pl.col("amount").is_not_null())
        stats["awarded_total_cad"] = float(summable["amount"].sum() or 0.0)
        stats["awarded_rows_counted"] = summable.height
        stats["excluded_non_dollar_rows"] = excluded.height
        if excluded.height:
            units = excluded["amount_unit_label"].value_counts().iter_rows()
            stats["excluded_units"] = dict(units)
        stats["distinct_buyers"] = winners["buyer_name"].n_unique()
        stats["distinct_suppliers"] = winners["supplier_name"].n_unique()
        date_col = "award_date"
    else:
        amount_col = "final_amount" if "final_amount" in frame.columns else "amount"
        stats["total_cad"] = float(frame[amount_col].sum() or 0.0)
        stats["distinct_suppliers"] = frame["supplier_name"].n_unique()
        date_col = "final_date" if "final_date" in frame.columns else "expense_date"

    if date_col in frame.columns:
        stats["date_min"] = str(frame[date_col].min())
        stats["date_max"] = str(frame[date_col].max())
    return stats


def load_duckdb(
    database: Path | None = None,
    *,
    source_id: str = "seao",
    staging_dir: Path | None = None,
) -> Path:
    """Register the staged Parquet as DuckDB views.

    DuckDB reads Parquet directly, so these are views over the files rather than
    copies — they stay current as new files are staged.
    """
    import duckdb

    root = (staging_dir or STAGING_DIR) / source_id
    db_path = database or (DEFAULT_DATA_DIR / "govbudget.duckdb")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(db_path))
    try:
        for dataset in DATASETS:
            directory = root / dataset
            if not any(directory.glob("*.parquet")):
                continue
            pattern = str(directory / "*.parquet").replace("\\", "/")
            connection.execute(
                f"CREATE OR REPLACE VIEW {source_id}_{dataset} AS "
                f"SELECT * FROM read_parquet('{pattern}')"
            )
        # Convenience view: winners only, in dollars. The one to query for money.
        if any((root / "awards").glob("*.parquet")):
            connection.execute(
                f"CREATE OR REPLACE VIEW {source_id}_awards_won AS "
                f"SELECT * FROM {source_id}_awards WHERE is_winner AND is_summable"
            )
    finally:
        connection.close()
    return db_path


__all__ = [
    "AWARD_SCHEMA",
    "FINAL_SCHEMA",
    "EXPENSE_SCHEMA",
    "DATASETS",
    "SUMMABLE_UNITS",
    "to_frame",
    "awards_to_frame",
    "write_records",
    "write_awards",
    "load",
    "load_awards",
    "deduplicate",
    "summarize",
    "load_duckdb",
]
