"""Typed YAML config loader with legacy env-var override support.

Reads YAML files from ``config/`` at the repo root. **YAML is the source of
truth** — every accessor declares a file + dotted key path; the value MUST
exist in the YAML or the loader raises :class:`ConfigError`. Function-level
defaults are deliberately not supported (issue #210): if you can write the
default in code, you can write it in YAML where reviewers see it.

For genuinely optional values (a knob that can legitimately be unset), use
``get_optional_*`` and return ``None`` when YAML stores ``null``/``~``.

Each typed accessor takes a file stem, a dotted key path, and an optional
legacy env-var name. When the legacy env var is set in the process
environment, it wins for the duration of the deprecation window; the
loader emits a one-shot ``structlog.warning("config_env_override_used", ...)``
so ops can spot deployments still relying on env overrides. After 1–2
release cycles the env override branch is removed in a follow-up PR.

If the env value is set but fails parsing or range validation, the loader
falls back to the YAML value rather than failing — so an operator typo
in env never silently disables the YAML setting.

Usage from a subsystem ``_config.py``::

    from common.config_loader import get_int, cached_accessor

    @cached_accessor
    def query_row_limit() -> int:
        return get_int(file="analytics", key="analytics.query.row_limit",
                       env="ANALYTICS_QUERY_LIMIT", minimum=1)
"""

from __future__ import annotations

import functools
import os
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

import structlog
import yaml

log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _REPO_ROOT / "config"
_lock = threading.Lock()

_REGISTERED_ACCESSORS: list[Any] = []
_OVERRIDE_WARNED: set[tuple[str, str, str]] = set()


class ConfigError(RuntimeError):
    """Raised when a required config key is missing or fails validation in both env and YAML."""


@functools.lru_cache(maxsize=None)
def load_yaml(name: str) -> dict[str, Any]:
    """Load and cache a YAML file from ``config/<name>.yaml``.

    Missing files return an empty dict (the loader is fail-soft for
    not-yet-migrated files). The top-level node must be a mapping.
    """
    path = _CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    return data


def reload_all() -> None:
    """Test hook — clears the YAML cache and every registered accessor cache.

    Production callers must not call this; config is process-scoped immutable.
    """
    with _lock:
        load_yaml.cache_clear()
        _OVERRIDE_WARNED.clear()
        for fn in _REGISTERED_ACCESSORS:
            fn.cache_clear()


def _resolve(file: str, dotted_key: str) -> Any:
    cur: Any = load_yaml(file)
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _maybe_warn_override(env: str, file: str, key: str) -> None:
    sentinel = (env, file, key)
    if sentinel in _OVERRIDE_WARNED:
        return
    _OVERRIDE_WARNED.add(sentinel)
    log.warning(
        "config_env_override_used",
        var=env,
        file=file,
        key=key,
        note="Legacy env override; planned removal post-deprecation (issue #210).",
    )


def _env_value(env: str | None, file: str, key: str) -> str | None:
    if not env:
        return None
    raw = os.getenv(env)
    if raw is None or raw == "":
        return None
    _maybe_warn_override(env, file, key)
    return raw


def _missing(file: str, key: str, env: str | None) -> ConfigError:
    yaml_path = f"config/{file}.yaml -> {key}"
    env_hint = f" (or env override `{env}`)" if env else ""
    return ConfigError(
        f"Missing required config value: {yaml_path}{env_hint}. "
        f"Add the key to the YAML file. Defaults must live in YAML, not in code."
    )


# ---------------------------------------------------------------------------
# int / optional int
# ---------------------------------------------------------------------------

def _try_int(
    candidate: Any,
    *,
    minimum: int | None,
    maximum: int | None,
) -> int | None:
    try:
        parsed = int(candidate)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def get_int(
    *,
    file: str,
    key: str,
    env: str | None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Resolve a required int.

    Order: env (if valid) → YAML (if valid) → :class:`ConfigError`.
    Range failure on env falls through to YAML; range failure on YAML
    raises (the YAML is wrong and must be fixed).
    """
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        v = _try_int(raw_env, minimum=minimum, maximum=maximum)
        if v is not None:
            return v
    yaml_val = _resolve(file, key)
    if yaml_val is None:
        raise _missing(file, key, env)
    v = _try_int(yaml_val, minimum=minimum, maximum=maximum)
    if v is None:
        raise ConfigError(
            f"config/{file}.yaml -> {key}: value {yaml_val!r} fails int "
            f"validation (min={minimum}, max={maximum}). Fix the YAML."
        )
    return v


def get_optional_int(
    *,
    file: str,
    key: str,
    env: str | None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Resolve an optional int. ``null`` / missing in YAML returns ``None``."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        v = _try_int(raw_env, minimum=minimum, maximum=maximum)
        if v is not None:
            return v
    yaml_val = _resolve(file, key)
    if yaml_val is None:
        return None
    return _try_int(yaml_val, minimum=minimum, maximum=maximum)


# ---------------------------------------------------------------------------
# float / optional float
# ---------------------------------------------------------------------------

def _try_float(
    candidate: Any,
    *,
    minimum: float | None,
    maximum: float | None,
) -> float | None:
    try:
        parsed = float(candidate)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def get_float(
    *,
    file: str,
    key: str,
    env: str | None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Resolve a required float (see :func:`get_int` for fallback semantics)."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        v = _try_float(raw_env, minimum=minimum, maximum=maximum)
        if v is not None:
            return v
    yaml_val = _resolve(file, key)
    if yaml_val is None:
        raise _missing(file, key, env)
    v = _try_float(yaml_val, minimum=minimum, maximum=maximum)
    if v is None:
        raise ConfigError(
            f"config/{file}.yaml -> {key}: value {yaml_val!r} fails float "
            f"validation (min={minimum}, max={maximum}). Fix the YAML."
        )
    return v


def get_optional_float(
    *,
    file: str,
    key: str,
    env: str | None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    """Resolve an optional float. ``null`` / missing in YAML returns ``None``."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        v = _try_float(raw_env, minimum=minimum, maximum=maximum)
        if v is not None:
            return v
    yaml_val = _resolve(file, key)
    if yaml_val is None:
        return None
    return _try_float(yaml_val, minimum=minimum, maximum=maximum)


# ---------------------------------------------------------------------------
# bool
# ---------------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "on", "t", "y"}
_FALSE = {"0", "false", "no", "off", "f", "n", ""}


def _try_bool(candidate: Any) -> bool | None:
    if isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, str):
        s = candidate.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


def get_bool(*, file: str, key: str, env: str | None) -> bool:
    """Resolve a required bool (see :func:`get_int` for fallback semantics)."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        v = _try_bool(raw_env)
        if v is not None:
            return v
    yaml_val = _resolve(file, key)
    if yaml_val is None:
        raise _missing(file, key, env)
    v = _try_bool(yaml_val)
    if v is None:
        raise ConfigError(
            f"config/{file}.yaml -> {key}: value {yaml_val!r} is not a recognized "
            f"boolean. Use one of: true/false, 1/0, yes/no, on/off."
        )
    return v


# ---------------------------------------------------------------------------
# str / optional str
# ---------------------------------------------------------------------------

def get_str(*, file: str, key: str, env: str | None) -> str:
    """Resolve a required string."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        return raw_env
    val = _resolve(file, key)
    if val is None:
        raise _missing(file, key, env)
    return str(val)


def get_optional_str(*, file: str, key: str, env: str | None) -> str | None:
    """Resolve an optional string. ``null`` / missing in YAML returns ``None``."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        return raw_env
    val = _resolve(file, key)
    if val is None:
        return None
    return str(val)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def get_list(
    *,
    file: str,
    key: str,
    env: str | None,
    separator: str = ",",
) -> list[str]:
    """Resolve a required list (string list). Empty YAML list ``[]`` is valid."""
    raw_env = _env_value(env, file, key)
    if raw_env is not None:
        return [x.strip() for x in raw_env.split(separator) if x.strip()]
    val = _resolve(file, key)
    if val is None:
        raise _missing(file, key, env)
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split(separator) if x.strip()]
    raise ConfigError(
        f"config/{file}.yaml -> {key}: value {val!r} must be a YAML list "
        f"or comma-separated string."
    )


# ---------------------------------------------------------------------------
# accessor decorator
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])


def cached_accessor(fn: _F) -> _F:
    """Decorator: ``lru_cache(maxsize=1)`` the accessor and register for ``reload_all()``."""
    wrapped = functools.lru_cache(maxsize=1)(fn)
    _REGISTERED_ACCESSORS.append(wrapped)
    return wrapped  # type: ignore[return-value]


__all__ = [
    "cached_accessor",
    "ConfigError",
    "get_bool",
    "get_float",
    "get_int",
    "get_list",
    "get_optional_float",
    "get_optional_int",
    "get_optional_str",
    "get_str",
    "load_yaml",
    "reload_all",
]
