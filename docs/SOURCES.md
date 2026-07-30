# Source guide

Narrative companion to `sources/registry.json`. The registry is the machine-readable
truth; this explains what each source is *for* and how they fit together.

Sources are grouped into tiers by how central they are to the dashboard, not by
quality. A tier-3 source is not worse — it answers a different question.

---

## Tier 1 — The core four

These carry the load. None require an API key.

### USAspending.gov
The DATA Act's flagship: award-level detail on contracts, grants, loans, and direct
payments, plus agency budgetary resources broken to the federal account and object
class. If the dashboard answers "who received federal money," this is where it
comes from.

Reports **obligations**. Award coverage is reliable from FY2008; account-level from
FY2017. Historical rows are restated retroactively, so any cached extract needs an
as-of timestamp.

The useful search endpoints are POST with a JSON filter body, not GET — plan the
client accordingly.

### Treasury Fiscal Data
Three distinct products under one API, all keyless:

- **Monthly Treasury Statement (MTS)** — actual receipts and outlays by agency and
  budget function. This is the authoritative "money left the building" series and
  the anchor every outlay figure reconciles against.
- **Daily Treasury Statement (DTS)** — daily cash position and flows. Good for a
  live pulse tile; does not aggregate cleanly to fiscal-year budget totals because
  it is cash basis.
- **Debt & interest** — Debt to the Penny, average rates, interest expense.

One practical trap: pagination uses bracketed parameters (`page[size]`,
`page[number]`) that must be percent-encoded. Passing them raw to curl fails with a
"bad range in URL" error, and some HTTP clients will silently mangle them.

### OMB Historical Tables
The only consistent multi-decade series, running to FY1940 and in places to 1789.
Outlays by agency (Table 4.1), by function (3.1/3.2), receipts by source (2.1),
deficit (1.1). Includes forward projections from the President's Budget.

Spreadsheets, not an API, published annually with the Budget. whitehouse.gov
reorganizes these links between administrations — use GovInfo's BUDGET collection
for anything archival.

### Congressional Budget Office
Baseline projections, the long-term outlook, and legislative cost estimates. No
API; per-publication spreadsheets.

CBO matters because it is *independent of OMB*. Showing both projections side by
side, and the spread between them, is more informative than either alone.

---

## Tier 2 — Program, recipient, and audit detail

Where the accountability story lives.

**Federal Audit Clearinghouse** is the highest-value source in this tier for a
project named "audit." Every entity spending $750k+ in federal awards files a
Single Audit, and the FAC exposes findings, questioned costs, and material
weaknesses through a PostgREST-style API. It is the only source that systematically
links a specific federal award to a finding of misuse.

**SAM.gov** covers entity registrations, Assistance Listings, opportunities, and —
most usefully — the exclusions/debarment list. Cross-referencing active awards
against exclusions is a strong audit check. Access is the slowest to obtain of any
source here, so request it early.

**FPDS-NG** is the raw upstream feed USAspending's contract data derives from. More
timely and more granular, but ATOM XML with an idiosyncratic query language.
Reach for it only when USAspending's latency or aggregation genuinely falls short.

**PaymentAccuracy.gov** publishes agency improper-payment estimates. This is the
number most users arrive looking for, and the one most often misreported: an
improper payment failed a documentation or eligibility test, most are not fraud,
and some are *under*payments. The UI must say so wherever the figure appears.

**Oversight.gov** consolidates Inspector General reports across agencies —
the qualitative counterpart to FAC's structured findings.

**CMS Data** covers Medicare and Medicaid, roughly a quarter of federal outlays.
A dashboard that renders HHS as a single bar is hiding the largest story in the
budget.

**GovInfo** and **Congress.gov** supply the "why" layer: the appropriations acts
behind the accounts, and durable archival copies of budget documents.

---

## Tier 3 — Macro context

**BEA** places government spending in the national accounts, splitting federal from
state/local. It reconciles to OMB only after documented adjustments — different
basis, not a discrepancy. BEA is also the source of the GDP price deflator required
for any inflation-adjusted chart.

**FRED** is a convenience layer over Treasury, BEA, and CBO. Fine for prototyping,
but cite the primary source in the UI or the dashboard inherits FRED's revision lag.

**Census Annual Survey of State & Local Government Finances** is the only
nationally comparable state/local dataset. Its two-year publication lag rules it
out for anything "current," but nothing else lets you compare states like for like.

**OPM FedScope** covers federal workforce headcount and compensation — a large
share of discretionary spending that is invisible in award-level data.

---

## Tier 4 — Discovery

**Socrata Discovery API** is the practical route into state and local coverage.
Rather than hand-coding fifty state portals, search the catalog and normalize what
returns. Expect substantial per-publisher mapping work; schemas are inconsistent.

**Data.gov CKAN** is a metadata catalog, not a data source. Many listed links are
dead. Treat hits as leads to verify.

---

## Cross-cutting caveats

Recorded with severity levels in `sources/registry.json` under `caveats`. The two
critical ones:

**Obligations vs outlays.** USAspending and Treasury MTS measure different things
and must never be summed or plotted on a shared axis without explicit labels. This
is the most common way consolidated spending dashboards go wrong.

**Fiscal year.** The federal fiscal year runs October 1 to September 30, named for
the year it ends. Mixing fiscal and calendar years silently shifts a quarter of the
data. State fiscal years differ again — most run July to June, but Texas, Alabama,
Michigan, and New York do not.

Also significant: the DUNS-to-UEI identifier change in April 2022 breaks recipient
time series across that boundary; federal grants passed through states to
localities appear in multiple datasets and inflate naive cross-jurisdiction totals;
and USAspending account totals will not tie exactly to Treasury MTS, so the
residual should be displayed and alerted on only when it leaves its historical band.

---

## Suggested build order

1. **Treasury MTS** — smallest, keyless, authoritative. Establishes the outlay
   spine and a reconciliation target before anything else lands.
2. **USAspending agency budgetary resources** — attaches obligations to the same
   agency dimension, immediately exercising the obligations/outlays distinction.
3. **OMB Historical Tables** — gives the long-run trend and share-of-GDP views.
4. **FAC** — adds the audit dimension, and is the first source needing a key.

Anything beyond that depends on which questions the dashboard is meant to answer
first.
