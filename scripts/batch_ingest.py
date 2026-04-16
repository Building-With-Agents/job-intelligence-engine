"""
Batch ingestion — budget-aware JSearch queries with per-tier location expansion.

Reads query configuration from config/ingestion_queries.yaml. Each query
declares a ``location_tier`` (or ``null``); the script expands the query
across every location in that tier and calls JSearch once per expansion.

On the paid Pro plan the monthly request budget is authoritative — the
counter is persisted in ``dbo.job_ingestion_runs.api_requests_used`` and
summed per calendar month.

Prerequisites:
  - JSEARCH_API_KEY in .env (paid Pro-plan key in slot 1)
  - PYTHON_DATABASE_URL for database staging + budget counter

Usage (from repo root):
  python scripts/batch_ingest.py                            # run all queries × their tiers
  python scripts/batch_ingest.py --dry-run                  # show plan without API calls
  python scripts/batch_ingest.py --delay 10                 # seconds between queries
  python scripts/batch_ingest.py --start-query 24           # skip queries 1-23
  python scripts/batch_ingest.py --queries legal-tech,robotics-dev   # filter queries
  python scripts/batch_ingest.py --location-tier tier_2     # override every query's tier
  python scripts/batch_ingest.py --no-locations             # disable geo expansion entirely
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import yaml

# Path bootstrap
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

import structlog  # noqa: E402

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ingestion_queries.yaml"


def _load_config() -> dict:
    """Load query configuration from YAML."""
    if not _CONFIG_PATH.exists():
        log.error("config_not_found", path=str(_CONFIG_PATH))
        sys.exit(1)
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_api_keys() -> list[tuple[int, str]]:
    """Load keyed JSearch credentials as ``(slot, value)`` pairs."""
    keys: list[tuple[int, str]] = []
    primary = os.getenv("JSEARCH_API_KEY", "").strip()
    if primary:
        keys.append((1, primary))
    for i in range(2, 11):  # support up to 10 keys
        key = os.getenv(f"JSEARCH_API_KEY_{i}", "").strip()
        if key:
            keys.append((i, key))
    return keys


def _resolve_start_key_slot(cli_value: int | None) -> int:
    """Choose the first key slot to use, preferring CLI over env."""
    raw = cli_value if cli_value is not None else os.getenv("JSEARCH_START_KEY_INDEX", "1")
    try:
        slot = int(raw)
    except (TypeError, ValueError):
        log.warning("invalid_start_key_slot", raw_value=raw, fallback=1)
        return 1
    if slot < 1:
        log.warning("invalid_start_key_slot", raw_value=raw, fallback=1)
        return 1
    return slot


def _expand_queries(
    queries: list[dict],
    location_tiers: dict[str, list[str]],
    *,
    tier_override: str | None = None,
    disable_locations: bool = False,
) -> list[tuple[dict, str]]:
    """Expand each query into ``(single-keyword query dict, location)`` pairs.

    Each entry in the returned list triggers **one** JSearch call (issue #165).
    The expansion is 3D: every YAML query fans out across its keywords AND its
    tier's locations so the adapter sees a single keyword + single location
    per call (canonical JSearch ``"<role> in <location>"`` pattern).

    - ``tier_override``: if set, every query uses this tier (useful for ad-hoc runs).
    - ``disable_locations``: force one call per keyword with no location context.
    - A query with ``location_tier: null`` or an unknown tier emits one entry
      per keyword with ``location=""`` (no geo expansion).
    """
    expanded: list[tuple[dict, str]] = []

    def _emit_for_keywords(q: dict, locations: list[str]) -> None:
        keywords = q.get("keywords") or []
        if not keywords:
            # Degenerate query with no keywords: fall back to a bare ``jobs``
            # request at each location so callers still see a row.
            for loc in locations:
                expanded.append(({**q, "keywords": ["jobs"]}, loc))
            return
        for kw in keywords:
            clone = {**q, "keywords": [kw]}
            for loc in locations:
                expanded.append((clone, loc))

    for q in queries:
        if disable_locations:
            _emit_for_keywords(q, [""])
            continue
        tier_name = tier_override if tier_override is not None else q.get("location_tier")
        if not tier_name:
            _emit_for_keywords(q, [""])
            continue
        tier_locs = location_tiers.get(tier_name)
        if not tier_locs:
            log.warning(
                "unknown_location_tier",
                query=q.get("name"),
                tier=tier_name,
                available=sorted(location_tiers.keys()),
            )
            _emit_for_keywords(q, [""])
            continue
        _emit_for_keywords(q, list(tier_locs))
    return expanded


def _build_region_config(query: dict, location: str) -> dict:
    """Build a RegionConfig dict from a YAML query entry + resolved location.

    Expects ``query["keywords"]`` to already be trimmed to a single keyword
    by ``_expand_queries``. The keyword is passed through unchanged so the
    adapter can build ``"<keyword> in <location>"``.
    """
    keyword = (query.get("keywords") or ["jobs"])[0]
    kw_slug = keyword.replace(" ", "-").replace("/", "-").lower()
    loc_slug = location.replace(",", "").replace(" ", "-").lower() or "national"
    return {
        "region_id": f"batch-{query['name']}-{kw_slug}-{loc_slug}"[:100],
        "display_name": f"{query['name']} [{keyword} in {location or 'national'}]",
        "query_location": location,
        "radius_miles": query.get("radius_miles", 9999),
        "states": query.get("states", []),
        "countries": query.get("countries", ["US"]),
        "sources": ["jsearch"],
        "role_categories": [],
        "keywords": query["keywords"],
    }


def _monthly_requests_used() -> int:
    """Sum ``api_requests_used`` for JSearch runs in the current UTC month.

    Returns 0 if the DB is unreachable — budget check will not block when
    we can't read the counter (warning logged so operators notice).
    """
    try:
        from sqlalchemy import text

        from common.data_store.database import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT COALESCE(SUM(api_requests_used), 0) AS used
                    FROM dbo.job_ingestion_runs
                    WHERE source = 'jsearch'
                      AND started_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
                    """
                )
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        log.warning("monthly_counter_read_failed", error=str(exc))
        return 0


def _record_run_requests(run_id: str, pages: int) -> None:
    """Persist the request count for a completed run via direct UPDATE."""
    try:
        from sqlalchemy import text

        from common.data_store.database import get_engine

        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE dbo.job_ingestion_runs SET api_requests_used = :pages WHERE run_id = :run_id"),
                {"pages": pages, "run_id": run_id},
            )
    except Exception as exc:
        log.warning("api_requests_used_write_failed", run_id=run_id, error=str(exc))


def _propagate_throttle_env(throttle: dict) -> None:
    """Push throttle YAML values into env vars read by the JSearch adapter."""
    rps = throttle.get("requests_per_second")
    rpm = throttle.get("requests_per_minute")
    if rps is not None:
        os.environ["JSEARCH_RPS"] = str(rps)
    if rpm is not None:
        os.environ["JSEARCH_RPM"] = str(rpm)


def main() -> None:
    parser = argparse.ArgumentParser(description="Budget-aware batch ingestion via JSearch")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without API calls")
    parser.add_argument("--delay", type=int, default=5, help="Seconds between queries (default: 5)")
    parser.add_argument(
        "--start-key",
        type=int,
        default=None,
        metavar="K",
        help="Start with JSearch key slot K (e.g. 4 uses JSEARCH_API_KEY_4). Defaults to JSEARCH_START_KEY_INDEX or 1.",
    )
    parser.add_argument(
        "--start-query",
        type=int,
        default=1,
        metavar="N",
        help="Start at query N (1-indexed), skipping all prior queries.",
    )
    parser.add_argument(
        "--queries",
        type=str,
        default="",
        metavar="name1,name2,...",
        help="Run only the named queries (comma-separated).",
    )
    parser.add_argument(
        "--location-tier",
        type=str,
        default=None,
        metavar="TIER",
        help="Override every query's location_tier with TIER (must exist in YAML location_tiers).",
    )
    parser.add_argument(
        "--no-locations",
        action="store_true",
        help="Disable geo expansion — run each query once with no location context.",
    )
    args = parser.parse_args()

    config = _load_config()
    budget_per_key = config.get("budget_per_key", 500)
    max_pages = config.get("max_pages", 50)
    all_queries = config.get("queries", [])
    location_tiers: dict[str, list[str]] = config.get("location_tiers", {}) or {}
    monthly_budget = int(config.get("monthly_request_budget", 0) or 0)
    throttle = config.get("throttle", {}) or {}
    _propagate_throttle_env(throttle)

    # Safety log: paid key lives in slot 1; warn if operator pointed start elsewhere.
    start_idx_env = os.getenv("JSEARCH_START_KEY_INDEX", "1")
    if start_idx_env != "1":
        log.warning("start_key_not_paid_slot", JSEARCH_START_KEY_INDEX=start_idx_env)

    # Filter queries based on --start-query and --queries flags
    if args.queries:
        query_names = {n.strip() for n in args.queries.split(",")}
        queries = [q for q in all_queries if q["name"] in query_names]
        missing = query_names - {q["name"] for q in queries}
        if missing:
            log.warning("unknown_query_names", names=sorted(missing))
    elif args.start_query > 1:
        queries = all_queries[args.start_query - 1 :]
        log.info("skipping_queries", skipped=args.start_query - 1, remaining=len(queries))
    else:
        queries = all_queries

    if not queries:
        log.error("no_queries_configured")
        sys.exit(1)

    # Expand into (query, location) pairs. Each pair = one JSearch fetch batch.
    expanded = _expand_queries(
        queries,
        location_tiers,
        tier_override=args.location_tier,
        disable_locations=args.no_locations,
    )
    if not expanded:
        log.error("no_query_location_pairs_after_expansion")
        sys.exit(1)

    api_keys = _load_api_keys()
    if not api_keys and not args.dry_run:
        log.error("no_api_keys_found", hint="Set JSEARCH_API_KEY in .env (paid Pro-plan key)")
        sys.exit(1)

    start_key_slot = _resolve_start_key_slot(args.start_key)
    start_key_idx = next((idx for idx, (slot, _) in enumerate(api_keys) if slot >= start_key_slot), None)
    if start_key_idx is None and not args.dry_run:
        log.error(
            "start_key_not_available",
            requested=start_key_slot,
            available_slots=[slot for slot, _ in api_keys],
        )
        sys.exit(1)
    if start_key_idx is None:
        start_key_idx = 0
    effective_start_slot = api_keys[start_key_idx][0] if api_keys else start_key_slot
    if api_keys and effective_start_slot != start_key_slot:
        log.warning(
            "start_key_missing",
            requested=start_key_slot,
            using=effective_start_slot,
            available_slots=[slot for slot, _ in api_keys],
        )

    total_requests = len(expanded) * max_pages
    monthly_used = _monthly_requests_used() if monthly_budget else 0
    monthly_after = monthly_used + total_requests
    over_budget = bool(monthly_budget) and monthly_after > monthly_budget

    log.info(
        "batch_plan",
        queries=len(queries),
        expanded_pairs=len(expanded),
        total_requests=total_requests,
        api_keys_available=max(0, len(api_keys) - start_key_idx),
        api_keys_total=len(api_keys),
        budget_per_key=budget_per_key,
        monthly_budget=monthly_budget,
        monthly_used=monthly_used,
        monthly_after=monthly_after,
        over_budget=over_budget,
        start_key_slot=effective_start_slot,
        dry_run=args.dry_run,
    )

    # Build a name→original-index map for reference (1-indexed as shown in dry-run)
    _all_query_names = [q["name"] for q in all_queries]

    if args.dry_run:
        print(f"\n{'=' * 60}")
        print("Batch Ingestion Plan")
        if api_keys:
            print(f"  (starting at key slot {effective_start_slot})")
        if args.start_query > 1:
            print(f"  (starting at query {args.start_query}, skipping {args.start_query - 1})")
        if args.queries:
            print(f"  (filtered to: {args.queries})")
        if args.location_tier:
            print(f"  (tier override: {args.location_tier})")
        if args.no_locations:
            print("  (geo expansion disabled)")
        print(f"{'=' * 60}")

        # Group expanded pairs by query name for readable output — each
        # expansion row is a (single-keyword, location) call.
        by_query: dict[str, list[tuple[str, str]]] = {}
        for q, loc in expanded:
            kw = (q.get("keywords") or [""])[0]
            by_query.setdefault(q["name"], []).append((kw, loc or "national"))
        for q in queries:
            name = q["name"]
            pairs = by_query.get(name, [])
            orig_idx = _all_query_names.index(name) + 1 if name in _all_query_names else "?"
            print(f"\n  [{orig_idx}] {name}")
            print(f"      Keywords: {q['keywords']}")
            print(f"      Calls: {len(pairs)} × {max_pages} pages = {len(pairs) * max_pages} requests max")
            for kw, loc in pairs:
                print(f"        - '{kw}' in {loc}")

        print(f"\n  Total: {total_requests} API requests across {len(expanded)} keyword-location pairs")
        print(
            f"  Keys available: {max(0, len(api_keys) - start_key_idx)} x {budget_per_key} = "
            f"{max(0, len(api_keys) - start_key_idx) * budget_per_key} legacy budget"
        )
        if monthly_budget:
            pct = (monthly_after * 100) // monthly_budget if monthly_budget else 0
            print(f"  Monthly Pro budget: {monthly_used} used / {monthly_budget} cap")
            print(f"  After this run:     {monthly_after} used ({pct}% of cap)")
            if over_budget:
                print(f"  ⚠ OVER BUDGET by {monthly_after - monthly_budget} requests — run will stop early")
            elif pct >= 80:
                print("  ⚠ >80% of monthly budget — consider smaller run")
        print(f"  Delay: {args.delay}s between queries")
        print("\n  Tip: --start-query N to skip first N-1 queries")
        print("       --location-tier TIER to override every query's tier")
        print("       --no-locations to disable geo expansion")
        print("       --queries name1,name2 to run specific queries only")
        print(f"{'=' * 60}\n")
        return

    if over_budget:
        log.error(
            "monthly_budget_would_be_exceeded",
            monthly_used=monthly_used,
            monthly_after=monthly_after,
            monthly_budget=monthly_budget,
            hint="reduce --queries or wait for next month",
        )
        sys.exit(1)

    # Late imports
    from common.event_envelope import EventEnvelope
    from ingestion.agent import IngestionAgent

    agent = IngestionAgent()
    current_key_idx = start_key_idx
    highest_key_idx_used = start_key_idx
    requests_used_on_key = 0
    total_staged = 0
    total_requests_used = 0
    warned_80_pct = False

    stop_batch = False
    for i, (query, location) in enumerate(expanded, 1):
        pages = max_pages

        # Monthly budget pre-check (re-read counter periodically so long runs stay honest).
        if monthly_budget:
            current_monthly = monthly_used + total_requests_used
            if current_monthly + pages > monthly_budget:
                log.warning(
                    "monthly_budget_exhausted",
                    monthly_used=current_monthly,
                    monthly_budget=monthly_budget,
                    queries_remaining=len(expanded) - i + 1,
                )
                stop_batch = True
                break
            if not warned_80_pct and current_monthly >= int(monthly_budget * 0.8):
                log.warning(
                    "monthly_budget_80pct",
                    monthly_used=current_monthly,
                    monthly_budget=monthly_budget,
                )
                warned_80_pct = True

        attempt = 1
        while True:
            if requests_used_on_key + pages > budget_per_key:
                current_key_idx += 1
                requests_used_on_key = 0
                if current_key_idx >= len(api_keys):
                    log.warning("all_keys_exhausted", queries_remaining=len(expanded) - i + 1)
                    stop_batch = True
                    break
                highest_key_idx_used = max(highest_key_idx_used, current_key_idx)
                log.info("key_rotation", new_key_slot=api_keys[current_key_idx][0])

            active_key_slot, active_key = api_keys[current_key_idx]
            highest_key_idx_used = max(highest_key_idx_used, current_key_idx)
            os.environ["JSEARCH_API_KEY"] = active_key
            os.environ["JSEARCH_MAX_PAGES"] = str(pages)

            region = _build_region_config(query, location)
            correlation_id = f"batch-{query['name']}-{uuid.uuid4().hex[:8]}"

            log.info(
                "query_start",
                num=f"{i}/{len(expanded)}",
                name=query["name"],
                location=location or "national",
                keywords=query["keywords"],
                pages=pages,
                key_slot=active_key_slot,
                attempt=attempt,
                key_budget_remaining=budget_per_key - requests_used_on_key,
            )

            event = EventEnvelope(
                correlation_id=correlation_id,
                agent_id="batch-ingest-script",
                payload={"region_config": region, "source": "jsearch"},
            )

            try:
                out = agent.process(event)

                # Approximate actual pages consumed from total_fetched instead
                # of writing the configured max_pages (issue #164 pragmatic
                # fix). JSearch returns 10 jobs per full page, and the adapter
                # early-breaks on empty/partial pages, so:
                #   pages ≈ max(1, ceil(total_fetched / 10))
                # This still over-counts 429 retries and cross-source dedup
                # inside the adapter, but is 5–40× closer to RapidAPI's actual
                # billing than the old max_pages approach.
                # Follow-up: expose true pages_consumed from the adapter (#164).
                run_id = out.payload.get("batch_id") if out else None
                total_fetched = int(out.payload.get("total_fetched", 0)) if out else 0
                actual_pages = max(1, (total_fetched + 9) // 10)
                requests_used_on_key += actual_pages
                total_requests_used += actual_pages
                if run_id:
                    _record_run_requests(run_id, actual_pages)

                if out and out.payload.get("event_type") == "IngestBatch":
                    staged = out.payload.get("staged_count", 0)
                    dedup = out.payload.get("dedup_count", 0)
                    total_staged += staged
                    log.info(
                        "query_complete",
                        name=query["name"],
                        location=location or "national",
                        staged=staged,
                        dedup_skipped=dedup,
                        running_total=total_staged,
                        requests_used=total_requests_used,
                    )
                    break

                if out and out.payload.get("event_type") == "SourceFailure":
                    error = out.payload.get("error", "")
                    if "429" in str(error) or "rate" in str(error).lower():
                        log.warning("rate_limited", name=query["name"], rotating_key=True, key_slot=active_key_slot)
                        current_key_idx += 1
                        requests_used_on_key = 0
                        if current_key_idx >= len(api_keys):
                            log.warning("all_keys_exhausted_429")
                            stop_batch = True
                            break
                        highest_key_idx_used = max(highest_key_idx_used, current_key_idx)
                        log.info("key_rotation", new_key_slot=api_keys[current_key_idx][0])
                        attempt += 1
                        continue

                    log.warning("source_failure", name=query["name"], error=error)
                break
            except Exception as exc:
                error_str = str(exc)
                if "429" in error_str:
                    log.warning(
                        "rate_limited_exception", name=query["name"], rotating_key=True, key_slot=active_key_slot
                    )
                    current_key_idx += 1
                    requests_used_on_key = 0
                    if current_key_idx >= len(api_keys):
                        log.warning("all_keys_exhausted_429")
                        stop_batch = True
                        break
                    highest_key_idx_used = max(highest_key_idx_used, current_key_idx)
                    log.info("key_rotation", new_key_slot=api_keys[current_key_idx][0])
                    attempt += 1
                    continue

                log.error("query_failed", name=query["name"], error=error_str)
                break

        if stop_batch:
            break

        if i < len(expanded):
            time.sleep(args.delay)

    log.info(
        "batch_complete",
        total_staged=total_staged,
        total_requests=total_requests_used,
        keys_used=(highest_key_idx_used - start_key_idx + 1) if api_keys else 0,
        start_key_slot=effective_start_slot,
        monthly_used_after=monthly_used + total_requests_used if monthly_budget else None,
        monthly_budget=monthly_budget if monthly_budget else None,
    )
    keys_used = (highest_key_idx_used - start_key_idx + 1) if api_keys else 0
    print(f"\nDone. {total_staged} records staged. {total_requests_used} API requests used across {keys_used} key(s).")
    if monthly_budget:
        print(f"Monthly budget: {monthly_used + total_requests_used}/{monthly_budget} used this month.")
    print("Run processing loop: python scripts/run_processing_loop.py")


if __name__ == "__main__":
    main()
