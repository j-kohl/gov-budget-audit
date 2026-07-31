"""Tests for the dashboard aggregation layer.

These matter because the aggregates are what anyone actually reads. A filter
dropped here silently produces a wrong headline number with no error.
"""

import csv
import json
from datetime import date

import pytest

from govbudget import dashboard, staging
from govbudget.models import ContractAward, ContractExpense, ContractFinal


def award(**overrides) -> ContractAward:
    base = dict(
        source_id="seao",
        source_content_hash="h",
        source_format="seao-xml",
        notice_number="1",
        supplier_name="Fournisseur A",
        buyer_name="Ministère X",
        amount=100.0,
        amount_unit_code="1",
        award_date=date(2024, 1, 15),
        publication_date=date(2024, 1, 20),
        is_winner=True,
        competitiveness="open",
        competitiveness_label="Appel d'offres public",
        nature_label="Services",
        delivery_region_label="Montréal",
    )
    base.update(overrides)
    return ContractAward(**base)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Stage a small, deliberately awkward dataset and build the aggregates."""
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(dashboard, "STAGING_DIR", tmp_path / "staging")

    awards = [
        award(notice_number="1", supplier_name="A", amount=1000.0),
        # A losing bid: must never reach a total.
        award(notice_number="1", supplier_name="B", amount=9999.0, is_winner=False),
        # A winner priced in points, not dollars: must be excluded from sums.
        award(
            notice_number="2", supplier_name="C", amount=88.0,
            amount_unit_code="11", competitiveness="direct",
            competitiveness_label="Gré à gré",
        ),
        award(
            notice_number="3", supplier_name="D", amount=500.0,
            competitiveness="direct", competitiveness_label="Gré à gré",
        ),
    ]
    staging.write_records(
        awards, dataset="awards", source_id="seao", content_hash="h1",
        staging_dir=tmp_path / "staging",
    )
    staging.write_records(
        [ContractFinal(
            source_id="seao", source_content_hash="h", source_format="seao-xml",
            notice_number="1", final_amount=1200.0, final_date=date(2024, 6, 1),
            final_publication_date=date(2024, 6, 2), supplier_name="A",
        )],
        dataset="finals", source_id="seao", content_hash="h1",
        staging_dir=tmp_path / "staging",
    )
    staging.write_records(
        [ContractExpense(
            source_id="seao", source_content_hash="h", source_format="seao-xml",
            notice_number="1", amount=250.0, expense_date=date(2024, 7, 1),
            expense_publication_date=date(2024, 7, 2),
            description="Dépense supplémentaire excédant 10 %", supplier_name="A",
        )],
        dataset="expenses", source_id="seao", content_hash="h1",
        staging_dir=tmp_path / "staging",
    )

    out = tmp_path / "out"
    written = dashboard.build(out)
    return out, written


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class TestSummary:
    def test_total_excludes_losing_bids_and_non_dollar_units(self, staged):
        out, _ = staged
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        # 1000 + 500 only: the 9999 bid lost, the 88 is priced in points.
        assert summary["total_cad"] == 1500.0
        assert summary["all_bids"] == 4
        assert summary["winning_bids"] == 3
        assert summary["excluded_non_dollar"] == 1

    def test_counts(self, staged):
        out, _ = staged
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["awards"] == 2
        assert summary["suppliers"] == 2
        assert summary["overrun_total_cad"] == 250.0


class TestAggregates:
    def test_suppliers_ranked_by_value(self, staged):
        out, _ = staged
        rows = read_csv(out / "suppliers.csv")
        assert [r["supplier_name"] for r in rows] == ["A", "D"]
        assert float(rows[0]["total_cad"]) == 1000.0

    def test_direct_award_count_tracked(self, staged):
        out, _ = staged
        rows = {r["supplier_name"]: r for r in read_csv(out / "suppliers.csv")}
        assert int(rows["D"]["direct_awards"]) == 1
        assert int(rows["A"]["direct_awards"]) == 0

    def test_competition_by_year(self, staged):
        out, _ = staged
        rows = read_csv(out / "competition.csv")
        assert {r["method_key"] for r in rows} == {"open", "direct"}

    def test_overruns_join_to_the_award(self, staged):
        out, _ = staged
        rows = read_csv(out / "overruns.csv")
        assert len(rows) == 1
        assert rows[0]["notice_number"] == "1"
        assert float(rows[0]["overrun_cad"]) == 250.0
        assert float(rows[0]["overrun_pct"]) == 25.0

    def test_variance_awarded_versus_final(self, staged):
        out, _ = staged
        rows = read_csv(out / "variance.csv")
        assert float(rows[0]["awarded_cad"]) == 1000.0
        assert float(rows[0]["final_cad"]) == 1200.0
        assert float(rows[0]["delta_cad"]) == 200.0

    def test_every_expected_file_written(self, staged):
        out, written = staged
        for name in ("summary", "monthly", "suppliers", "buyers",
                     "competition", "categories", "regions", "overruns", "variance"):
            assert name in written, name


class TestDeduplication:
    """The analytical layer resolves identity economically, not by award_id.

    Measured on the full 17-year dataset, keying on the staged `dedupe_key`
    double-counted 20.4% of the total ($117.6B): SEAO's two eras republish the
    same award under different award_ids, and the NEQ is present in one era and
    absent in the other, so any key built from those fields changes shape at the
    era boundary and never matches itself.
    """

    def _build(self, tmp_path, monkeypatch, awards):
        monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "staging")
        monkeypatch.setattr(dashboard, "STAGING_DIR", tmp_path / "staging")
        for i, record in enumerate(awards):
            staging.write_records(
                [record], dataset="awards", source_id="seao", content_hash=f"h{i}",
                staging_dir=tmp_path / "staging",
            )
        out = tmp_path / "out"
        dashboard.build(out)
        return json.loads((out / "summary.json").read_text(encoding="utf-8"))

    def test_same_award_across_eras_counted_once(self, tmp_path, monkeypatch):
        """The real failure mode: one award, both eras, different shapes."""
        summary = self._build(tmp_path, monkeypatch, [
            award(source_format="seao-xml", award_id=None, supplier_neq="1143244383",
                  amount=129000.0, publication_date=date(2021, 3, 1)),
            award(source_format="ocds-json", award_id="1938505", supplier_neq=None,
                  amount=129000.0, publication_date=date(2023, 5, 1)),
        ])
        assert summary["awards"] == 1
        assert summary["total_cad"] == 129000.0

    def test_republished_award_counted_once(self, tmp_path, monkeypatch):
        """Monthly archives reissue a notice under a new award_id."""
        summary = self._build(tmp_path, monkeypatch, [
            award(award_id="604464", amount=1000.0, publication_date=date(2022, 3, 21)),
            award(award_id="2841695", amount=1000.0, publication_date=date(2022, 3, 21)),
        ])
        assert summary["awards"] == 1
        assert summary["total_cad"] == 1000.0

    def test_distinct_lots_same_day_are_kept(self, tmp_path, monkeypatch):
        """A notice can award the same supplier several lots on one day.

        Measured: 10,556 such groups carry distinct award_ids on a shared
        publication date. Collapsing on (notice, supplier, date) alone would
        erase $23.5B of genuine awards, so the amount stays in the key.
        """
        summary = self._build(tmp_path, monkeypatch, [
            award(award_id="A", amount=1000.0),
            award(award_id="B", amount=2500.0),
        ])
        assert summary["awards"] == 2
        assert summary["total_cad"] == 3500.0

    def test_the_richer_row_survives(self, tmp_path, monkeypatch):
        """When two rows tie, keep the one carrying the NEQ."""
        summary = self._build(tmp_path, monkeypatch, [
            award(supplier_neq=None, amount=500.0),
            award(supplier_neq="1143244383", amount=500.0),
        ])
        assert summary["awards"] == 1
        rows = read_csv((tmp_path / "out") / "suppliers.csv")
        assert rows[0]["neq"] == "1143244383"


class TestProgrammeDrilldownAggregate:
    def test_negative_revenue_rows_are_kept(self, tmp_path, monkeypatch):
        """External and internal revenues are booked as negative standard
        objects. Dropping them inflated the drill-down total by $16.3B and broke
        the reconciliation against the flat standard-object table."""
        import polars as pl

        from govbudget.models import BudgetLine

        monkeypatch.setattr(staging, "STAGING_DIR", tmp_path / "staging")
        monkeypatch.setattr(dashboard, "STAGING_DIR", tmp_path / "staging")

        def line(label, amount):
            return BudgetLine(
                source_id="gc_infobase", source_content_hash="h",
                jurisdiction="ca-federal", fiscal_year="2024-25",
                organization="DND", programme="Ready Land Forces",
                measure="expenditures", amount=amount,
                economic_category="personnel", economic_source_label=label,
                dimensions={"table": "programs_by_vote"},
            )

        staging.write_records(
            [line("Personnel", 1000.0), line("External revenues", -250.0)],
            dataset="budget_lines", source_id="gc_infobase", content_hash="h",
            staging_dir=tmp_path / "staging",
        )
        # Quebec side is required for build_budget to run at all.
        staging.write_records(
            [BudgetLine(
                source_id="qc_comptes_publics", source_content_hash="h",
                jurisdiction="qc", fiscal_year="2024-25", organization="Santé",
                measure="expenditures", amount=5.0, economic_category="transfers",
                dimensions={"table": "comptes_publics"},
            )],
            dataset="budget_lines", source_id="qc_comptes_publics", content_hash="h",
            staging_dir=tmp_path / "staging",
        )

        out = tmp_path / "out"
        dashboard.build_budget(out)
        frame = pl.read_csv(out / "program_drilldown.csv")
        assert frame["amount"].sum() == 750.0, "the negative offset must survive"
        assert frame.height == 2
