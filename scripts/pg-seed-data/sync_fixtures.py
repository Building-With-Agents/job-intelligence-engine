# ruff: noqa: T201
"""Download heavy seed fixtures from a GitHub Release bundle.

Closes #322 — replaces Git LFS for the six >5 MB fixtures whose download
would otherwise burn the org-wide LFS bandwidth budget. Small fixtures
remain plain JSON in git for diffability; this script only handles the
bundled heavy ones.

Workflow
--------
1. Read `fixtures-manifest.json` for the pinned release tag and bundle SHA256.
2. If every heavy fixture is already present locally and matches manifest
   `included_tables`, exit early — idempotent.
3. Otherwise: `gh release download <tag> --pattern <bundle>` into a tmpdir,
   verify SHA256, extract into `scripts/pg-seed-data/fixtures/`.

Usage::

    python scripts/pg-seed-data/sync_fixtures.py
    python scripts/pg-seed-data/sync_fixtures.py --force   # re-extract even if local

Exits 0 on success, non-zero on verification or download failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import zstandard as zstd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures"
_MANIFEST_PATH = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures-manifest.json"


def _read_manifest() -> dict:
    if not _MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found at {_MANIFEST_PATH}", file=sys.stderr)
        sys.exit(2)
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _local_fixtures_match(manifest: dict) -> bool:
    expected_tables: list[str] = manifest.get("included_tables", [])
    for table in expected_tables:
        path = _FIXTURES_DIR / f"{table}.json"
        if not path.is_file() or path.stat().st_size < 1024:
            return False
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gh_download(release_tag: str, pattern: str, dest_dir: Path) -> Path:
    cmd = [
        "gh",
        "release",
        "download",
        release_tag,
        "--repo",
        "Building-With-Agents/job-intelligence-engine",
        "--pattern",
        pattern,
        "--dir",
        str(dest_dir),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: gh release download failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(3)
    matches = list(dest_dir.glob(pattern))
    if len(matches) != 1:
        print(f"ERROR: expected exactly one bundle file, got {len(matches)}: {matches}", file=sys.stderr)
        sys.exit(4)
    return matches[0]


def _extract_zst_tar(bundle_path: Path, target_dir: Path, expected_tables: list[str]) -> None:
    """Decompress .tar.zst stream and extract entries matching expected fixtures."""
    target_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{table}.json" for table in expected_tables}
    extracted: set[str] = set()

    dctx = zstd.ZstdDecompressor()
    with bundle_path.open("rb") as compressed, dctx.stream_reader(compressed) as reader:
        tar_bytes = BytesIO(reader.read())
    with tarfile.open(fileobj=tar_bytes, mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if name not in expected_names:
                print(f"  skip unexpected entry: {member.name}")
                continue
            extracted.add(name)
            extracted_file = tar.extractfile(member)
            if extracted_file is None:
                continue
            (target_dir / name).write_bytes(extracted_file.read())
            print(f"  extracted: {name} ({member.size:,} bytes)")

    missing = expected_names - extracted
    if missing:
        print(f"ERROR: bundle missing expected fixtures: {sorted(missing)}", file=sys.stderr)
        sys.exit(5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-download even if local fixtures look complete")
    args = parser.parse_args()

    manifest = _read_manifest()
    release_tag = manifest["release_tag"]
    bundle_filename = manifest["bundle_filename"]
    expected_sha = manifest["bundle_sha256"]
    expected_tables: list[str] = manifest["included_tables"]

    print("=" * 60)
    print(f"Fixture sync — release {release_tag}")
    print(f"Bundle: {bundle_filename}")
    print(f"Tables: {', '.join(expected_tables)}")
    print("=" * 60)

    if not args.force and _local_fixtures_match(manifest):
        print("All heavy fixtures already present locally. Use --force to re-download.")
        return 0

    with tempfile.TemporaryDirectory(prefix="jie-fixtures-") as tmpdir:
        tmp_path = Path(tmpdir)
        bundle_path = _gh_download(release_tag, bundle_filename, tmp_path)
        actual_sha = _sha256(bundle_path)
        if actual_sha != expected_sha:
            print(
                f"ERROR: SHA256 mismatch\n  expected: {expected_sha}\n  got:      {actual_sha}",
                file=sys.stderr,
            )
            return 6
        print(f"  SHA256 verified: {actual_sha}")
        _extract_zst_tar(bundle_path, _FIXTURES_DIR, expected_tables)

    print("\nDone. Heavy fixtures synced to scripts/pg-seed-data/fixtures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
