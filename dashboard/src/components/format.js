// Shared formatters. Quebec data, so French-Canadian locale throughout:
// space-grouped thousands and a comma decimal mark, matching how these figures
// appear in the source documents.

const cad = new Intl.NumberFormat("fr-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

const count = new Intl.NumberFormat("fr-CA");

const pct = new Intl.NumberFormat("fr-CA", {maximumFractionDigits: 1});

/** Full dollar amount, no decimals. */
export const money = (value) => (value == null ? "—" : cad.format(value));

/** Compact dollars for axis ticks and dense tables: 1,2 G$ / 340 M$ / 12 k$. */
export function moneyShort(value) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${pct.format(value / 1e9)} G$`;
  if (abs >= 1e6) return `${pct.format(value / 1e6)} M$`;
  if (abs >= 1e3) return `${Math.round(value / 1e3)} k$`;
  return cad.format(value);
}

export const number = (value) => (value == null ? "—" : count.format(value));

export const percent = (value) => (value == null ? "—" : `${pct.format(value)} %`);

/** Truncate long free-text titles without cutting mid-word where avoidable. */
export function truncate(text, max = 70) {
  if (!text) return "";
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${space > max * 0.6 ? cut.slice(0, space) : cut}…`;
}

/** Consistent colours for the competitiveness dimension across every page. */
export const competitionColor = {
  domain: ["Appel d'offres public", "Sur invitation", "Gré à gré", "Autre"],
  range: ["#4269d0", "#97bbf5", "#efb118", "#9498a0"],
};
