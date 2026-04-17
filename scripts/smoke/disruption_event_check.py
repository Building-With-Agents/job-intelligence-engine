#!/usr/bin/env python3
"""Week 8 smoke test — verify DisruptionRefreshed event emission.

The disruption service only publishes a DisruptionRefreshed event when a bus
is registered. This script attaches an in-process capture bus, runs one
refresh, and prints the event payload.

Usage (from any shell, any CWD):

    python scripts/smoke/disruption_event_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.disruption.service import DisruptionFingerprintService  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402


class CaptureBus:
    """Minimal in-process event bus that captures published envelopes."""

    def __init__(self) -> None:
        self.events: list = []

    def publish(self, envelope) -> None:  # noqa: ANN001
        self.events.append(envelope)


def main() -> int:
    bus = CaptureBus()
    svc = DisruptionFingerprintService(event_bus=bus)

    with session_scope() as session:
        svc.refresh_disruption_fingerprints(
            session=session,
            correlation_id="wk8-event-check",
        )

    if not bus.events:
        print("WARNING: no DisruptionRefreshed envelope captured.")
        print("  - If roles_considered=0, this is expected (no roles to refresh).")
        print("  - If roles_considered>0, the service may not be emitting — check")
        print("    DisruptionFingerprintService.refresh_disruption_fingerprints for")
        print("    the publish() call path.")
        return 1

    env = bus.events[0]
    print("DisruptionRefreshed event captured:")
    print()

    # EventEnvelope top-level fields
    print(f"  event_id:               {env.event_id}")
    print(f"  correlation_id:         {env.correlation_id}")
    print(f"  agent_id:               {env.agent_id}")
    print(f"  schema_version:         {env.schema_version}")

    # event_type lives inside env.payload (a dict), not at envelope top level
    payload = env.payload if isinstance(env.payload, dict) else {}
    print(f"  payload.event_type:     {payload.get('event_type', '(missing)')}")
    for field in [
        "role_count",
        "displacement_count",
        "augmentation_count",
        "transformation_count",
        "emergence_count",
        "refresh_duration_ms",
    ]:
        val = payload.get(field)
        if val is not None:
            print(f"  payload.{field:25} {val}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
