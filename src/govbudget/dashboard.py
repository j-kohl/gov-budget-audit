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

from . import taxonomy
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


# -- budget lines: federal vs Quebec --------------------------------------


def _budget_parquet(source_id: str) -> str:
    return str(STAGING_DIR / source_id / "budget_lines" / "*.parquet").replace("\\", "/")


def _has_budget(source_id: str) -> bool:
    return any((STAGING_DIR / source_id / "budget_lines").glob("*.parquet"))


def build_budget(output_dir: Path | None = None) -> dict[str, int]:
    """Aggregate the two budget sources onto the shared spine.

    Federal and Quebec stay in separate rows throughout — never summed, never
    averaged. The only thing this joins on is the harmonized economic category,
    and every row carries the comparability verdict so the page can refuse to
    draw a comparison the taxonomy says is invalid.

    Federal uses `expenditures` (what was spent); Quebec publishes credits, so
    it uses `authorities` (what was approved). Those are different measures and
    the page says so — it is the closest honest pairing available until the
    Comptes publics actuals are ingested.
    """
    out = output_dir or DASHBOARD_DATA_DIR
    out.mkdir(parents=True, exist_ok=True)
    qc_source = "qc_comptes_publics" if _has_budget("qc_comptes_publics") else "qc_budget_depenses"
    if not (_has_budget("gc_infobase") and _has_budget(qc_source)):
        log.warning("budget lines missing for one or both jurisdictions; skipping")
        return {}

    con = duckdb.connect()
    con.execute(f"""
        CREATE VIEW budget AS
        SELECT * FROM read_parquet('{_budget_parquet("gc_infobase")}')
        UNION ALL BY NAME
        SELECT * FROM read_parquet('{_budget_parquet(qc_source)}')
    """)
    # One slice per jurisdiction, both on actual expenditure now that the
    # Comptes publics are ingested. Quebec previously had to use credits, which
    # meant comparing a plan against an outturn.
    #
    # The beneficiary table is excluded: it is the same transfer money cut by
    # recipient, so including it would double-count Quebec's transfers.
    qc_filter = (
        "measure = 'expenditures' AND dimensions LIKE '%comptes_publics%'"
        if qc_source == "qc_comptes_publics"
        else "measure = 'authorities'"
    )
    con.execute(f"""
        CREATE VIEW spine AS
        SELECT jurisdiction, fiscal_year, organization,
               coalesce(economic_category, 'other') AS economic_category,
               appropriation,
               CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount
        FROM budget
        WHERE (jurisdiction = 'ca-federal' AND measure = 'expenditures'
               AND dimensions LIKE '%standard_object%')
           OR (jurisdiction = 'qc' AND {qc_filter})
        GROUP BY 1, 2, 3, 4, 5
    """)

    written: dict[str, int] = {}

    def dump(name: str, sql: str) -> None:
        path = str(out / f"{name}.csv").replace("\\", "/")
        con.execute(f"COPY ({sql}) TO '{path}' (HEADER, DELIMITER ',')")
        written[name] = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]

    dump("budget_categories", """
        SELECT jurisdiction, fiscal_year, economic_category,
               CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount
        FROM spine GROUP BY 1,2,3 ORDER BY 1,2,3
    """)
    dump("budget_organizations", """
        SELECT * EXCLUDE (rn) FROM (
            SELECT jurisdiction, fiscal_year, organization,
                   CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount,
                   row_number() OVER (PARTITION BY jurisdiction, fiscal_year
                                      ORDER BY sum(amount) DESC, organization) AS rn
            FROM spine GROUP BY 1,2,3)
        WHERE rn <= 15 ORDER BY jurisdiction, fiscal_year, rn
    """)
    # Appropriation comes from a different federal table than the economic
    # split: standard-object rows carry no voted/statutory flag, the vote table
    # does. Drawn from whichever table actually has it per jurisdiction.
    dump("budget_appropriation", """
        SELECT jurisdiction, fiscal_year, appropriation,
               CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount
        FROM budget
        WHERE appropriation IS NOT NULL
          AND ((jurisdiction = 'ca-federal' AND measure = 'expenditures'
                AND dimensions LIKE '%vote%')
            OR (jurisdiction = 'qc' AND measure = 'authorities'))
        GROUP BY 1,2,3 ORDER BY 1,2,3
    """)

    # -- where the money actually goes ---------------------------------
    # The named-programme views. The economic split says a category; these say
    # which programme inside it, which is the question people arrive with.
    dump("spending_programmes", """
        WITH named AS (
            -- Federal: statutory and voted appropriations carry their own name
            -- in the vote table, and transfer programmes carry theirs.
            SELECT 'ca-federal' AS jurisdiction, fiscal_year,
                   organization, programme,
                   CASE WHEN dimensions LIKE '%transfer_payments%'
                        THEN 'transfer' ELSE 'appropriation' END AS kind,
                   amount
            FROM budget
            WHERE jurisdiction = 'ca-federal' AND measure = 'expenditures'
              AND programme IS NOT NULL
              AND (dimensions LIKE '%vote%' OR dimensions LIKE '%transfer_payments%')
            UNION ALL
            SELECT 'qc', fiscal_year, organization, programme,
                   'programme', amount
            FROM budget
            WHERE jurisdiction = 'qc' AND measure = 'expenditures'
              AND dimensions LIKE '%comptes_publics%'
              AND programme IS NOT NULL
            UNION ALL
            -- Who actually receives Quebec's transfers.
            SELECT 'qc', fiscal_year, organization, programme,
                   'transfer', amount
            FROM budget
            WHERE jurisdiction = 'qc' AND dimensions LIKE '%beneficiaires%'
              AND programme IS NOT NULL
        )
        -- Aggregated across organizations, not per organization. Generic vote
        -- names — 'Operating/Program', 'Capital', 'Grants & Contributions' —
        -- exist in every department, so grouping by organization emits the same
        -- label many times and a bar chart silently stacks them into one
        -- segmented bar. Summing across departments is also the more useful
        -- figure: it is what the government spends on that vote in total.
        SELECT * EXCLUDE (rn) FROM (
            SELECT jurisdiction, fiscal_year, programme, kind,
                   CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount,
                   count(DISTINCT organization) AS organizations,
                   max(organization) AS organization,
                   row_number() OVER (PARTITION BY jurisdiction, fiscal_year, kind
                                      ORDER BY sum(amount) DESC, programme) AS rn
            FROM named GROUP BY 1,2,3,4)
        WHERE rn <= 25 ORDER BY jurisdiction, fiscal_year, kind, rn
    """)

    # Lapsed spending: approved but not spent. Only meaningful where a source
    # publishes both measures for the same rows, which the vote table does.
    dump("spending_lapsed", """
        SELECT fiscal_year,
               CAST(sum(CAST(amount AS DECIMAL(18,2)))
                    FILTER (WHERE measure = 'authorities') AS DOUBLE) AS authorities,
               CAST(sum(CAST(amount AS DECIMAL(18,2)))
                    FILTER (WHERE measure = 'expenditures') AS DOUBLE) AS expenditures
        FROM budget
        WHERE jurisdiction = 'ca-federal' AND dimensions LIKE '%vote%'
        GROUP BY 1 HAVING authorities IS NOT NULL AND expenditures IS NOT NULL
        ORDER BY 1
    """)

    # -- programme drill-down ------------------------------------------
    # org x programme x economic category for the latest year with data. The
    # voted and statutory tables together reconcile to within 0.4% of the flat
    # standard-object table, so this is the same money at finer grain — but it
    # is deliberately NOT part of `spine`, which would double-count it against
    # the standard-object rows.
    if con.execute("""
        SELECT count(*) FROM budget
        WHERE dimensions LIKE '%programs_by_vote%' OR dimensions LIKE '%programs_statutory%'
    """).fetchone()[0]:
        drill_year = con.execute("""
            SELECT max(fiscal_year) FROM budget
            WHERE dimensions LIKE '%programs_by_vote%' OR dimensions LIKE '%programs_statutory%'
        """).fetchone()[0]
        dump("program_drilldown", f"""
            SELECT organization, programme,
                   coalesce(economic_category, 'other') AS economic_category,
                   coalesce(economic_source_label, 'Non précisé') AS standard_object,
                   coalesce(appropriation, 'unknown') AS appropriation,
                   CAST(sum(CAST(amount AS DECIMAL(18,2))) AS DOUBLE) AS amount
            FROM budget
            WHERE fiscal_year = '{drill_year}'
              AND (dimensions LIKE '%programs_by_vote%'
                OR dimensions LIKE '%programs_statutory%')
              AND organization IS NOT NULL AND programme IS NOT NULL
            GROUP BY 1,2,3,4,5
            -- Negative groups are kept. External and internal revenues are
            -- booked as negative standard objects, so dropping them inflates
            -- the total by $16.3B and breaks the reconciliation against the
            -- flat table. Charts filter them out; the totals must not.
            ORDER BY amount DESC
        """)
        (out / "drilldown_meta.json").write_text(
            json.dumps({"fiscal_year": drill_year}, indent=2), encoding="utf-8"
        )
        written["drilldown_meta"] = 1

    latest = {
        row[0]: row[1] for row in
        con.execute("SELECT jurisdiction, max(fiscal_year) FROM spine GROUP BY 1").fetchall()
    }
    payload = {
        "latest_year": latest,
        "categories": [
            {
                "key": category,
                "label_en": taxonomy.ECONOMIC_LABELS[category][0],
                "label_fr": taxonomy.ECONOMIC_LABELS[category][1],
                "level": taxonomy.comparability(category).level,
                "note": taxonomy.comparability(category).note,
                "note_fr": taxonomy.comparability(category).note_fr,
            }
            for category in taxonomy.ECONOMIC_CATEGORIES
        ],
        "measures": {
            "ca-federal": "expenditures",
            "qc": "expenditures" if qc_source == "qc_comptes_publics" else "authorities",
        },
        "qc_source": qc_source,
    }
    (out / "comparability.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    written["comparability"] = len(payload["categories"])
    con.close()
    log.info("wrote budget aggregates: %s", written)
    return written
