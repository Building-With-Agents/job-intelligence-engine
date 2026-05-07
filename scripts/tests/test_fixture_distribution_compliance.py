"""Guard tests for seed-fixture distribution compliance.

Heavy fixtures are distributed via a GitHub Release bundle pinned by
``scripts/pg-seed-data/fixtures-manifest.json`` (issue #322). LFS is no
longer used.

Invariant the export workflow must hold:

**Size guard.** Any committed fixture larger than ``_PLAIN_TEXT_MAX_BYTES``
must be distributed via the GitHub Release bundle declared in
``fixtures-manifest.json`` — i.e., listed in ``included_tables``. Without
this, the next re-export of a heavy table silently bloats the git history
and trips GitHub's 50 MB warning / 100 MB hard block.

Pure-Python, no DB, fast — safe to run in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures"
_MANIFEST_PATH = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures-manifest.json"

# Plain-text fixtures may not exceed this size; anything heavier MUST be
# distributed through the GitHub Release bundle (manifest) instead of being
# committed to git.
_PLAIN_TEXT_MAX_BYTES = 40 * 1024 * 1024  # 40 MB


def _bundled_fixture_basenames() -> set[str]:
    """Read fixtures-manifest.json and return the set of fixture filenames in the bundle."""
    if not _MANIFEST_PATH.exists():
        return set()
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {f"{table}.json" for table in manifest.get("included_tables", [])}


def _fixture_files() -> list[Path]:
    if not _FIXTURES_DIR.is_dir():
        pytest.skip(f"Fixtures dir not present: {_FIXTURES_DIR}")
    return sorted(p for p in _FIXTURES_DIR.iterdir() if p.is_file() and p.suffix == ".json")


def test_heavy_fixtures_are_in_release_bundle() -> None:
    """Any fixture above the plain-text size ceiling must be in the manifest's bundle list."""
    bundled = _bundled_fixture_basenames()
    offenders: list[str] = []
    for fixture in _fixture_files():
        size = fixture.stat().st_size
        if size <= _PLAIN_TEXT_MAX_BYTES:
            continue
        if fixture.name not in bundled:
            rel = fixture.relative_to(_REPO_ROOT).as_posix()
            offenders.append(f"  {rel} ({size / 1024 / 1024:.1f} MB)")
    if offenders:
        offender_block = "\n".join(offenders)
        threshold_mb = _PLAIN_TEXT_MAX_BYTES / 1024 / 1024
        msg = (
            f"\nFixture files exceed {threshold_mb:.0f} MB without being in the GitHub "
            "Release bundle.\n"
            "Add each filename (without `.json`) to `included_tables` in "
            "scripts/pg-seed-data/fixtures-manifest.json, then run:\n"
            "  python scripts/pg-seed-data/publish_fixtures.py\n"
            "to cut a new fixtures-vN release. The fixtures must also appear in\n"
            "`BUNDLED_TABLES` in scripts/pg-seed-data/publish_fixtures.py.\n\n"
            f"Offenders:\n{offender_block}"
        )
        pytest.fail(msg)
