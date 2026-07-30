# gov-budget-audit

A consolidated dashboard for government spending data that is otherwise scattered
across dozens of unrelated federal, state, and local systems.

The problem this addresses is not that the data is secret — nearly all of it is
public. It is that answering a simple question ("what did this agency actually
spend, and on what?") means reconciling an award database, a Treasury cash
statement, a spreadsheet published once a year by OMB, and an audit clearinghouse
that none of the others link to. Each uses different identifiers, a different
accounting basis, and a different definition of "spending."

## Status

Early. This repository currently contains the **source registry** — the inventory
of every system worth ingesting, with its endpoints, auth requirements, update
cadence, join keys, and known traps — plus a checker that verifies which sources
are actually reachable.

The registry is deliberately the first deliverable. Every downstream decision
(schema, refresh cadence, which comparisons are even valid) follows from what
these sources actually provide, and several of them cannot be joined to each
other without explicit reconciliation logic.

## Layout

```
sources/registry.json    Machine-readable inventory: 20 sources, 9 cross-cutting caveats
scripts/check_sources.py Probes every source, reports live/blocked/dead
docs/SOURCES.md          Narrative guide — what each source is for, and how they fit together
```

## Checking the sources

```bash
python3 scripts/check_sources.py                       # probe everything
python3 scripts/check_sources.py --tier 1              # just the core sources
python3 scripts/check_sources.py --write               # record results into the registry
python3 scripts/check_sources.py --report docs/SOURCE_STATUS.md
```

Standard library only — no install step.

Sources needing an API key are probed regardless; a 401 confirms the endpoint is
alive and that the key is the only thing missing. To promote those to full checks:

| Variable | Source | Where to get it |
| --- | --- | --- |
| `GOVINFO_API_KEY` | GovInfo | api.data.gov — one key covers GovInfo, Congress.gov, and FAC |
| `CONGRESS_API_KEY` | Congress.gov | api.data.gov |
| `FAC_API_KEY` | Federal Audit Clearinghouse | api.data.gov |
| `SAM_API_KEY` | SAM.gov | SAM.gov account; some endpoints need a granted role — request early |
| `BEA_API_KEY` | BEA | apps.bea.gov registration |
| `FRED_API_KEY` | FRED | fred.stlouisfed.org registration |
| `CENSUS_API_KEY` | Census | api.census.gov/data/key_signup.html |

The four highest-value sources — USAspending, Treasury Fiscal Data, OMB Historical
Tables, and CBO — need no key at all, so the project can go a long way before any
registration is required.

## The reconciliation problem

The single most important thing to get right, and the reason a "consolidated"
dashboard is harder than it looks: **the major sources measure different things
and will never agree.**

- **USAspending** reports *obligations* — money legally committed.
- **Treasury MTS** reports *outlays* — money that actually left the Treasury.
- **OMB Historical Tables** report *budget authority and outlays* on the budget basis.
- **BEA** reports *expenditures* on the national accounts basis.

A contract obligated in FY2024 may outlay over five subsequent years. Summing
across these sources, or charting them on a shared axis without labels, produces
numbers that are simply wrong. The design commitment here is that every figure
carries its measure and its as-of date, and that the residual between sources is
displayed rather than hidden — the gap is information, not error.

`sources/registry.json` records nine such caveats with severity levels, including
the DUNS-to-UEI identifier break in April 2022, pass-through double-counting
between federal and state datasets, and the distinction between improper payments
and fraud. These are ingest-layer requirements, not footnotes.

## Environment note

This repository was scaffolded in a sandbox whose egress policy denies every
government data host (`api.usaspending.gov`, `api.fiscaldata.treasury.gov`,
`api.census.gov`, `apps.bea.gov`, `api.congress.gov`, `api.fac.gov`,
`catalog.data.gov`, and others). Endpoint details in the registry were written
from documentation, and **every entry is marked `"verified": false`** until
`check_sources.py` confirms it from a network that can reach these hosts. Run it
with `--write` and the registry records what is genuinely live.
