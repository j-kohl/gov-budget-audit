"""Command-line interface.

    govbudget sources                 list the registry
    govbudget caveats                 list the cross-cutting data caveats
    govbudget check-sources           probe every source for reachability
    govbudget seao:discover           list SEAO files published on Données Québec
    govbudget seao:inspect FILE       report the structure of a SEAO XML file
    govbudget seao:ingest             fetch, parse and stage SEAO contracts
    govbudget seao:summary            headline figures for what has been staged
    govbudget infobase:ingest         fetch, parse and stage GC InfoBase
    govbudget infobase:summary        headline federal figures
    govbudget qc:ingest               fetch, parse and stage the Budget de dépenses
    govbudget compare                 federal vs Quebec on the shared spine
    govbudget dashboard:data          build the aggregates the dashboard reads
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import registry, staging
from .storage import RawStore

log = logging.getLogger("govbudget")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


# -- commands -------------------------------------------------------------


def cmd_sources(args: argparse.Namespace) -> int:
    entries = registry.sources()
    if args.jurisdiction:
        entries = [s for s in entries if s.jurisdiction == args.jurisdiction]
    if args.access:
        entries = [s for s in entries if s.access == args.access]

    width = max((len(s.id) for s in entries), default=10)
    for source in sorted(entries, key=lambda s: (s.tier, s.jurisdiction, s.id)):
        flag = "verified" if source.verified else "unverified"
        print(f"  T{source.tier}  {source.id:<{width}}  {source.access:<6}  {flag:<10}  {source.name}")
    print(f"\n{len(entries)} source(s)", file=sys.stderr)
    return 0


def cmd_caveats(args: argparse.Namespace) -> int:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for caveat in sorted(registry.caveats(), key=lambda c: order.get(c.severity, 9)):
        print(f"[{caveat.severity.upper()}] {caveat.id}\n    {caveat.summary}\n")
    return 0


def cmd_check_sources(args: argparse.Namespace) -> int:
    """Probe each source's portal or API for reachability."""
    import httpx

    from .http import build_client

    entries = registry.sources()
    if args.id:
        entries = [s for s in entries if s.id in set(args.id)]

    results: list[tuple[str, str, str]] = []
    with build_client(timeout=30.0) as client:
        for source in entries:
            url = source.ckan_api and f"{source.ckan_api}/status_show" or source.portal
            if not url:
                results.append((source.id, "skipped", "no URL in registry"))
                continue
            try:
                response = client.get(url)
                status = "live" if response.status_code < 400 else "error"
                results.append((source.id, status, f"HTTP {response.status_code}"))
            except httpx.ProxyError as exc:
                results.append((source.id, "blocked", f"egress policy: {exc}"))
            except httpx.ConnectError as exc:
                # A certificate failure means the host answered — it is a trust
                # problem, not a dead endpoint, and needs a different fix.
                kind = "tls" if "CERTIFICATE" in str(exc).upper() else "dead"
                results.append((source.id, kind, f"{type(exc).__name__}: {exc}"))
            except httpx.HTTPError as exc:
                results.append((source.id, "dead", f"{type(exc).__name__}: {exc}"))

    width = max((len(r[0]) for r in results), default=10)
    for source_id, status, detail in results:
        print(f"  {status:<8} {source_id:<{width}}  {detail}")

    live = sum(1 for _, status, _ in results if status == "live")
    print(f"\n{live}/{len(results)} live", file=sys.stderr)

    if args.write:
        data = registry.load_registry()
        live_ids = {sid for sid, status, _ in results if status == "live"}
        probed = {sid for sid, _, _ in results}
        for entry in data["sources"]:
            if entry["id"] in probed:
                entry["verified"] = entry["id"] in live_ids
        data["last_verified"] = datetime.now(UTC).isoformat(timespec="seconds")
        registry.REGISTRY_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"updated {registry.REGISTRY_PATH}", file=sys.stderr)

    return 0 if live == len(results) else 1


def cmd_seao_discover(args: argparse.Namespace) -> int:
    from .sources import seao

    package, resources = seao.discover()
    print(f"dataset: {package.title or package.name}  ({len(resources)} resources)\n")

    selected = seao.select(resources, era=args.era, cadence=args.cadence, year=args.year,
                           since=args.since, until=args.until)
    for item in selected:
        print(f"  {item}")
        if args.urls:
            print(f"      {item.url}")

    eras = {}
    for item in resources:
        eras[item.era] = eras.get(item.era, 0) + 1
    print(f"\n{len(selected)} shown; by era: {eras}", file=sys.stderr)
    return 0


def cmd_seao_inspect(args: argparse.Namespace) -> int:
    """Report the real structure of a SEAO XML file, to correct the field map."""
    from .parsers import seao_xml

    data = Path(args.file).read_bytes()
    report = seao_xml.inspect_structure(data)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"root: {report.get('root')}")
    print(f"record tags present: {report.get('record_tags_present')}\n")
    print("paths:")
    for entry in report.get("paths", [])[: args.limit]:
        samples = "; ".join(entry["samples"][:2])
        print(f"  {entry['count']:>8}  {entry['path']}")
        if samples:
            print(f"            e.g. {samples}")

    unmapped = report.get("unmapped_leaf_tags") or []
    if unmapped:
        print(f"\ntags not covered by FIELD_MAP ({len(unmapped)}):")
        for tag in unmapped[: args.limit]:
            print(f"  {tag}")
    return 0


def cmd_seao_ingest(args: argparse.Namespace) -> int:
    from .sources import seao

    package, resources = seao.discover()
    selected = seao.select(
        resources, era=args.era, cadence=args.cadence, year=args.year,
        since=args.since, until=args.until, limit=args.limit,
    )
    if not selected:
        print("no resources matched the filters", file=sys.stderr)
        return 2

    print(f"ingesting {len(selected)} of {len(resources)} resources from {package.name}", file=sys.stderr)

    if args.dry_run:
        for item in selected:
            print(f"  would fetch {item}\n      {item.url}")
        return 0

    totals = {"awards": 0, "finals": 0, "expenses": 0}
    written: list[Path] = []
    with RawStore() as store:
        for _item, entry, bundle in seao.ingest(
            selected, store=store, force=args.force, include_revisions=args.include_revisions
        ):
            for dataset, records in zip(("awards", "finals", "expenses"), bundle, strict=True):
                totals[dataset] += len(records)
                path = staging.write_records(
                    records,
                    dataset=dataset,
                    source_id=seao.SOURCE_ID,
                    content_hash=entry.content_hash,
                )
                if path:
                    written.append(path)

    summary = ", ".join(f"{count:,} {name}" for name, count in totals.items())
    print(f"\n{summary} from {len(written)} staged file(s)", file=sys.stderr)

    if written:
        db = staging.load_duckdb(source_id=seao.SOURCE_ID)
        print(f"duckdb views seao_awards / seao_awards_won / seao_finals / "
              f"seao_expenses ready at {db}", file=sys.stderr)
    return 0


def cmd_seao_summary(args: argparse.Namespace) -> int:
    datasets = [args.dataset] if args.dataset else list(staging.DATASETS)
    found = False
    for dataset in datasets:
        frame = staging.load(dataset, "seao")
        if frame.is_empty():
            continue
        found = True
        if args.dedupe:
            frame = staging.deduplicate(frame)
        print(f"\n{dataset}:")
        for key, value in staging.summarize(frame).items():
            if isinstance(value, float):
                print(f"  {key:<26} {value:,.2f}")
            else:
                print(f"  {key:<26} {value}")

    if not found:
        print("nothing staged yet — run `govbudget seao:ingest`", file=sys.stderr)
        return 1
    return 0


def cmd_infobase_ingest(args: argparse.Namespace) -> int:
    from .sources import infobase

    total = 0
    written: list[Path] = []
    with RawStore() as store:
        for table, entry, lines in infobase.ingest(store=store, force=args.force):
            total += len(lines)
            path = staging.write_records(
                lines, dataset="budget_lines", source_id=infobase.SOURCE_ID,
                content_hash=entry.content_hash,
            )
            if path:
                written.append(path)
            print(f"  {table.key:<18} {len(lines):>8,} lines", file=sys.stderr)

    print(f"\n{total:,} budget lines from {len(written)} file(s)", file=sys.stderr)
    return 0 if total else 1


def cmd_infobase_summary(args: argparse.Namespace) -> int:
    import polars as pl

    from . import taxonomy
    from .sources import infobase

    frame = staging.load("budget_lines", infobase.SOURCE_ID)
    if frame.is_empty():
        print("nothing staged — run `govbudget infobase:ingest`", file=sys.stderr)
        return 1

    year = args.year or frame.filter(
        pl.col("measure") == "expenditures"
    )["fiscal_year"].max()
    slice_ = frame.filter(
        (pl.col("fiscal_year") == year)
        & (pl.col("measure") == "expenditures")
        & (pl.col("dimensions").str.contains("standard_object"))
    )
    print(f"federal expenditures by economic category, {year}:\n")
    grouped = (
        slice_.group_by("economic_category")
        .agg(pl.col("amount").sum().alias("total"))
        .sort("total", descending=True)
    )
    for row in grouped.iter_rows(named=True):
        verdict = taxonomy.comparability(row["economic_category"] or "other")
        print(f"  ${row['total']/1e9:>8.1f}B  {row['economic_category']:<13} "
              f"[{verdict.level}]")
    print(f"\n  ${grouped['total'].sum()/1e9:>8.1f}B  total", file=sys.stderr)
    return 0


def cmd_qc_ingest(args: argparse.Namespace) -> int:
    from .sources import qc_depenses

    total = 0
    written: list[Path] = []
    with RawStore() as store:
        for resource, entry, lines in qc_depenses.ingest(
            store=store, years=args.years, force=args.force
        ):
            total += len(lines)
            path = staging.write_records(
                lines, dataset="budget_lines", source_id=qc_depenses.SOURCE_ID,
                content_hash=entry.content_hash,
            )
            if path:
                written.append(path)
            print(f"  {(resource.name or '')[:46]:<46} {len(lines):>7,} lines", file=sys.stderr)

    print(f"\n{total:,} budget lines from {len(written)} file(s)", file=sys.stderr)
    return 0 if total else 1


def cmd_compare(args: argparse.Namespace) -> int:
    """Federal vs Quebec on the dimensions the taxonomy says are shared."""
    import polars as pl

    from . import taxonomy

    fed = staging.load("budget_lines", "gc_infobase")
    qc = staging.load("budget_lines", "qc_budget_depenses")
    if fed.is_empty() or qc.is_empty():
        print("need both: run `govbudget infobase:ingest` and `govbudget qc:ingest`",
              file=sys.stderr)
        return 1

    fed_year = args.federal_year or (
        fed.filter(pl.col("measure") == "expenditures")["fiscal_year"].max()
    )
    qc_year = args.quebec_year or qc["fiscal_year"].max()

    f = (
        fed.filter(
            (pl.col("fiscal_year") == fed_year)
            & (pl.col("measure") == "expenditures")
            & pl.col("dimensions").str.contains("standard_object")
        )
        .group_by("economic_category").agg(pl.col("amount").sum())
    )
    q = (
        qc.filter((pl.col("fiscal_year") == qc_year) & (pl.col("measure") == "authorities"))
        .group_by("economic_category").agg(pl.col("amount").sum())
    )
    fmap = {r["economic_category"]: r["amount"] for r in f.iter_rows(named=True)}
    qmap = {r["economic_category"]: r["amount"] for r in q.iter_rows(named=True)}

    print(f"federal expenditures {fed_year}  vs  Quebec crédits {qc_year}\n")
    print(f"  {'category':<14}{'federal':>11}{'Quebec':>11}   comparability")
    for category in taxonomy.ECONOMIC_CATEGORIES:
        verdict = taxonomy.comparability(category)
        print(f"  {category:<14}{fmap.get(category, 0)/1e9:>9.1f}B{qmap.get(category, 0)/1e9:>10.1f}B"
              f"   {verdict.level}")
    print(f"  {'TOTAL':<14}{sum(fmap.values())/1e9:>9.1f}B{sum(qmap.values())/1e9:>10.1f}B")

    print("\nwhy most of these cannot be placed side by side:", file=sys.stderr)
    for category in taxonomy.ECONOMIC_CATEGORIES:
        verdict = taxonomy.comparability(category)
        if verdict.level != "comparable":
            print(f"  [{verdict.level}] {category}: {verdict.note}", file=sys.stderr)
    return 0


def cmd_dashboard_data(args: argparse.Namespace) -> int:
    """Recompute the aggregate files the Observable Framework site reads."""
    from . import dashboard

    out = Path(args.output) if args.output else None
    try:
        written = dashboard.build(out)
    except Exception as exc:
        log.error("could not build aggregates: %s", exc)
        log.error("has anything been staged? try `govbudget seao:ingest`")
        return 1

    for name, rows in written.items():
        print(f"  {name:<14} {rows:>8,} rows")
    target = out or dashboard.DASHBOARD_DATA_DIR
    print(f"\nwrote {len(written)} file(s) to {target}", file=sys.stderr)
    return 0


# -- wiring ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="govbudget", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sources", help="list registry sources")
    p.add_argument("--jurisdiction", choices=["ca-federal", "qc"])
    p.add_argument("--access", choices=["ckan", "bulk", "pdf", "crawl"])
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("caveats", help="list cross-cutting data caveats")
    p.set_defaults(func=cmd_caveats)

    p = sub.add_parser("check-sources", help="probe sources for reachability")
    p.add_argument("--id", nargs="+", help="only these source ids")
    p.add_argument("--write", action="store_true", help="record results into the registry")
    p.set_defaults(func=cmd_check_sources)

    p = sub.add_parser("seao:discover", help="list SEAO files on Données Québec")
    p.add_argument("--era", choices=["xml", "ocds"])
    p.add_argument("--cadence", choices=["annuel", "mensuel", "hebdo"])
    p.add_argument("--year", type=int)
    p.add_argument("--since", help="inclusive lower bound, YYYY-MM")
    p.add_argument("--until", help="inclusive upper bound, YYYY-MM")
    p.add_argument("--urls", action="store_true", help="print resource URLs")
    p.set_defaults(func=cmd_seao_discover)

    p = sub.add_parser("seao:inspect", help="report the structure of a SEAO XML file")
    p.add_argument("file", help="path to a downloaded SEAO XML file")
    p.add_argument("--json", action="store_true", help="emit the full report as JSON")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_seao_inspect)

    p = sub.add_parser("seao:ingest", help="fetch, parse and stage SEAO contracts")
    p.add_argument("--era", choices=["xml", "ocds"])
    p.add_argument("--cadence", choices=["annuel", "mensuel", "hebdo"])
    p.add_argument("--year", type=int)
    p.add_argument("--since", help="inclusive lower bound, YYYY-MM")
    p.add_argument("--until", help="inclusive upper bound, YYYY-MM")
    p.add_argument("--limit", type=int, help="stop after N resources")
    p.add_argument("--force", action="store_true", help="re-download even if unchanged")
    p.add_argument("--dry-run", action="store_true", help="list what would be fetched")
    p.add_argument(
        "--include-revisions",
        action="store_true",
        help="also parse the *Revisions files (they restate existing records)",
    )
    p.set_defaults(func=cmd_seao_ingest)

    p = sub.add_parser("seao:summary", help="headline figures for staged contracts")
    p.add_argument("--dataset", choices=["awards", "finals", "expenses"])
    p.add_argument("--dedupe", action="store_true", default=True)
    p.add_argument("--no-dedupe", dest="dedupe", action="store_false")
    p.set_defaults(func=cmd_seao_summary)

    p = sub.add_parser("infobase:ingest", help="fetch and stage GC InfoBase")
    p.add_argument("--force", action="store_true", help="re-download even if unchanged")
    p.set_defaults(func=cmd_infobase_ingest)

    p = sub.add_parser("infobase:summary", help="federal spending by economic category")
    p.add_argument("--year", help="fiscal year, e.g. 2023-24")
    p.set_defaults(func=cmd_infobase_summary)

    p = sub.add_parser("qc:ingest", help="fetch and stage the Quebec Budget de dépenses")
    p.add_argument("--years", type=int, help="only the N most recent years")
    p.add_argument("--force", action="store_true", help="re-download even if unchanged")
    p.set_defaults(func=cmd_qc_ingest)

    p = sub.add_parser("compare", help="federal vs Quebec on the shared spine")
    p.add_argument("--federal-year")
    p.add_argument("--quebec-year")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("dashboard:data", help="build the dashboard's aggregate files")
    p.add_argument("--output", help="directory to write into (default dashboard/src/data)")
    p.set_defaults(func=cmd_dashboard_data)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        log.error("%s: %s", type(exc).__name__, exc)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
