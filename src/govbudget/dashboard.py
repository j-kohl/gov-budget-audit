"""Build the aggregate files the dashboard reads.

The staged Parquet is far too large to ship to a browser, and the dashboard only
ever shows aggregates and top-N tables. This module runs those aggregations in
DuckDB and writes small JSON/CSV files into the Observable Framework project, so
the published site is static and needs no server or database at run time.

Every money figure here comes from `seao_awards_won` — winners only, plain CAD
only. That view exists precisely so a query cannot accidentally sum losing bids
or add percentages to dollars.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb

from .staging import STAGING_DIR
from .storage import REPO_ROOT

log = logging.getLogger(__name__)

DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "src" / "data"

#: Rows kept in each top-N table. Enough to explore, small enough to stay static.
TOP_N = 100


def _parquet(dataset: str, source_id: str = "seao") -> str:
    pattern = STAGING_DIR / source_id / dataset / "*.parquet"
    return str(pattern).replace("\\", "/")


#: Identity of an award in economic terms: one notice paying one supplier one
#: amount on one date. Deliberately *not* the staged `dedupe_key`.
#:
#: `dedupe_key` is a source-level key — it faithfully records what one publisher
#: said, and it includes `award_id`. That is the right thing for staging, but it
#: does not resolve identity across SEAO's two eras, and measurement showed the
#: gap is not marginal: 20.4% of the headline total ($117.6B of $576.6B) was the
#: same award counted twice.
#:
#: Two causes, both invisible from a single file:
#:   * The eras overlap in *content*, not just in file dates. Splitting the
#:     backfill at 2021-05 stops files overlapping, but OCDS releases still
#:     republish awards the XML era already carried, under a new award_id.
#:   * `award_id` is absent in XML and present in OCDS, and the NEQ is often
#:     present in one era and missing in the other — so a key built from either
#:     field changes shape between eras and never matches.
#:
#: The trade-off: a notice that genuinely awards the same supplier the same
#: amount twice on one day collapses to one row. That is far rarer than the
#: duplication it removes.
ECONOMIC_KEY = """concat_ws('|',
    coalesce(notice_number, ocid, release_id, ''),
    lower(trim(coalesce(supplier_name, ''))),
    CAST(round(coalesce(amount, 0), 2) AS VARCHAR),
    CAST(coalesce(award_date, DATE '1900-01-01') AS VARCHAR)
)"""


def _connect(source_id: str = "seao") -> duckdb.DuckDBPyConnection:
    """In-memory connection with deduplicated views over the staged Parquet.

    Deduplication happens here, not at ingest. Staging stays faithful to what
    each file said; resolving one award published several ways is an analytical
    decision, and keeping it here means it can be revised without re-parsing
    17 years of archives.
    """
    con = duckdb.connect()
    con.execute(f"""
        CREATE VIEW awards AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY {ECONOMIC_KEY}
                -- Latest publication wins; everything after it is a tiebreaker.
                -- These are needed, not defensive: the same (notice, award,
                -- supplier) is republished when a notice is revised, and some
                -- revisions share a publication date while disagreeing about
                -- is_winner. Without a total order DuckDB picks arbitrarily and
                -- the headline total moves between runs of the same data.
                -- Ties resolve toward the awarded row, then the larger amount,
                -- then a content hash so the result is fully reproducible.
                ORDER BY publication_date DESC NULLS LAST,
                         award_date DESC NULLS LAST,
                         is_winner DESC NULLS LAST,
                         is_summable DESC NULLS LAST,
                         amount DESC NULLS LAST,
                         (supplier_neq IS NOT NULL) DESC,
                         md5(concat_ws('|', source_content_hash, source_format,
                                       ocid, release_id, award_id, title,
                                       buyer_name, supplier_name, supplier_neq,
                                       CAST(amount AS VARCHAR)))
            ) AS rn
            FROM read_parquet('{_parquet("awards", source_id)}')
        ) WHERE rn = 1
    """)
    con.execute("CREATE VIEW awards_won AS SELECT * FROM awards WHERE is_winner AND is_summable")

    for dataset, order in (("finals", "final_publication_date"), ("expenses", "expense_publication_date")):
        pattern = _parquet(dataset, source_id)
        if not list((STAGING_DIR / source_id / dataset).glob("*.parquet")):
            continue
        amount_col = "final_amount" if dataset == "finals" else "amount"
        con.execute(f"""
            CREATE VIEW {dataset} AS
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY concat_ws('|',
                        coalesce(notice_number, ''),
                        lower(trim(coalesce(supplier_name, ''))),
                        CAST(round(coalesce({amount_col}, 0), 2) AS VARCHAR))
                    ORDER BY {order} DESC NULLS LAST,
                             {amount_col} DESC NULLS LAST,
                             md5(concat_ws('|', source_content_hash,
                                           source_format, notice_number,
                                           CAST({amount_col} AS VARCHAR)))
                ) AS rn
                FROM read_parquet('{pattern}')
            ) WHERE rn = 1
        """)
    return con


def _has(con: duckdb.DuckDBPyConnection, view: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM duckdb_views() WHERE view_name = ?", [view]
    ).fetchone()[0])


def build(output_dir: Path | None = None, *, source_id: str = "seao") -> dict[str, int]:
    """Compute every aggregate and write it. Returns rows written per file."""
    out = output_dir or DASHBOARD_DATA_DIR
    out.mkdir(parents=True, exist_ok=True)
    con = _connect(source_id)
    written: dict[str, int] = {}

    def dump_csv(name: str, sql: str) -> None:
        path = out / f"{name}.csv"
        con.execute(f"COPY ({sql}) TO '{str(path).replace(chr(92), '/')}' (HEADER, DELIMITER ',')")
        written[name] = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        log.info("wrote %s (%d rows)", path.name, written[name])

    # -- headline ---------------------------------------------------------
    summary = con.execute("""
        SELECT
            count(*)                              AS awards,
            count(DISTINCT notice_number)         AS notices,
            count(DISTINCT supplier_name)         AS suppliers,
            count(DISTINCT buyer_name)            AS buyers,
            CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad,
            round(median(amount), 2)              AS median_cad,
            min(award_date)                       AS first_award,
            max(award_date)                       AS last_award
        FROM awards_won
    """).fetchone()

    # Coverage is reported on the publication date, not the award date. SEAO
    # publishes notices for contracts awarded long before — and, in a handful of
    # cases, mis-keyed as far in the future — so award_date is not a measure of
    # what a file covers.
    coverage = con.execute("""
        SELECT source_format, count(*) AS rows,
               min(publication_date) AS from_date, max(publication_date) AS to_date
        FROM awards_won GROUP BY 1 ORDER BY 1
    """).fetchall()

    # Outliers are reported rather than dropped: they are real published rows,
    # but they would stretch every axis and make the headline range misleading.
    quality = con.execute("""
        SELECT count(*) FILTER (WHERE award_date < DATE '2009-01-01') AS before_2009,
               count(*) FILTER (WHERE award_date > current_date)      AS future_dated,
               count(*) FILTER (WHERE award_date IS NULL)             AS missing_date
        FROM awards_won
    """).fetchone()

    # Headline range excludes those outliers so it describes the real dataset.
    span = con.execute("""
        SELECT min(award_date), max(award_date) FROM awards_won
        WHERE award_date >= DATE '2009-01-01' AND award_date <= current_date
    """).fetchone()

    bids = con.execute("""
        SELECT count(*) AS all_bids,
               count(*) FILTER (WHERE is_winner) AS winning_bids,
               count(*) FILTER (WHERE is_winner AND NOT is_summable) AS excluded_non_dollar
        FROM awards
    """).fetchone()

    overrun_total = 0.0
    if _has(con, "expenses"):
        overrun_total = con.execute(
            "SELECT CAST(coalesce(sum(CAST(amount AS DECIMAL(18,2))), 0) AS DOUBLE) "
            "FROM expenses"
        ).fetchone()[0]

    payload = {
        "awards": summary[0],
        "notices": summary[1],
        "suppliers": summary[2],
        "buyers": summary[3],
        "total_cad": float(summary[4] or 0),
        "median_cad": float(summary[5] or 0),
        "first_award": str(span[0]),
        "last_award": str(span[1]),
        "all_bids": bids[0],
        "winning_bids": bids[1],
        "excluded_non_dollar": bids[2],
        "overrun_total_cad": float(overrun_total),
        "data_quality": {
            "before_2009": quality[0],
            "future_dated": quality[1],
            "missing_date": quality[2],
        },
        "coverage": [
            {"format": r[0], "rows": r[1], "from": str(r[2]), "to": str(r[3])} for r in coverage
        ],
    }
    (out / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    written["summary"] = 1

    # -- time series ------------------------------------------------------
    dump_csv("monthly", """
        -- Cast to DATE, not TIMESTAMP: DuckDB writes a timestamp as
        -- '2009-01-01 00:00:00', which d3.autoType does not recognise as a date
        -- (it wants ISO-8601). The column would arrive as a string and the
        -- chart would silently render nothing.
        SELECT CAST(date_trunc('month', award_date) AS DATE) AS month,
               coalesce(competitiveness, 'other') AS method_key,
               count(*) AS awards,
               CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad
        FROM awards_won
        WHERE award_date IS NOT NULL AND award_date >= DATE '2009-01-01'
        GROUP BY 1, 2 ORDER BY 1, 2
    """)

    # -- who gets paid ----------------------------------------------------
    dump_csv("suppliers", f"""
        SELECT supplier_name, max(supplier_neq) AS neq,
               max(supplier_city) AS city,
               count(*) AS awards, CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad,
               count(*) FILTER (WHERE competitiveness = 'direct') AS direct_awards
        FROM awards_won WHERE supplier_name IS NOT NULL
        GROUP BY 1 ORDER BY total_cad DESC, supplier_name LIMIT {TOP_N}
    """)

    dump_csv("buyers", f"""
        SELECT buyer_name,
               max(is_municipal) AS is_municipal,
               count(*) AS awards, CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad,
               count(DISTINCT supplier_name) AS suppliers,
               count(*) FILTER (WHERE competitiveness = 'direct') AS direct_awards
        FROM awards_won WHERE buyer_name IS NOT NULL
        GROUP BY 1 ORDER BY total_cad DESC, buyer_name LIMIT {TOP_N}
    """)

    # -- how competitive --------------------------------------------------
    dump_csv("competition", """
        SELECT year(award_date) AS year,
               coalesce(competitiveness_label, 'Autre') AS method,
               coalesce(competitiveness, 'other') AS method_key,
               count(*) AS awards, CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad
        FROM awards_won
        WHERE award_date IS NOT NULL AND year(award_date) BETWEEN 2009 AND year(current_date)
        GROUP BY 1, 2, 3 ORDER BY 1, 2
    """)

    dump_csv("categories", f"""
        SELECT coalesce(nature_label, procurement_category, 'Non précisé') AS category,
               count(*) AS awards, CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad
        FROM awards_won GROUP BY 1 ORDER BY total_cad DESC, category LIMIT {TOP_N}
    """)

    dump_csv("regions", """
        SELECT coalesce(delivery_region_label, 'Non précisé') AS region,
               count(*) AS awards, CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS total_cad
        FROM awards_won GROUP BY 1 ORDER BY total_cad DESC, region
    """)

    # -- overruns ---------------------------------------------------------
    if _has(con, "expenses"):
        dump_csv("overruns", f"""
            WITH e AS (
                SELECT notice_number,
                       CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS overrun_cad,
                       count(*) AS expense_count,
                       max(description) AS reason,
                       max(expense_date) AS last_expense
                FROM expenses WHERE notice_number IS NOT NULL GROUP BY 1
            ),
            a AS (
                SELECT notice_number, max(buyer_name) AS buyer_name,
                       max(supplier_name) AS supplier_name,
                       max(title) AS title,
                       round(max(amount), 2) AS awarded_cad,
                       max(seao_url) AS seao_url
                FROM awards_won WHERE amount > 0 GROUP BY 1
            )
            SELECT a.notice_number, a.buyer_name, a.supplier_name, a.title,
                   a.awarded_cad, e.overrun_cad, e.expense_count, e.reason,
                   CASE WHEN a.awarded_cad >= 1000
                        THEN round(100.0 * e.overrun_cad / a.awarded_cad, 1)
                   END AS overrun_pct,
                   a.seao_url
            FROM e JOIN a USING (notice_number)
            ORDER BY e.overrun_cad DESC, a.notice_number LIMIT {TOP_N}
        """)

    # -- awarded versus final --------------------------------------------
    if _has(con, "finals"):
        dump_csv("variance", f"""
            WITH f AS (
                SELECT notice_number, round(max(final_amount), 2) AS final_cad
                FROM finals WHERE notice_number IS NOT NULL AND final_amount > 0 GROUP BY 1
            ),
            a AS (
                SELECT notice_number, max(buyer_name) AS buyer_name,
                       max(supplier_name) AS supplier_name,
                       round(max(amount), 2) AS awarded_cad
                FROM awards_won WHERE amount > 0 GROUP BY 1
            )
            SELECT a.notice_number, a.buyer_name, a.supplier_name,
                   a.awarded_cad, f.final_cad,
                   round(f.final_cad - a.awarded_cad, 2) AS delta_cad,
                   round(100.0 * (f.final_cad - a.awarded_cad) / a.awarded_cad, 1) AS delta_pct
            FROM f JOIN a USING (notice_number)
            WHERE abs(f.final_cad - a.awarded_cad) > 1
            ORDER BY abs(f.final_cad - a.awarded_cad) DESC, a.notice_number LIMIT {TOP_N}
        """)

    con.close()
    return written
