# gov-budget-audit

A consolidated view of Canadian federal and Quebec government spending, built
from the public sources it is currently scattered across.

Nearly all of this data is already public. The problem is that answering a plain
question — what did this ministry spend, on what, and who got paid — means
reconciling a contract database, an expenditure budget published as a PDF, an
audited set of public accounts, and a federal open-data portal that shares no
identifiers with any of them.

## Status

The **SEAO ingester is built and tested**: Quebec public contracts, 2009 to
present, discovered through Données Québec and parsed from both the legacy XML
and the post-2021 OCDS JSON.

Also here: the [source registry](sources/registry.json) covering 12 federal and
Quebec sources with their access method and known traps, and the raw-store and
staging layers the remaining ingesters will reuse.

Not yet built: the PDF extractors (Quebec Budget de dépenses, Comptes publics,
Public Accounts of Canada), the federal ingesters, and the dashboard itself.

## Why Python rather than a Node stack

Roughly half the sources are PDFs — the Budget de dépenses, Comptes publics, and
Public Accounts of Canada — and `pdfplumber`/`Camelot` have no serious JS
equivalent. Reconciling two incompatible spending taxonomies is also far easier
in polars than in anything on the JS side.

The storage layer is Parquet plus DuckDB rather than Postgres. These are
analytical facts, not transactional app data: DuckDB queries the Parquet in
place, costs nothing to host, and a parser fix means re-running over the raw
store instead of writing a migration. Nothing here stops the staged output being
loaded into Postgres later if the dashboard wants it there.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,pdf]"
```

## Usage

```bash
govbudget sources                    # what is in the registry
govbudget caveats                    # cross-cutting data traps, by severity
govbudget check-sources --write      # probe reachability, record results

govbudget seao:discover              # list SEAO files published on Données Québec
govbudget seao:discover --era ocds --cadence hebdo
govbudget seao:ingest --era ocds --limit 5
govbudget seao:ingest --year 2019 --dry-run
govbudget seao:summary               # headline figures for what has been staged

govbudget seao:inspect path/to/file.xml   # report a file's real structure
```

Then query directly:

```sql
-- data/govbudget.duckdb
SELECT supplier_name, sum(amount) AS total, count(*) AS contracts
FROM seao_awards
WHERE award_date >= '2023-01-01'
GROUP BY 1 ORDER BY total DESC LIMIT 20;
```

## How the pipeline is arranged

```
Données Québec CKAN  ->  raw store  ->  parsers  ->  Parquet staging  ->  DuckDB
   (discovery)          (verbatim,      (XML |       (one file per      (views over
                       content-hash)     OCDS)        source hash)       parquet)
```

Raw files land verbatim, named by the SHA-256 of their bytes, and are never
mutated. Fetches are conditional on ETag and Last-Modified, so re-running only
transfers what changed. Because parsing reads from the raw store rather than the
network, a parser fix replays over the whole history without re-fetching — which
matters when a source publishes a decade of files.

## The unverified parts

**Every endpoint in this repository is unverified.** The environment this was
built in denies egress to every Canadian and Quebec government host
(`open.canada.ca`, `donneesquebec.ca`, `seao.ca`, `tbs-sct.canada.ca`,
`finances.gouv.qc.ca`, `quebec.ca`, `vgq.qc.ca`, `pbo-dpb.ca` — all 403 at the
proxy). Nothing was fetched, so nothing could be checked against reality.

What this means in practice:

- **The OCDS parser should be close to right.** It targets a published standard,
  and is tested against release packages, record packages, bare releases and
  newline-delimited JSON, including multi-supplier awards and value fallbacks.
- **The legacy SEAO XML field map is a guess.** SEAO's pre-2021 XML has no
  published schema. The parser resolves each field against a list of candidate
  tag spellings after normalizing away namespace, accents, case and punctuation,
  so correcting it is a one-line change to `FIELD_MAP`. Run
  `govbudget seao:inspect` on a real file first — it prints the actual element
  paths with sample values and flags every tag the map does not cover.
- **Dataset discovery is by search, not by hard-coded ID.** If the CKAN query
  fails to find SEAO, the error says so and points at the registry rather than
  failing silently.

Run `govbudget check-sources --write` from an unrestricted network and the
registry records what is genuinely reachable.

## Decisions worth making early

Recorded in full under `caveats` in the registry; `govbudget caveats` prints
them by severity. The one that shapes the schema:

**The federal and Quebec spending taxonomies do not map onto each other.** The
federal standard object / vote / program structure and Quebec's portefeuille /
mission / programme structure have no crosswalk that survives contact with the
detail. Either build two parallel fact tables joined only at a coarse function
level, or accept a lossy mapping into one. This is why `models.BudgetLine` holds
jurisdiction-specific dimensions in a `dimensions` dict rather than as columns —
it defers the decision without pretending it has been made.

Contract awards sidestep this entirely, which is the other reason SEAO was a
good place to start: an award has a supplier and an amount, and needs no
taxonomy reconciliation to be useful.

Two more worth knowing before charting anything: Estimates report *authorities*
and Public Accounts report *expenditures* (the gap is lapsed spending, and it is
real), and federal transfers to Quebec appear as both federal expenditure and
Quebec revenue, so summing the two levels double-counts the health transfer and
equalization.

## Licensing

Most of these sources are under the Open Government Licence — Canada or Québec,
which permits commercial reuse but requires attribution. Anything public-facing
needs a visible attribution statement per the licence terms.

## Tests

```bash
.venv/bin/python -m pytest
```

82 tests covering amount and date normalization across French and English
formats, both parsers, CKAN response handling, content-addressed storage with
conditional fetch, and the staging schema.
