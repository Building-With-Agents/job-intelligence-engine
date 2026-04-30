# ruff: noqa: T201
"""Bundle heavy seed fixtures and publish as a GitHub Release asset.

Closes #322 — admin tool. Run after `export_fixtures.py` regenerates
`fixtures/*.json` from the live source-of-truth DB. Produces a single
`fixtures-vN.tar.zst` and creates the corresponding GitHub Release,
then updates `fixtures-manifest.json` so devs can `sync_fixtures.py`
to pull the new bundle.

Usage::

    # Default: derive next tag from the existing manifest's release_tag
    python scripts/pg-seed-data/publish_fixtures.py

    # Explicit tag (e.g., for a re-cut)
    python scripts/pg-seed-data/publish_fixtures.py --tag fixtures-v2

    # Dry-run: build bundle, print SHA, but do NOT call gh release create
    python scripts/pg-seed-data/publish_fixtures.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import zstandard as zstd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures"
_MANIFEST_PATH = _REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures-manifest.json"

# Fixtures that go in the release bundle. Mirrors the prior LFS tracking set.
# Add a new entry here when a fixture grows past ~5 MB; remove if it shrinks.
BUNDLED_TABLES: list[str] = [
    "extracted_intelligence",
    "raw_ingested_jobs",
    "job_postings",
    "normalized_jobs",
    "llm_audit_log",
    "postal_geo_data",
]

_REPO_FULL_NAME = "Building-With-Agents/job-intelligence-engine"


def _next_tag(current_tag: str) -> str:
    m = re.fullmatch(r"fixtures-v(\d+)", current_tag)
    if not m:
        return "fixtures-v1"
    return f"fixtures-v{int(m.group(1)) + 1}"


def _verify_inputs() -> None:
    missing = [t for t in BUNDLED_TABLES if not (_FIXTURES_DIR / f"{t}.json").is_file()]
    if missing:
        print(f"ERROR: required fixtures missing under {_FIXTURES_DIR}: {missing}", file=sys.stderr)
        sys.exit(2)


def _build_bundle(target_path: Path) -> tuple[int, int]:
    """Tar the BUNDLED_TABLES fixtures and zstd-compress to target_path.

    Returns (uncompressed_bytes, compressed_bytes).
    """
    raw_buffer = io.BytesIO()
    uncompressed = 0
    with tarfile.open(fileobj=raw_buffer, mode="w") as tar:
        for table in BUNDLED_TABLES:
            src = _FIXTURES_DIR / f"{table}.json"
            arcname = f"{table}.json"
            tar.add(src, arcname=arcname)
            uncompressed += src.stat().st_size

    raw_bytes = raw_buffer.getvalue()
    cctx = zstd.ZstdCompressor(level=19)
    compressed = cctx.compress(raw_bytes)
    target_path.write_bytes(compressed)
    return uncompressed, len(compressed)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gh_release_create(tag: str, bundle_path: Path, notes: str) -> None:
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        str(bundle_path),
        "--repo",
        _REPO_FULL_NAME,
        "--title",
        f"Seed fixtures bundle {tag}",
        "--notes",
        notes,
    ]
    print(f"Running: {' '.join(cmd[:6])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: gh release create failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(3)
    print(result.stdout.strip())


def _write_manifest(tag: str, bundle_filename: str, sha256: str) -> None:
    manifest = {
        "release_tag": tag,
        "bundle_filename": bundle_filename,
        "bundle_sha256": sha256,
        "included_tables": BUNDLED_TABLES,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "repo": _REPO_FULL_NAME,
    }
    _MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nManifest written: {_MANIFEST_PATH.relative_to(_REPO_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Override the release tag (default: increment manifest's tag)")
    parser.add_argument("--dry-run", action="store_true", help="Build bundle, print SHA, skip release create")
    args = parser.parse_args()

    _verify_inputs()

    if args.tag:
        tag = args.tag
    elif _MANIFEST_PATH.exists():
        tag = _next_tag(json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")).get("release_tag", "fixtures-v0"))
    else:
        tag = "fixtures-v1"

    bundle_filename = f"{tag}.tar.zst"
    bundle_path = _REPO_ROOT / bundle_filename

    print("=" * 60)
    print(f"Publishing fixtures bundle {tag}")
    print("=" * 60)

    uncompressed, compressed = _build_bundle(bundle_path)
    sha = _sha256(bundle_path)
    print(f"\nBundle: {bundle_path.name}")
    print(f"  Uncompressed: {uncompressed:>14,} bytes")
    print(f"  Compressed:   {compressed:>14,} bytes")
    print(f"  Ratio:        {compressed / uncompressed:.2%}")
    print(f"  SHA256:       {sha}")

    if args.dry_run:
        print("\nDry run: bundle and SHA computed; release NOT created. Bundle file kept for inspection.")
        return 0

    notes = (
        f"Auto-generated heavy seed fixtures bundle.\n\n"
        f"- Tables: `{', '.join(BUNDLED_TABLES)}`\n"
        f"- Uncompressed: {uncompressed:,} bytes\n"
        f"- Compressed:   {compressed:,} bytes\n"
        f"- SHA256:       `{sha}`\n\n"
        f"Devs: run `python scripts/pg-seed-data/sync_fixtures.py` after pulling latest "
        f"`development` to refresh local fixtures."
    )
    _gh_release_create(tag, bundle_path, notes)
    _write_manifest(tag, bundle_filename, sha)

    bundle_path.unlink(missing_ok=True)
    print(f"\nLocal bundle file removed: {bundle_path.name}")
    print(f"\nNext: commit {_MANIFEST_PATH.relative_to(_REPO_ROOT)} and push.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
