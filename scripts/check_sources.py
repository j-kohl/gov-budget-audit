#!/usr/bin/env python3
"""Probe every source in sources/registry.json and report which are actually live.

Standard library only, so it runs anywhere Python 3.9+ is installed:

    python3 scripts/check_sources.py                 # probe everything
    python3 scripts/check_sources.py --tier 1        # only tier-1 sources
    python3 scripts/check_sources.py --id usaspending treasury_fiscal_mts
    python3 scripts/check_sources.py --write         # record results into the registry
    python3 scripts/check_sources.py --report docs/SOURCE_STATUS.md

Sources needing a key are probed anyway: a 401/403 confirms the endpoint is
alive and tells you the key is what is missing, which is more useful than
skipping. Set the key in the environment (see `auth_env` in the registry) to
promote those to a full check.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "sources" / "registry.json"

TIMEOUT_SECONDS = 30
USER_AGENT = "gov-budget-audit source checker (+https://github.com/j-kohl/gov-budget-audit)"

# Outcome classes, ordered worst-to-best for summary reporting.
LIVE = "live"
NEEDS_KEY = "needs_key"
BLOCKED = "blocked"
DEAD = "dead"


def build_opener() -> urllib.request.OpenerDirector:
    """Build an opener that trusts the agent-proxy CA bundle when present."""
    ca_bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    if os.path.exists(ca_bundle):
        context = ssl.create_default_context(cafile=ca_bundle)
    else:
        context = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def probe(source: dict, opener: urllib.request.OpenerDirector) -> dict:
    """Fetch a source's health_check URL and classify the outcome."""
    url = source.get("health_check")
    result = {
        "id": source["id"],
        "name": source["name"],
        "tier": source["tier"],
        "url": url,
        "status": DEAD,
        "http_code": None,
        "detail": "",
    }

    if not url:
        result["detail"] = "no health_check URL defined in registry"
        return result

    # Attach the key if the source needs one and we have it. Most of these
    # accept the key as an api_key query param; BEA calls it UserID.
    auth_env = source.get("auth_env")
    key = os.environ.get(auth_env) if auth_env else None
    if key:
        param = "UserID" if source["id"] == "bea_nipa" else "api_key"
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{param}={key}"

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            result["http_code"] = response.status
            result["status"] = LIVE
            body = response.read(400)
            result["detail"] = f"{len(body)}+ bytes, content-type {response.headers.get('Content-Type', 'unknown')}"
    except urllib.error.HTTPError as exc:
        result["http_code"] = exc.code
        if exc.code in (401, 403) and source.get("auth") == "api_key" and not key:
            result["status"] = NEEDS_KEY
            result["detail"] = f"HTTP {exc.code}; endpoint alive, set ${auth_env}"
        elif exc.code in (401, 403, 407):
            # Could be the egress proxy rather than the destination.
            result["status"] = BLOCKED
            result["detail"] = f"HTTP {exc.code}; egress policy or credentials"
        elif exc.code == 405:
            # Health check is a POST-only endpoint; reaching it at all is a pass.
            result["status"] = LIVE
            result["detail"] = "HTTP 405; endpoint reachable but POST-only"
        else:
            result["status"] = DEAD
            result["detail"] = f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "tunnel" in reason.lower() or "CONNECT" in reason or "forbidden" in reason.lower():
            result["status"] = BLOCKED
            result["detail"] = f"proxy refused CONNECT: {reason}"
        else:
            result["status"] = DEAD
            result["detail"] = reason
    except Exception as exc:  # noqa: BLE001 - a checker must never crash mid-sweep
        result["status"] = DEAD
        result["detail"] = f"{type(exc).__name__}: {exc}"

    return result


def render_report(results: list[dict], registry: dict) -> str:
    """Render results as a Markdown status report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = {status: sum(1 for r in results if r["status"] == status) for status in (LIVE, NEEDS_KEY, BLOCKED, DEAD)}

    icon = {LIVE: "OK", NEEDS_KEY: "KEY", BLOCKED: "BLOCKED", DEAD: "FAIL"}

    lines = [
        "# Source status",
        "",
        f"Generated {now} by `scripts/check_sources.py`.",
        "",
        f"{counts[LIVE]} live, {counts[NEEDS_KEY]} awaiting an API key, "
        f"{counts[BLOCKED]} blocked by network policy, {counts[DEAD]} failing.",
        "",
        "| Source | Tier | Status | HTTP | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]

    for result in sorted(results, key=lambda r: (r["tier"], r["id"])):
        lines.append(
            f"| `{result['id']}` — {result['name']} | {result['tier']} | "
            f"{icon[result['status']]} | {result['http_code'] or '—'} | {result['detail']} |"
        )

    blocked = [r for r in results if r["status"] == BLOCKED]
    if blocked:
        lines += [
            "",
            "## Blocked hosts",
            "",
            "These were refused before reaching the destination. If you are running inside a",
            "sandbox with an egress allowlist, these need to be allowlisted rather than retried:",
            "",
        ]
        lines += [f"- `{r['id']}` — {r['url']}" for r in blocked]

    needs_key = [r for r in results if r["status"] == NEEDS_KEY]
    if needs_key:
        by_id = {s["id"]: s for s in registry["sources"]}
        lines += ["", "## Missing API keys", ""]
        lines += [f"- `{r['id']}` — set `${by_id[r['id']]['auth_env']}`" for r in needs_key]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", type=int, action="append", help="only probe these tiers (repeatable)")
    parser.add_argument("--id", nargs="+", help="only probe these source ids")
    parser.add_argument("--write", action="store_true", help="record verified state back into the registry")
    parser.add_argument("--report", type=Path, help="write a Markdown status report to this path")
    parser.add_argument("--jobs", type=int, default=8, help="parallel probes (default 8)")
    args = parser.parse_args()

    registry = json.loads(REGISTRY_PATH.read_text())
    sources = registry["sources"]

    if args.tier:
        sources = [s for s in sources if s["tier"] in args.tier]
    if args.id:
        wanted = set(args.id)
        unknown = wanted - {s["id"] for s in registry["sources"]}
        if unknown:
            print(f"unknown source ids: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        sources = [s for s in sources if s["id"] in wanted]

    if not sources:
        print("no sources matched the filters", file=sys.stderr)
        return 2

    print(f"Probing {len(sources)} source(s)...\n", file=sys.stderr)
    opener = build_opener()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda s: probe(s, opener), sources))

    width = max(len(r["id"]) for r in results)
    for result in sorted(results, key=lambda r: (r["tier"], r["id"])):
        print(f"  {result['status']:<9} {result['id']:<{width}}  {result['detail']}")

    counts = {status: sum(1 for r in results if r["status"] == status) for status in (LIVE, NEEDS_KEY, BLOCKED, DEAD)}
    print(
        f"\n{counts[LIVE]} live, {counts[NEEDS_KEY]} need a key, "
        f"{counts[BLOCKED]} blocked, {counts[DEAD]} failing",
        file=sys.stderr,
    )

    if args.write:
        verified_ids = {r["id"] for r in results if r["status"] == LIVE}
        probed_ids = {r["id"] for r in results}
        for source in registry["sources"]:
            if source["id"] in probed_ids:
                source["verified"] = source["id"] in verified_ids
        registry["last_verified"] = datetime.now(timezone.utc).isoformat()
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
        print(f"updated {REGISTRY_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(results, registry))
        print(f"wrote {args.report}", file=sys.stderr)

    # Missing keys are a setup gap, not a failure. Blocked and dead are failures.
    return 1 if (counts[BLOCKED] or counts[DEAD]) else 0


if __name__ == "__main__":
    sys.exit(main())
