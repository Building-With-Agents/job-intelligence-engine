"""JIE #226 — config-loaded API keys for ``POST /analytics/query`` (``X-API-Key`` header)."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    secret: str


def _json_load_api_keys_list(raw: str, *, source: str) -> list[object]:
    """Parse ``JIE_API_KEYS`` / file body; raise :class:`ValueError` with stable code on bad JSON."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "jie_api_keys_invalid_json: "
            + source
            + ' must be valid JSON (double-quoted keys). Example: [{"key_id":"local-dev-1",'
            + '"secret":"local-dev-secret"}]. In bash, wrap the whole value in single quotes '
            + "when using export (avoids `!` history expansion)."
        ) from exc


def load_api_keys() -> list[ApiKeyRecord]:
    """Load rotate-friendly keys from ``JIE_API_KEYS`` JSON or ``JIE_API_KEYS_FILE`` (YAML/JSON).

    Returns an empty list when neither source is set (caller decides fail-closed vs dev escape).
    """
    raw = os.getenv("JIE_API_KEYS", "").strip()
    if raw:
        data = _json_load_api_keys_list(raw, source="JIE_API_KEYS")
        if not isinstance(data, list):
            raise ValueError("JIE_API_KEYS must be a JSON list of objects with key_id and secret")
        return _records_from_list(data, source="JIE_API_KEYS")

    path = os.getenv("JIE_API_KEYS_FILE", "").strip()
    if not path:
        return []

    p = Path(path)
    blob = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover — PyYAML is in requirements.txt
            raise ValueError("JIE_API_KEYS_FILE requires PyYAML to be installed") from exc
        data = yaml.safe_load(blob) or []
    else:
        data = _json_load_api_keys_list(blob, source="JIE_API_KEYS_FILE")

    if not isinstance(data, list):
        raise ValueError("JIE_API_KEYS_FILE must contain a JSON/YAML list of objects with key_id and secret")
    return _records_from_list(data, source="JIE_API_KEYS_FILE")


def _records_from_list(data: list[object], *, source: str) -> list[ApiKeyRecord]:
    out: list[ApiKeyRecord] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"{source} entries must be objects")
        kid = str(item.get("key_id", "")).strip()
        sec = str(item.get("secret", "")).strip()
        if not kid or not sec:
            raise ValueError(f"{source} each entry requires non-empty key_id and secret")
        out.append(ApiKeyRecord(key_id=kid, secret=sec))
    return out


def validate_api_key_header(*, provided: str, allowed: list[ApiKeyRecord]) -> ApiKeyRecord:
    """Match ``X-API-Key`` against allowlist using constant-time digest compare."""
    if not provided.strip():
        raise ValueError("missing_api_key")
    if not allowed:
        raise ValueError("server_misconfigured_no_keys")
    provided_b = provided.encode("utf-8")
    matched: ApiKeyRecord | None = None
    for rec in allowed:
        if secrets.compare_digest(provided_b, rec.secret.encode("utf-8")):
            matched = rec
            break
    if matched is None:
        raise ValueError("invalid_api_key")
    return matched
