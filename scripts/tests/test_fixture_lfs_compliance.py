"""Guard tests for seed-fixture distribution compliance.

Originally introduced for issue #251 (LFS) and reworked for issue #322
(GitHub Release bundle replacing LFS, after the org-wide LFS bandwidth
budget was exhausted).

Two invariants the export workflow must hold:

1. **Size guard.** Any committed fixture larger than `_PLAIN_TEXT_MAX_BYTES`
   must be distributed via the GitHub Release bundle declared in
   ``fixtures-manifest.json`` — i.e., listed in ``included_tables``. Without
   this, the next re-export of a heavy table silently bloats the git history
   and trips GitHub's 50 MB warning / 100 MB hard block.

2. **LFS pointer detection.** Holdover from the old LFS workflow: no fixture
   in the working tree should be a ~133-byte LFS pointer. Marked
   ``requires_lfs`` so CI can skip it; runs locally for devs who still have
   stale LFS pointers from before the migration.

Both checks are pure-Python, no DB, fast — safe to run in CI.
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

# LFS pointer files always start with this exact line.
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"

# A real LFS pointer is ~130–150 bytes; anything beyond is definitely real content.
_LFS_POINTER_MAX_BYTES = 200


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


@pytest.mark.requires_lfs
def test_no_lfs_pointer_files_committed() -> None:
    """No fixture should be present as a tiny LFS pointer file in the working tree.

    Holdover guard: if this fails, the dev cloned with the old LFS smudge filter
    active and the fixture is the ~133-byte pointer instead of real content. After
    issue #322 the heavy fixtures live in a GitHub Release bundle, not LFS — so a
    pointer file here is a legacy state. Run:

        python scripts/pg-seed-data/sync_fixtures.py

    to fetch the bundle and replace pointers with real content.

    Marked `requires_lfs` so CI can skip it.
    """
    pointers: list[str] = []
    for fixture in _fixture_files():
        size = fixture.stat().st_size
        if size > _LFS_POINTER_MAX_BYTES:
            continue
        with fixture.open("rb") as f:
            head = f.read(len(_LFS_POINTER_PREFIX))
        if head == _LFS_POINTER_PREFIX:
            rel = fixture.relative_to(_REPO_ROOT).as_posix()
            pointers.append(f"  {rel} ({size} bytes)")
    if pointers:
        pointer_block = "\n".join(pointers)
        msg = (
            "\nFixture(s) present as Git-LFS pointer files instead of real content.\n"
            "After issue #322 the heavy fixtures live in a GitHub Release bundle\n"
            "rather than LFS. Run:\n"
            "  python scripts/pg-seed-data/sync_fixtures.py\n"
            "to fetch the bundle and replace these pointers with real content.\n\n"
            f"Pointer files found:\n{pointer_block}"
        )
        pytest.fail(msg)
