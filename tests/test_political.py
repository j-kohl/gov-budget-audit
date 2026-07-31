"""Tests for the political-era reference data.

This is hand-maintained, so the tests guard two things: that the terms are
internally consistent, and that the fiscal-year arithmetic does not quietly
attribute a whole year to a government that held it for part.
"""

from datetime import date

import pytest

from govbudget import political


class TestTermIntegrity:
    def test_terms_are_contiguous_within_a_jurisdiction(self):
        """A gap would leave a chart span with no government."""
        for jurisdiction in ("ca-federal", "qc"):
            terms = political.terms_for(jurisdiction)
            for earlier, later in zip(terms, terms[1:], strict=False):
                assert earlier.end == later.start, (
                    f"{jurisdiction}: {earlier.leader} ends {earlier.end} but "
                    f"{later.leader} starts {later.start}"
                )

    def test_exactly_one_incumbent_per_jurisdiction(self):
        for jurisdiction in ("ca-federal", "qc"):
            open_terms = [t for t in political.terms_for(jurisdiction) if t.end is None]
            assert len(open_terms) == 1, jurisdiction

    def test_the_incumbent_is_last(self):
        for jurisdiction in ("ca-federal", "qc"):
            assert political.terms_for(jurisdiction)[-1].end is None

    def test_every_party_has_a_colour(self):
        for term in political.TERMS:
            assert term.party in political.PARTY_COLOUR, term.party

    def test_coverage_starts_before_the_earliest_data(self):
        """SEAO reaches 2009; no chart should have an unlabelled span."""
        for jurisdiction in ("ca-federal", "qc"):
            assert political.terms_for(jurisdiction)[0].start < date(2009, 1, 1)


class TestFiscalYearArithmetic:
    def test_bounds_run_april_to_april(self):
        assert political.fiscal_year_bounds("2015-16") == (
            date(2015, 4, 1), date(2016, 4, 1)
        )

    def test_a_settled_year_has_one_government(self):
        parts = political.overlap("2018-19", "ca-federal")
        assert len(parts) == 1
        assert parts[0]["leader"] == "Justin Trudeau"
        assert parts[0]["share"] == pytest.approx(1.0)

    def test_an_election_year_is_split_not_assigned(self):
        """2015-16 was Harper until 4 November, Trudeau after. Attributing the
        whole year to either is wrong."""
        parts = political.overlap("2015-16", "ca-federal")
        assert [p["leader"] for p in parts] == ["Stephen Harper", "Justin Trudeau"]
        assert sum(p["share"] for p in parts) == pytest.approx(1.0)
        assert parts[0]["share"] == pytest.approx(0.59, abs=0.01)

    def test_quebec_election_years_split_too(self):
        parts = political.overlap("2012-13", "qc")
        assert {p["leader"] for p in parts} == {"Jean Charest", "Pauline Marois"}

    def test_shares_always_sum_to_one(self):
        for year in [f"{y}-{str(y + 1)[-2:]}" for y in range(2009, 2025)]:
            for jurisdiction in ("ca-federal", "qc"):
                parts = political.overlap(year, jurisdiction)
                assert sum(p["share"] for p in parts) == pytest.approx(1.0), (
                    year, jurisdiction
                )


class TestAttribution:
    def test_dominant_government_wins_the_year(self):
        rows = political.year_attribution(["2015-16"], "ca-federal")
        assert rows[0]["leader"] == "Stephen Harper"
        assert rows[0]["split"] is True
        assert rows[0]["also"] == "Justin Trudeau"

    def test_settled_years_are_not_flagged_split(self):
        rows = political.year_attribution(["2018-19"], "ca-federal")
        assert rows[0]["split"] is False
        assert rows[0]["also"] == ""

    def test_every_row_carries_a_colour(self):
        rows = political.year_attribution(["2013-14", "2020-21"], "qc")
        assert all(r["colour"].startswith("#") for r in rows)


class TestBands:
    def test_open_terms_are_closed_at_the_horizon(self):
        """A rect needs a right edge; an incumbent has no end date."""
        bands = political.all_bands(horizon=date(2026, 7, 31))
        incumbent = next(b for b in bands if b["incumbent"])
        assert incumbent["end"] == "2026-07-31"

    def test_bands_are_emitted_unclipped(self):
        """Charts clip via their own domain, so one payload serves all of them."""
        bands = political.all_bands()
        assert any(b["start"] < "2009-01-01" for b in bands)

    def test_clipped_bands_flag_the_cut(self):
        """A band cut by the chart edge must not read as an election."""
        bands = political.as_bands("ca-federal", "2011-12", "2024-25")
        assert bands[0]["clipped_start"] is True
        assert bands[0]["start"] == "2011-04-01"

    def test_clipped_range_drops_terms_outside_it(self):
        bands = political.as_bands("qc", "2020-21", "2024-25")
        assert [b["leader"] for b in bands] == ["François Legault"]


class TestLeaderOn:
    def test_boundary_day_belongs_to_the_incoming_government(self):
        """Terms are half-open, so the handover day is not double-counted."""
        assert political.leader_on(date(2015, 11, 4), "ca-federal").leader == "Justin Trudeau"
        assert political.leader_on(date(2015, 11, 3), "ca-federal").leader == "Stephen Harper"

    def test_before_coverage_returns_none(self):
        assert political.leader_on(date(1990, 1, 1), "qc") is None
