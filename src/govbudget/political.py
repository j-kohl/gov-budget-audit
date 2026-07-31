"""Who held office, so yearly figures can be read in political context.

No portal publishes this — it is hand-maintained reference data, and it is
marked as such wherever it reaches the dashboard so nobody mistakes it for a
sourced figure.

Two things this module is careful about.

**A fiscal year is not a political year.** Both governments run April to March,
and elections do not. The 2015-16 federal year was Harper until 4 November and
Trudeau after, so attributing the whole year to either is wrong. Terms are
therefore stored as exact date ranges and `overlap` reports what share of a
fiscal year each government actually held. The dashboard draws bands positioned
by date rather than colouring whole-year bars.

**Holding office is not the same as causing the spending.** A year's
expenditure reflects budgets set earlier, statutory programmes running for
decades, and commitments made by predecessors. These bands are context for
reading a chart, not an attribution of responsibility, and the dashboard says so.

Current terms carry `end=None`. Verify the incumbent before relying on the most
recent band: this file was last checked against events to mid-2026.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Last date the incumbents in this file were verified.
LAST_VERIFIED = date(2026, 7, 31)


@dataclass(frozen=True)
class Term:
    """One government's time in office."""

    jurisdiction: str  # 'ca-federal' | 'qc'
    leader: str
    party: str
    party_short: str
    start: date
    #: None while in office.
    end: date | None = None

    def contains(self, day: date) -> bool:
        return self.start <= day and (self.end is None or day < self.end)


#: Party colours, muted deliberately. These are background bands behind data;
#: full-strength party colours would compete with the series in front of them.
PARTY_COLOUR = {
    "Conservative": "#8aa6c8",
    "Liberal": "#e2a3a3",
    "Coalition Avenir Québec": "#8fc7e8",
    "Parti Québécois": "#9fb6d4",
    "Parti libéral du Québec": "#e8b0b0",
}

#: Federal prime ministers. Deliberately starts before the earliest data in the
#: project (SEAO reaches 2009) so no chart has an unlabelled span.
FEDERAL_TERMS: tuple[Term, ...] = (
    Term("ca-federal", "Paul Martin", "Liberal", "Lib",
         date(2003, 12, 12), date(2006, 2, 6)),
    Term("ca-federal", "Stephen Harper", "Conservative", "Con",
         date(2006, 2, 6), date(2015, 11, 4)),
    Term("ca-federal", "Justin Trudeau", "Liberal", "Lib",
         date(2015, 11, 4), date(2025, 3, 14)),
    Term("ca-federal", "Mark Carney", "Liberal", "Lib",
         date(2025, 3, 14), None),
)

#: Quebec premiers.
QUEBEC_TERMS: tuple[Term, ...] = (
    Term("qc", "Jean Charest", "Parti libéral du Québec", "PLQ",
         date(2003, 4, 29), date(2012, 9, 19)),
    Term("qc", "Pauline Marois", "Parti Québécois", "PQ",
         date(2012, 9, 19), date(2014, 4, 23)),
    Term("qc", "Philippe Couillard", "Parti libéral du Québec", "PLQ",
         date(2014, 4, 23), date(2018, 10, 18)),
    Term("qc", "François Legault", "Coalition Avenir Québec", "CAQ",
         date(2018, 10, 18), None),
)

TERMS: tuple[Term, ...] = FEDERAL_TERMS + QUEBEC_TERMS


def terms_for(jurisdiction: str) -> tuple[Term, ...]:
    return tuple(t for t in TERMS if t.jurisdiction == jurisdiction)


def fiscal_year_bounds(fiscal_year: str) -> tuple[date, date]:
    """'2015-16' -> (2015-04-01, 2016-04-01), exclusive end.

    Both governments run April to March.
    """
    start_year = int(fiscal_year.split("-")[0])
    return date(start_year, 4, 1), date(start_year + 1, 4, 1)


def overlap(fiscal_year: str, jurisdiction: str) -> list[dict[str, object]]:
    """Which governments held office during a fiscal year, and for what share.

    Returns one entry per term overlapping the year, with `share` summing to 1
    across the year. An election year yields two entries rather than being
    assigned to a single government.
    """
    year_start, year_end = fiscal_year_bounds(fiscal_year)
    span = (year_end - year_start).days
    result: list[dict[str, object]] = []

    for term in terms_for(jurisdiction):
        start = max(term.start, year_start)
        end = min(term.end or year_end, year_end)
        days = (end - start).days
        if days <= 0:
            continue
        result.append({
            "leader": term.leader,
            "party": term.party,
            "party_short": term.party_short,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "share": days / span,
        })
    return result


def leader_on(day: date, jurisdiction: str) -> Term | None:
    for term in terms_for(jurisdiction):
        if term.contains(day):
            return term
    return None


def all_bands(horizon: date | None = None) -> list[dict[str, object]]:
    """Every term as a date range, unclipped.

    Charts with a continuous date axis clip these themselves via their scale
    domain, so emitting them unclipped means one payload serves every chart
    regardless of its range. An open-ended term is closed at `horizon` because
    a rect needs a right edge.
    """
    end_of_time = horizon or LAST_VERIFIED
    return [
        {
            "jurisdiction": term.jurisdiction,
            "leader": term.leader,
            "party": term.party,
            "party_short": term.party_short,
            "colour": PARTY_COLOUR.get(term.party, "#cccccc"),
            "start": term.start.isoformat(),
            "end": (term.end or end_of_time).isoformat(),
            "incumbent": term.end is None,
        }
        for term in TERMS
    ]


def year_attribution(fiscal_years: list[str], jurisdiction: str) -> list[dict[str, object]]:
    """Per-fiscal-year attribution, for charts with an ordinal year axis.

    Bands drawn by date need a continuous axis. Where a chart plots fiscal-year
    categories instead, each year gets the government that held office for most
    of it, plus a flag when the year was actually split by an election — so the
    chart can mark it rather than implying one government held the whole year.
    """
    rows: list[dict[str, object]] = []
    for fiscal_year in fiscal_years:
        parts = overlap(fiscal_year, jurisdiction)
        if not parts:
            continue
        dominant = max(parts, key=lambda p: p["share"])
        rows.append({
            "fiscal_year": fiscal_year,
            "jurisdiction": jurisdiction,
            "leader": dominant["leader"],
            "party": dominant["party"],
            "party_short": dominant["party_short"],
            "colour": PARTY_COLOUR.get(str(dominant["party"]), "#cccccc"),
            "share": round(float(dominant["share"]), 4),
            "split": len(parts) > 1,
            "also": " / ".join(
                str(p["leader"]) for p in parts if p is not dominant
            ),
        })
    return rows


def as_bands(jurisdiction: str, first_year: str, last_year: str) -> list[dict[str, object]]:
    """Terms clipped to a chart's date range, ready to draw as bands.

    Clipping matters: an open-ended term drawn to `None` has no right edge, and
    a term starting decades before the data would stretch the axis.
    """
    range_start, _ = fiscal_year_bounds(first_year)
    _, range_end = fiscal_year_bounds(last_year)

    bands: list[dict[str, object]] = []
    for term in terms_for(jurisdiction):
        start = max(term.start, range_start)
        end = min(term.end or range_end, range_end)
        if end <= start:
            continue
        bands.append({
            "jurisdiction": jurisdiction,
            "leader": term.leader,
            "party": term.party,
            "party_short": term.party_short,
            "colour": PARTY_COLOUR.get(term.party, "#cccccc"),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "incumbent": term.end is None,
            #: True when the band is cut by the chart edge rather than by an
            #: actual change of government — the label should not imply an
            #: election happened there.
            "clipped_start": term.start < range_start,
            "clipped_end": term.end is not None and term.end > range_end,
        })
    return bands
