"""Meta-test: every YAML leaf in ``config/*.yaml`` carries a ``# Env override:`` comment.

Prevents future leaves from sneaking in undocumented. Allowlist below covers
keys that intentionally have no legacy env override (purely YAML-only).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _REPO_ROOT / "config"

# Files with the new YAML-loader convention. ``ingestion_queries.yaml`` is a
# pre-existing data file (query catalogue, not a tuning-knob registry) and
# is intentionally excluded.
_REGISTRY_FILES = (
    "analytics.yaml",
    "clustering.yaml",
    "enrichment.yaml",
    "eval.yaml",
    "ingestion.yaml",
    "laborpulse.yaml",
    "llm.yaml",
    "llm_costs.yaml",
    "pipeline.yaml",
    "skills_extraction.yaml",
)

# Keys that intentionally have no legacy env override (the dotted-path full
# key from the YAML file).
_NO_ENV_OVERRIDE_ALLOWLIST: set[str] = set()

_ENV_OVERRIDE_RE = re.compile(r"#\s*Env override:\s*([A-Z][A-Z0-9_]*)")


def _walk_leaves(node: object, path: list[str]) -> list[tuple[str, object]]:
    """Yield (dotted_key, value) for every scalar leaf in a YAML mapping."""
    out: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_leaves(v, path + [str(k)]))
    elif isinstance(node, list):
        # YAML lists are leaves (we treat the list itself as the value).
        out.append((".".join(path), node))
    else:
        out.append((".".join(path), node))
    return out


def _collect_env_override_lines(path: Path) -> set[str]:
    """Return the set of dotted-key paths whose preceding comments mention ``Env override``.

    Heuristic: walk the file line-by-line; for each ``key:`` line, look back
    at the contiguous comment block to see if any line matches
    ``# Env override: VAR``. If found, mark the YAML key path as documented.
    Indentation tracks the dotted path.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    documented: set[str] = set()
    stack: list[tuple[int, str]] = []  # (indent, key)
    pending_comments: list[str] = []
    key_re = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            pending_comments.append(stripped)
            continue
        if not stripped:
            pending_comments = []
            continue
        m = key_re.match(line)
        if not m:
            pending_comments = []
            continue
        indent = len(m.group(1))
        key = m.group(2)
        # Pop deeper-or-equal items from the stack.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        dotted = ".".join(k for _, k in stack)
        # If any pending comment matches ``Env override``, mark documented.
        if any(_ENV_OVERRIDE_RE.search(c) for c in pending_comments):
            documented.add(dotted)
        pending_comments = []
    return documented


@pytest.mark.parametrize("filename", _REGISTRY_FILES)
def test_yaml_file_loads_as_mapping(filename: str) -> None:
    path = _CONFIG_DIR / filename
    assert path.exists(), f"Missing config file: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict), f"{filename}: top-level YAML must be a mapping"


@pytest.mark.parametrize("filename", _REGISTRY_FILES)
def test_every_leaf_has_env_override_comment(filename: str) -> None:
    """Every scalar (or list) leaf must have a ``# Env override:`` comment OR
    appear in the allowlist."""
    path = _CONFIG_DIR / filename
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    documented = _collect_env_override_lines(path)
    leaves = _walk_leaves(data, [])
    leaf_keys = {dotted for dotted, _ in leaves}
    missing = []
    for dotted in sorted(leaf_keys):
        if dotted in documented:
            continue
        if dotted in _NO_ENV_OVERRIDE_ALLOWLIST:
            continue
        # Some leaves are intermediate dict keys (no env override expected for
        # the structural parent); only flag terminal scalar/list leaves.
        if dotted.count(".") < 1:
            # Top-level key (e.g. "pipeline" itself); skip — its children
            # are what we care about.
            continue
        missing.append(dotted)
    assert not missing, (
        f"{filename}: leaves without an `# Env override:` comment "
        f"(add one or extend _NO_ENV_OVERRIDE_ALLOWLIST):\n  - " + "\n  - ".join(missing)
    )
