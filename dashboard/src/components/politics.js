// Marks for showing which government held office behind a time series.
//
// Two shapes, because the charts have two kinds of x axis:
//
//   dateBands()  for a continuous date scale — real rects positioned by date,
//                so an election lands where it actually happened
//   yearStripe() for an ordinal fiscal-year scale, where a date rect cannot be
//                positioned; one cell per year, marked when an election split it
//
// Both are background context. They are drawn under the data, in muted colours,
// and neither implies the government in office caused the spending.

import * as Plot from "npm:@observablehq/plot";
import * as d3 from "npm:d3";

const parseDate = (s) => new Date(`${s}T00:00:00`);

/** Terms for one jurisdiction, as Date objects. */
export function bandsFor(eras, jurisdiction) {
  return eras.bands
    .filter((b) => b.jurisdiction === jurisdiction)
    .map((b) => ({...b, start: parseDate(b.start), end: parseDate(b.end)}));
}

export function yearsFor(eras, jurisdiction) {
  return eras.years.filter((y) => y.jurisdiction === jurisdiction);
}

/**
 * Background bands for a chart with a continuous date x scale.
 * Returns marks to place first in the `marks` array, so data draws on top.
 */
export function dateBands(eras, jurisdiction, {label = true} = {}) {
  const bands = bandsFor(eras, jurisdiction);
  const marks = [
    Plot.rect(bands, {
      x1: "start", x2: "end",
      fill: "colour", fillOpacity: 0.22,
      // No inset: adjacent terms must meet exactly at the election date, or a
      // gap appears where there was no gap in government.
      inset: 0
    }),
    Plot.ruleX(bands.map((b) => b.start), {
      stroke: "currentColor", strokeOpacity: 0.25, strokeDasharray: "2,3"
    })
  ];
  if (label) {
    marks.push(
      Plot.text(bands, {
        x: (d) => new Date((d.start.getTime() + d.end.getTime()) / 2),
        frameAnchor: "top",
        dy: 6,
        text: "party_short",
        fill: "currentColor",
        fillOpacity: 0.55,
        fontSize: 10
      })
    );
  }
  return marks;
}

/**
 * A one-row stripe of party colours for a chart with an ordinal fiscal-year x
 * scale. `y` places it; pass a value just below the data's zero line.
 *
 * Election years are hatched rather than shown as a solid colour, because the
 * year genuinely belonged to two governments and picking one would be a claim
 * the data does not support.
 */
export function yearStripe(eras, jurisdiction, {y = 0, height = 1} = {}) {
  const years = yearsFor(eras, jurisdiction);
  return [
    Plot.cell(years, {
      x: "fiscal_year",
      fill: "colour",
      fillOpacity: (d) => (d.split ? 0.45 : 0.8),
      // A per-datum null on a scaled channel makes Plot drop that row entirely,
      // which silently rendered only the election years. Stroke is constant and
      // the opacity does the work instead.
      stroke: "currentColor",
      strokeOpacity: (d) => (d.split ? 0.55 : 0),
      strokeDasharray: "2,2",
      inset: 0.5,
      title: (d) =>
        d.split
          ? `${d.fiscal_year} — ${d.leader} (${Math.round(d.share * 100)}% de l'année), puis ${d.also}`
          : `${d.fiscal_year} — ${d.leader}`
    })
  ];
}

/** Distinct parties present, for a legend. */
export function partyLegend(eras, jurisdiction) {
  const seen = new Map();
  for (const b of eras.bands.filter((b) => b.jurisdiction === jurisdiction)) {
    if (!seen.has(b.party)) seen.set(b.party, b.colour);
  }
  return Array.from(seen, ([party, colour]) => ({party, colour}));
}

/** Leaders in office during a range, for prose. */
export function leadersBetween(eras, jurisdiction, from, to) {
  const a = parseDate(from), b = parseDate(to);
  return bandsFor(eras, jurisdiction)
    .filter((d) => d.end > a && d.start < b)
    .map((d) => d.leader);
}

export const formatShare = (d) => d3.format(".0%")(d);
