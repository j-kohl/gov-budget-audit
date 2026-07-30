# gov-budget-audit

A consolidated view of Canadian federal and Quebec government spending, built
from the public sources it is currently scattered across.

Nearly all of this data is already public. The problem is that answering a plain
question — what did this ministry spend, on what, and who got paid — means
reconciling a contract database, an expenditure budget published as a PDF, an
audited set of public accounts, and a federal open-data portal that shares no
identifiers with any of them.

## Status

The **SEAO pipeline and dashboard are built and running on the full 17-year
history**: 1.70M staged award rows, resolving to **732,012 awards worth
$463.0B** across 153,249 suppliers and 2,486 public bodies, plus $2.84B of
declared cost overruns.

All 12 sources in the [registry](sources/registry.json) have been probed and
respond. The dashboard is a static Observable Framework site under
[`dashboard/`](dashboard/).

Not yet built: the PDF extractors (Quebec Budget de dépenses, Comptes publics,
Public Accounts of Canada) and the federal ingesters. **SEAO covers contracts,
not total public spending** — transfer payments, salaries and debt service are
all outside it. See the dashboard's *Sources* page.

## The dashboard

```bash
govbudget dashboard:data          # aggregate the staged Parquet
cd dashboard && npm install && npm run dev
```

Four pages: overview, who gets paid, competition (public tender vs gré à gré),
and cost overruns. It reads precomputed aggregates rather than querying at run
time, so `npm run build` produces a static site that hosts anywhere.

The aggregates are byte-reproducible: money is summed as `DECIMAL` and every
window function and `LIMIT` carries a total ordering, so re-running on unchanged
data produces identical files.

## Why Python rather than a Node stack

Roughly half the remaining sources are PDFs — the Budget de dépenses, Comptes
publics, and Public Accounts of Canada — and `pdfplumber`/`Camelot` have no
serious JS equivalent. Reconciling two incompatible spending taxonomies is also
far easier in polars.

Storage is Parquet plus DuckDB rather than Postgres. These are analytical facts,
not transactional app data: DuckDB queries the Parquet in place, costs nothing
to host, and a parser fix means re-running over the raw store instead of writing
a migration. The staged output still loads into Postgres later if the dashboard
wants it there.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,pdf]"    # .venv/Scripts/pip on Windows
```

## Usage

```bash
govbudget sources                     # what is in the registry
govbudget caveats                     # data traps, by severity
govbudget check-sources --write       # probe reachability, record results

govbudget seao:discover               # list SEAO files on Données Québec
govbudget seao:discover --era xml --cadence annuel
govbudget seao:ingest --era ocds --cadence mensuel --limit 3
govbudget seao:ingest --era xml --year 2019
govbudget seao:summary                # headline figures for what is staged

govbudget seao:inspect path/to/file.xml   # report a file's real structure
```

Then query:

```sql
-- data/govbudget.duckdb
-- seao_awards_won is the view to use for money: winners only, dollars only.
SELECT supplier_name, count(*) AS contracts, sum(amount) AS total
FROM seao_awards_won
WHERE award_date >= '2024-01-01'
GROUP BY 1 ORDER BY total DESC LIMIT 20;

-- Cost overruns: supplementary spending against the original award.
SELECT e.notice_number, a.buyer_name, a.amount AS awarded,
       sum(e.amount) AS overrun,
       round(100.0 * sum(e.amount) / nullif(a.amount, 0), 1) AS pct
FROM seao_expenses e
JOIN seao_awards_won a USING (notice_number)
WHERE a.amount > 0
GROUP BY 1, 2, 3 ORDER BY overrun DESC LIMIT 20;
```

Views: `seao_awards` (every bidder), `seao_awards_won` (winners, CAD only),
`seao_finals` (settled amounts), `seao_expenses` (overruns).

## What SEAO actually publishes

Verified against the live dataset — 418 resources:

| | |
| --- | --- |
| `Année 2009` … `Année 2020` | yearly XML archives (zip) |
| `Janvier 2021` … `Mai 2024` | monthly XML archives, named in French |
| `mensuel_YYYYMMDD_YYYYMMDD` | monthly OCDS JSON, from June 2021 |
| `hebdo_YYYYMMDD_YYYYMMDD` | weekly OCDS JSON |
| 3 PDFs | the XML and JSON format specifications, and an FAQ |

**XML and JSON overlap** from June 2021 to May 2024, so ingesting both
double-counts. Ingest one era at a time.

**Each XML archive holds six files**, and they are three different facts:

- `Avis_*.xml` — notices, the buyer, and **every bidder**
- `Contrats_*.xml` — the final settled amount
- `Depenses_*.xml` — spending beyond the original contract
- plus a `*Revisions` file for each, skipped by default since they restate
  records already present

## The 20% that was counted twice

The single largest correctness issue found, and it only became visible once the
whole history was loaded. Keying awards on what each file said — notice, award
id, supplier — double-counted **20.4% of the total, $117.6B of $576.6B**.

SEAO's two eras overlap in *content*, not just in file dates. Splitting the
backfill at 2021-05 stops the files overlapping, but OCDS releases still
republish awards the XML era already carried, under a new `award_id`. Worse, the
NEQ is present in one era and absent in the other, so a key built from either
field changes shape at the boundary and never matches itself.

Identity is therefore resolved economically — one notice paying one supplier one
amount on one date — in the analytical layer, not at ingest. Staging stays
faithful to what each file said; the resolution can be revised without
re-parsing 17 years of archives.

That leaves the amount in the key deliberately. Collapsing on notice+supplier+date
alone would look tidier but erase $23.5B of genuine awards: measurement showed
10,556 groups where one notice awards the same supplier several distinct lots on
the same day, each with its own `award_id`.

## Three ways to get the numbers wrong

Each of these was found by checking real files against the published
specification, and each silently corrupts a spending total.

**1. `<fournisseurs>` lists every bidder, not just the winner.** Only
`<adjudicataire>1</adjudicataire>` is an award. In May 2024 that is 6,276
winners out of 11,350 bidder rows — counting all of them overstates spending by
about 80%. Both parsers set `is_winner`; every total must filter on it. (OCDS
publishes winners only, so those rows are marked `is_winner = true` to keep one
filter working across both eras.)

**2. Amounts are not always dollars.** `<montantssoumisunite>` can mean `$/km`,
`$/hour`, `%`, `points` or `$US`. Summing without filtering adds percentages to
dollars. Only units 0 and 1 are plain CAD, materialized as `is_summable`.

Unit `0` is not in the published specification but is the most common value in
the data. It is dollars: cross-checking those rows against the independently
published final amounts in `Contrats_*.xml` gives a median ratio of 1.000,
identical to unit 1. Treating it as unknown would discard most of the money.

**3. `0.000000` means "not applicable".** `<montanttotalcontrat>` is usually
zero while `<montantcontrat>` holds the real figure, so ordinary field-preference
ordering picks zero.

The OCDS side has its own conventions: the SEAO notice number is in the **OCID**
(`ocds-ec9k95-1740136`), not `tender.id` — which is the buyer's own reference —
and the NEQ is in `parties[].details.neq`, not the standard `identifier` object.
Getting either wrong breaks the join between the two eras.

## How the pipeline is arranged

```
Données Québec CKAN  ->  raw store  ->  parsers  ->  Parquet staging  ->  DuckDB
   (discovery)          (verbatim,      (XML |       (awards, finals,   (views over
                       content-hash)     OCDS)        expenses)          parquet)
```

Raw files land verbatim, named by the SHA-256 of their bytes, and are never
mutated. Fetches are conditional on ETag and Last-Modified. Because parsing reads
from the raw store rather than the network, a parser fix replays over the whole
history without re-fetching — which matters when a source publishes 15 years of
archives.

## A note on TLS

Some Government of Canada hosts — `tpsgc-pwgsc.gc.ca` among them — serve
certificates under Entrust roots. Mozilla distrusted Entrust in 2024 and certifi
mirrors Mozilla, so **certifi ships no Entrust roots at all** and Python cannot
reach these hosts even though browsers can. The project defers to the OS trust
store via `truststore`. Verification is never disabled.

## Decisions worth making early

`govbudget caveats` prints all 16 by severity. The one that shapes the schema:

**The federal and Quebec spending taxonomies do not map onto each other.** The
federal standard object / vote / program structure and Quebec's portefeuille /
mission / programme structure have no crosswalk that survives contact with the
detail. Either build two parallel fact tables joined only at a coarse function
level, or accept a lossy mapping into one. `models.BudgetLine` holds
jurisdiction-specific dimensions in a `dimensions` dict rather than as columns,
so it defers the decision without pretending it has been made.

Contract awards sidestep this, which is the other reason SEAO was a good place to
start: an award has a supplier and an amount, and needs no taxonomy
reconciliation to be useful.

Also worth knowing before charting: Estimates report *authorities* and Public
Accounts report *expenditures* (the gap is lapsed spending, and it is real);
federal transfers to Quebec appear as both federal expenditure and Quebec
revenue, so summing the two levels double-counts the health transfer and
equalization; and the two SEAO eras label procurement methods differently, so
grouping by method across the boundary splits one category in two until a
crosswalk is applied.

## Licensing

Most of these sources are under the Open Government Licence — Canada or Québec,
which permits commercial reuse but requires attribution. Anything public-facing
needs a visible attribution statement per the licence terms.

## Tests

```bash
.venv/bin/pytest
```

105 tests. The parser fixtures reproduce the real published schema — the 0/1
flags, the `0.000000` convention, French number formatting, and the OCDS
publisher conventions — rather than an invented one.
