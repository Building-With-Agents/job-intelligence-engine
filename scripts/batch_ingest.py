"""
Batch ingestion — budget-aware JSearch queries with API key rotation.

Reads query configuration from config/ingestion_queries.yaml.
Rotates API keys when budget_per_key is reached or a 429 is received.
Ingestion only — stages raw records for downstream processing.

Prerequisites:
  - JSEARCH_API_KEY (or JSEARCH_API_KEY_1, JSEARCH_API_KEY_2, ...) in .env
  - PYTHON_DATABASE_URL for database staging

Usage (from repo root):
  python scripts/batch_ingest.py                        # run all queries
  python scripts/batch_ingest.py --dry-run              # show plan without API calls
  python scripts/batch_ingest.py --delay 10             # seconds between queries
  python scripts/batch_ingest.py --start-query 24       # skip queries 1-23, start at 24
  python scripts/batch_ingest.py --queries legal-tech,robotics-dev  # run specific queries only
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


def _load_api_keys() -> list[str]:
    """Load API keys from env vars: JSEARCH_API_KEY, JSEARCH_API_KEY_2, ..., JSEARCH_API_KEY_N."""
    keys = []
    primary = os.getenv("JSEARCH_API_KEY", "").strip()
    if primary:
        keys.append(primary)
    for i in range(2, 11):  # support up to 10 keys
        key = os.getenv(f"JSEARCH_API_KEY_{i}", "").strip()
        if key:
            keys.append(key)
    return keys


def _build_region_config(query: dict) -> dict:
    """Build a RegionConfig dict from a YAML query entry.

    Supports optional ``location`` field for geo-targeted queries
    (e.g. ``location: "El Paso, TX"``).
    """
    location = query.get("location", "")
    return {
        "region_id": f"batch-{query['name']}",
        "display_name": query["name"],
        "query_location": location,
        "radius_miles": query.get("radius_miles", 9999),
        "states": query.get("states", []),
        "countries": query.get("countries", ["US"]),
        "sources": ["jsearch"],
        "role_categories": [],
        "keywords": query["keywords"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Budget-aware batch ingestion via JSearch")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without API calls")
    parser.add_argument("--delay", type=int, default=5, help="Seconds between queries (default: 5)")
    parser.add_argument(
        "--start-query", type=int, default=1, metavar="N",
        help="Start at query N (1-indexed), skipping all prior queries. "
             "Use to resume after a previous run exhausted keys.",
    )
    parser.add_argument(
        "--queries", type=str, default="", metavar="name1,name2,...",
        help="Run only the named queries (comma-separated). "
             "Names must match the 'name' field in ingestion_queries.yaml.",
    )
    args = parser.parse_args()

    config = _load_config()
    budget_per_key = config.get("budget_per_key", 500)
    all_queries = config.get("queries", [])

    # Filter queries based on --start-query and --queries flags
    if args.queries:
        query_names = {n.strip() for n in args.queries.split(",")}
        queries = [q for q in all_queries if q["name"] in query_names]
        missing = query_names - {q["name"] for q in queries}
        if missing:
            log.warning("unknown_query_names", names=sorted(missing))
    elif args.start_query > 1:
        queries = all_queries[args.start_query - 1:]
        log.info("skipping_queries", skipped=args.start_query - 1, remaining=len(queries))
    else:
        queries = all_queries

    if not queries:
        log.error("no_queries_configured")
        sys.exit(1)

    api_keys = _load_api_keys()
    if not api_keys and not args.dry_run:
        log.error("no_api_keys_found", hint="Set JSEARCH_API_KEY or JSEARCH_API_KEY_1 in .env")
        sys.exit(1)

    total_requests = sum(q.get("pages", 10) for q in queries)

    log.info(
        "batch_plan",
        queries=len(queries),
        total_requests=total_requests,
        api_keys_available=len(api_keys),
        budget_per_key=budget_per_key,
        total_budget=len(api_keys) * budget_per_key,
        dry_run=args.dry_run,
    )

    # Build a name→original-index map for reference (1-indexed as shown in dry-run)
    _all_query_names = [q["name"] for q in all_queries]

    if args.dry_run:
        print(f"\n{'='*60}")
        print("Batch Ingestion Plan")
        if args.start_query > 1:
            print(f"  (starting at query {args.start_query}, skipping {args.start_query - 1})")
        if args.queries:
            print(f"  (filtered to: {args.queries})")
        print(f"{'='*60}")
        for q in queries:
            orig_idx = _all_query_names.index(q["name"]) + 1 if q["name"] in _all_query_names else "?"
            pages = q.get("pages", 10)
            print(f"\n  [{orig_idx}] {q['name']}")
            print(f"      Keywords: {q['keywords']}")
            print(f"      Pages: {pages} ({pages} API requests, ~{pages * 10} results)")
        print(f"\n  Total: {total_requests} API requests across {len(queries)} queries")
        print(f"  Keys available: {len(api_keys)} x {budget_per_key} = {len(api_keys) * budget_per_key} budget")
        print(f"  Delay: {args.delay}s between queries")
        print(f"\n  Tip: --start-query N to skip first N-1 queries")
        print(f"       --queries name1,name2 to run specific queries only")
        print(f"{'='*60}\n")
        return

    # Late imports
    from common.event_envelope import EventEnvelope
    from ingestion.agent import IngestionAgent

    agent = IngestionAgent()
    current_key_idx = 0
    requests_used_on_key = 0
    total_staged = 0
    total_requests_used = 0

    for i, query in enumerate(queries, 1):
        pages = query.get("pages", 10)

        # Check if current key has budget
        if requests_used_on_key + pages > budget_per_key:
            current_key_idx += 1
            requests_used_on_key = 0
            if current_key_idx >= len(api_keys):
                log.warning("all_keys_exhausted", queries_remaining=len(queries) - i + 1)
                break
            log.info("key_rotation", new_key_idx=current_key_idx + 1)

        # Set the active key
        os.environ["JSEARCH_API_KEY"] = api_keys[current_key_idx]
        os.environ["BATCH_SIZE"] = str(pages * 10)

        region = _build_region_config(query)
        correlation_id = f"batch-{query['name']}-{uuid.uuid4().hex[:8]}"

        log.info(
            "query_start",
            num=f"{i}/{len(queries)}",
            name=query["name"],
            keywords=query["keywords"],
            pages=pages,
            key_idx=current_key_idx + 1,
            key_budget_remaining=budget_per_key - requests_used_on_key,
        )

        event = EventEnvelope(
            correlation_id=correlation_id,
            agent_id="batch-ingest-script",
            payload={"region_config": region, "source": "jsearch"},
        )

        try:
            out = agent.process(event)
            requests_used_on_key += pages
            total_requests_used += pages

            if out and out.payload.get("event_type") == "IngestBatch":
                staged = out.payload.get("staged_count", 0)
                dedup = out.payload.get("dedup_count", 0)
                total_staged += staged
                log.info(
                    "query_complete",
                    name=query["name"],
                    staged=staged,
                    dedup_skipped=dedup,
                    running_total=total_staged,
                    requests_used=total_requests_used,
                )
            elif out and out.payload.get("event_type") == "SourceFailure":
                error = out.payload.get("error", "")
                if "429" in str(error) or "rate" in str(error).lower():
                    log.warning("rate_limited", name=query["name"], rotating_key=True)
                    current_key_idx += 1
                    requests_used_on_key = 0
                    if current_key_idx >= len(api_keys):
                        log.warning("all_keys_exhausted_429")
                        break
                else:
                    log.warning("source_failure", name=query["name"], error=error)
        except Exception as exc:
            error_str = str(exc)
            if "429" in error_str:
                log.warning("rate_limited_exception", name=query["name"], rotating_key=True)
                current_key_idx += 1
                requests_used_on_key = 0
                if current_key_idx >= len(api_keys):
                    log.warning("all_keys_exhausted_429")
                    break
            else:
                log.error("query_failed", name=query["name"], error=error_str)

        if i < len(queries):
            time.sleep(args.delay)

    log.info(
        "batch_complete",
        total_staged=total_staged,
        total_requests=total_requests_used,
        keys_used=current_key_idx + 1,
    )
    print(f"\nDone. {total_staged} records staged. {total_requests_used} API requests used across {current_key_idx + 1} key(s).")
    print("Run processing loop: python scripts/run_processing_loop.py")


if __name__ == "__main__":
    main()
