"""Guard tests for seed-fixture LFS compliance (issue #251).

Two invariants this PR (#285) introduced and that future re-exports must hold:

1. **Size guard.** Any committed fixture larger than `_PLAIN_TEXT_MAX_BYTES`
   must be tracked through Git LFS via `.gitattributes`. Without this, the next
   re-export of a heavy table silently bloats the git history and hits GitHub's
   50 MB warning / 100 MB hard block.

2. **LFS pointer detection.** No fixture file in the working tree should be a
   ~133-byte LFS pointer file (the placeholder Git ships when `git lfs install`
   was never run on this clone). If pytest sees one, the dev forgot to install
   LFS — better to fail loudly here than silently load `[]` during seed.

Both checks are pure-Python, no DB, fast — safe to run in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures"
_GITATTRIBUTES = _REPO_ROOT / ".gitattributes"

# Plain-text fixtures may not exceed this size; anything heavier MUST be LFS-tracked.
# Set conservatively below GitHub's 50 MB warning to leave slop for one more
# pipeline run before someone notices and adds the file to .gitattributes.
_PLAIN_TEXT_MAX_BYTES = 40 * 1024 * 1024  # 40 MB

# LFS pointer files always start with this exact line.
_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"

# A real LFS pointer is ~130–150 bytes; anything beyond this is definitely real content.
_LFS_POINTER_MAX_BYTES = 200


def _lfs_tracked_paths() -> set[str]:
    """Parse .gitattributes for paths routed through `filter=lfs`."""
    if not _GITATTRIBUTES.exists():
        return set()
    tracked: set[str] = set()
    for line in _GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "filter=lfs" not in line:
            continue
        # Format: "<path-or-pattern> filter=lfs diff=lfs merge=lfs -text"
        path = line.split()[0]
        tracked.add(path)
    return tracked


def _fixture_files() -> list[Path]:
    if not _FIXTURES_DIR.is_dir():
        pytest.skip(f"Fixtures dir not present: {_FIXTURES_DIR}")
    return sorted(p for p in _FIXTURES_DIR.iterdir() if p.is_file() and p.suffix == ".json")


def test_heavy_fixtures_are_lfs_tracked() -> None:
    """Any fixture above the plain-text size ceiling must appear in .gitattributes."""
    tracked = _lfs_tracked_paths()
    offenders: list[str] = []
    for fixture in _fixture_files():
        size = fixture.stat().st_size
        if size <= _PLAIN_TEXT_MAX_BYTES:
            continue
        rel = fixture.relative_to(_REPO_ROOT).as_posix()
        if rel not in tracked:
            offenders.append(f"  {rel} ({size / 1024 / 1024:.1f} MB)")
    if offenders:
        offender_block = "\n".join(offenders)
        threshold_mb = _PLAIN_TEXT_MAX_BYTES / 1024 / 1024
        msg = (
            f"\nFixture files exceed {threshold_mb:.0f} MB without being Git-LFS-tracked.\n"
            "Add each one to .gitattributes via:\n"
            '  git lfs track "<path>"\n'
            "Then commit .gitattributes BEFORE re-staging the fixture so the LFS\n"
            "filter applies. See scripts/pg-seed-data/README.md for details.\n\n"
            f"Offenders:\n{offender_block}"
        )
        pytest.fail(msg)


@pytest.mark.requires_lfs
def test_no_lfs_pointer_files_committed() -> None:
    """No fixture should be present as a tiny LFS pointer file in the working tree.

    If this fails, the dev cloned without `git lfs install` and the fixture is the
    133-byte pointer instead of real content. Seeding will silently load `[]`.

    Marked `requires_lfs` so CI can skip it (CI does not pull LFS to conserve the
    GitHub LFS bandwidth budget). Devs must run `git lfs install` + `git lfs pull`
    locally and run pytest without `-m "not requires_lfs"` to exercise this guard.
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
            "This means `git lfs install` was never run on this clone, or the\n"
            "smudge filter failed. Run:\n"
            "  git lfs install\n"
            "  git lfs pull\n"
            "and re-run pytest. See scripts/pg-seed-data/README.md.\n\n"
            f"Pointer files found:\n{pointer_block}"
        )
        pytest.fail(msg)
