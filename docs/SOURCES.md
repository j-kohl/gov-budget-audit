# Source guide

Narrative companion to [`sources/registry.json`](../sources/registry.json). The
registry is the machine-readable truth; this explains what each source is *for*
and what it will cost to ingest.

The field that matters most for planning is `access`:

| access | meaning | effort |
| --- | --- | --- |
| `ckan` | CKAN Action API, discoverable and incremental | low |
| `bulk` | published bulk files, no crawling needed | low |
| `crawl` | walk a publications index, then extract | medium |
| `pdf` | table extraction with per-document templates | high |

Roughly 80% of what this project needs is `ckan` or `bulk`. Scraping HTML would
be slower, more fragile, and produce worse data than the files already published.

---

## Quebec

### SEAO — public contracts *(built)*
Every contract awarded in Quebec since 2009: ministries, the education network,
health and social services, and municipalities. Published through Données Québec,
not scraped from seao.ca.

Two eras meet in March 2021. Before it, yearly XML with no published schema;
after, JSON based on the Open Contracting Data Standard. Weekly (`hebdo_`) and
monthly (`mensuel_`) drops carry recent periods.

This was the first ingester because it is the highest-value dataset that needs no
PDF work, and because contract awards need no taxonomy reconciliation to be
useful — an award has a supplier and an amount.

Worth doing once: ingest the overlap window from both sides and diff it. The
schema change was not purely an encoding change, and a unified contract series
across that boundary should not be trusted until someone has checked.

### Budget de dépenses (Conseil du trésor)
Quebec expenditure broken down by portefeuille ministériel and by programme —
the source for program-level Quebec spending. PDF, but the volumes are laid out
consistently across years, so templated extraction is viable. Look for Excel
annexes before writing a parser.

### Plan budgétaire and Comptes publics
The annual budget and the audited actuals. Setting one against the other gives
budget-versus-actual variance by portefeuille, which is among the more useful
views the dashboard can offer.

### Vérificateur général du Québec
Audits of where spending went wrong — the qualitative counterpart to the
expenditure data. Low volume: crawl the publications index, then extract.

### Données Québec
The portal itself. Same CKAN API shape as open.canada.ca, so one client covers
both — see `govbudget/ckan.py`.

---

## Federal

### GC InfoBase
The best federal starting point, and no scraping is needed: the entire backing
dataset is published as CSVs on open.canada.ca under dataset
`a35cf382-690c-4221-a971-cf0fd189a46f`, which indexes all of them. Authorities,
expenditures, spending by vote, FTEs, program-level results.

InfoBase restates its historical series to absorb organizational mergers and
renames, which makes it more comparable across years than raw Public Accounts —
and means the two must not be mixed in one series.

### Proactive disclosure — contracts and grants
Federal contracts over $10k and grant/contribution awards, quarterly, via CKAN.
The federal counterpart to SEAO.

One important limit: transfer payments are about 60% of federal spending, but
this dataset covers only the grants and contributions slice. The large statutory
transfers — OAS/GIS, the Canada Health Transfer — appear only in Estimates and
Public Accounts. A dashboard built on disclosure data alone understates federal
spending badly.

### Estimates and Public Accounts
Estimates are what Parliament authorizes; Public Accounts are the audited
actuals. Estimates are available as CSV; Public Accounts are PDF but overlap
heavily with the InfoBase CSVs — check there before doing extraction work.

### Parliamentary Budget Officer
Independent analysis, generally more readable than the source documents. Low
volume, easy to crawl.

---

## Ingest order

1. **SEAO** — done. Highest value, no PDF work, no taxonomy problem.
2. **GC InfoBase CSVs** — largest federal win per unit of effort, and the
   federal expenditure spine everything else hangs off.
3. **Federal proactive disclosure** — contracts and grants, so both
   jurisdictions have comparable award-level data.
4. **Quebec Budget de dépenses** — the first PDF work, and the point at which
   the taxonomy decision has to be made.

---

## Cross-cutting caveats

Recorded with severity in the registry; `govbudget caveats` prints them. The
three critical ones:

**Taxonomy mismatch.** Federal standard object / vote / program and Quebec
portefeuille / mission / programme do not map onto each other. Decide between
two parallel fact tables and one lossy unified table before writing the schema.

**Organizational churn.** Departments merge, split and get renamed. InfoBase
restates history for this; Public Accounts does not. Mixing the two produces
phantom jumps at reorganization boundaries.

**Authorities versus expenditures.** Estimates report what was approved, Public
Accounts what was spent. The difference is lapsed spending — real, and worth
surfacing rather than hiding.

Also significant: federal transfers to Quebec are both federal expenditure and
Quebec revenue, so naive consolidation double-counts them; federal vendor names
are free text with no business number while SEAO carries the NEQ, so
cross-jurisdiction supplier matching needs a confidence score; and Quebec sources
are French-first, so keys must be normalized or joins fail silently on accents.
