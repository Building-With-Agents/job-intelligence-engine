"""Unit tests for ``common.config_loader``.

Defaults live in YAML — there are no Python-level defaults. Required
accessors raise :class:`ConfigError` when the YAML key is missing or its
value fails validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common import config_loader
from common.config_loader import (
    ConfigError,
    cached_accessor,
    get_bool,
    get_float,
    get_int,
    get_list,
    get_optional_float,
    get_optional_int,
    get_optional_str,
    get_str,
    load_yaml,
    reload_all,
)


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the loader to a tmp ``config/`` and clear caches between tests."""
    monkeypatch.setattr(config_loader, "_CONFIG_DIR", tmp_path)
    reload_all()
    yield tmp_path
    reload_all()


def _write(path: Path, name: str, body: str) -> None:
    (path / f"{name}.yaml").write_text(body, encoding="utf-8")


# ---------- load_yaml ----------

def test_load_yaml_missing_file_returns_empty(tmp_config_dir: Path) -> None:
    assert load_yaml("nonexistent") == {}


def test_load_yaml_parses_mapping(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: 1\n")
    assert load_yaml("demo") == {"a": {"b": 1}}


def test_load_yaml_rejects_non_mapping_top_level(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "- one\n- two\n")
    with pytest.raises(ValueError, match="top-level must be a mapping"):
        load_yaml("demo")


def test_load_yaml_caches_result(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 1\n")
    first = load_yaml("demo")
    _write(tmp_config_dir, "demo", "x: 999\n")
    second = load_yaml("demo")
    assert first == {"x": 1}
    assert second == {"x": 1}
    reload_all()
    assert load_yaml("demo") == {"x": 999}


# ---------- get_int (required, YAML-authoritative) ----------

def test_get_int_yaml_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: 42\n")
    assert get_int(file="demo", key="a.b", env=None) == 42


def test_get_int_env_overrides_yaml(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: 42\n")
    monkeypatch.setenv("DEMO_VAR", "100")
    assert get_int(file="demo", key="a.b", env="DEMO_VAR") == 100


def test_get_int_raises_when_missing_yaml_and_no_env(tmp_config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Missing required config value"):
        get_int(file="demo", key="a.b", env=None)


def test_get_int_raises_when_yaml_invalid_and_no_env(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: not_a_number\n")
    with pytest.raises(ConfigError, match="fails int validation"):
        get_int(file="demo", key="a.b", env=None)


def test_get_int_raises_when_yaml_below_minimum(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: -1\n")
    with pytest.raises(ConfigError, match="fails int validation"):
        get_int(file="demo", key="a.b", env=None, minimum=0)


def test_get_int_env_invalid_falls_back_to_yaml(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env value that fails parsing falls through to YAML; YAML wins."""
    _write(tmp_config_dir, "demo", "a:\n  b: 42\n")
    monkeypatch.setenv("DEMO_VAR", "not_a_number")
    assert get_int(file="demo", key="a.b", env="DEMO_VAR") == 42


def test_get_int_env_below_min_falls_back_to_yaml(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out-of-range env value falls through to YAML."""
    _write(tmp_config_dir, "demo", "a:\n  b: 5\n")
    monkeypatch.setenv("DEMO_VAR", "0")
    assert get_int(file="demo", key="a.b", env="DEMO_VAR", minimum=1) == 5


# ---------- get_optional_int ----------

def test_get_optional_int_returns_none_when_missing(tmp_config_dir: Path) -> None:
    assert get_optional_int(file="demo", key="missing", env=None) is None


def test_get_optional_int_returns_yaml_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 7\n")
    assert get_optional_int(file="demo", key="x", env=None) == 7


# ---------- get_float ----------

def test_get_float_yaml_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 0.92\n")
    assert get_float(file="demo", key="x", env=None) == 0.92


def test_get_float_raises_when_yaml_out_of_range(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 1.5\n")
    with pytest.raises(ConfigError, match="fails float validation"):
        get_float(file="demo", key="x", env=None, minimum=0.0, maximum=1.0)


def test_get_float_env_below_min_falls_back_to_yaml(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_config_dir, "demo", "x: 0.7\n")
    monkeypatch.setenv("DEMO_VAR", "-1")
    assert get_float(
        file="demo", key="x", env="DEMO_VAR", minimum=0.0, maximum=1.0
    ) == 0.7


# ---------- get_bool ----------

@pytest.mark.parametrize("raw,expected", [
    (True, True),
    (False, False),
    ("true", True),
    ("FALSE", False),
    ("1", True),
    ("0", False),
    ("yes", True),
    ("no", False),
    ("on", True),
    ("off", False),
])
def test_get_bool_yaml(tmp_config_dir: Path, raw: object, expected: bool) -> None:
    _write(
        tmp_config_dir, "demo",
        f"x: {raw!r}\n" if isinstance(raw, str) else f"x: {str(raw).lower()}\n",
    )
    assert get_bool(file="demo", key="x", env=None) is expected


def test_get_bool_env_overrides(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_config_dir, "demo", "x: false\n")
    monkeypatch.setenv("DEMO_BOOL", "1")
    assert get_bool(file="demo", key="x", env="DEMO_BOOL") is True


def test_get_bool_raises_when_yaml_invalid(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 'maybe'\n")
    with pytest.raises(ConfigError, match="not a recognized boolean"):
        get_bool(file="demo", key="x", env=None)


def test_get_bool_env_invalid_falls_back_to_yaml(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_config_dir, "demo", "x: true\n")
    monkeypatch.setenv("DEMO_VAR", "maybe")
    assert get_bool(file="demo", key="x", env="DEMO_VAR") is True


# ---------- get_str / get_optional_str ----------

def test_get_str_yaml_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: hello\n")
    assert get_str(file="demo", key="x", env=None) == "hello"


def test_get_str_raises_when_missing(tmp_config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Missing required config value"):
        get_str(file="demo", key="missing", env=None)


def test_get_optional_str_returns_none_when_missing(tmp_config_dir: Path) -> None:
    assert get_optional_str(file="demo", key="missing", env=None) is None


def test_get_optional_str_returns_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: present\n")
    assert get_optional_str(file="demo", key="x", env=None) == "present"


# ---------- get_optional_float ----------

def test_get_optional_float_returns_none(tmp_config_dir: Path) -> None:
    assert get_optional_float(file="demo", key="missing", env=None) is None


def test_get_optional_float_returns_yaml_value(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 0.5\n")
    assert get_optional_float(file="demo", key="x", env=None) == 0.5


# ---------- get_list ----------

def test_get_list_from_yaml_list(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x:\n  - a\n  - b\n  - c\n")
    assert get_list(file="demo", key="x", env=None) == ["a", "b", "c"]


def test_get_list_from_yaml_csv_string(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "x: 'a, b , c'\n")
    assert get_list(file="demo", key="x", env=None) == ["a", "b", "c"]


def test_get_list_env_csv_override(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_config_dir, "demo", "x:\n  - a\n")
    monkeypatch.setenv("DEMO_LIST", "x,y,z")
    assert get_list(file="demo", key="x", env="DEMO_LIST") == ["x", "y", "z"]


def test_get_list_raises_when_missing(tmp_config_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Missing required config value"):
        get_list(file="demo", key="missing", env=None)


# ---------- cached_accessor + reload_all ----------

def test_cached_accessor_caches_and_reloads(tmp_config_dir: Path) -> None:
    @cached_accessor
    def my_value() -> int:
        return get_int(file="demo", key="x", env=None)

    _write(tmp_config_dir, "demo", "x: 10\n")
    assert my_value() == 10

    _write(tmp_config_dir, "demo", "x: 20\n")
    assert my_value() == 10  # cached

    reload_all()
    assert my_value() == 20


# ---------- env override warning ----------

def test_env_override_emits_warning_once(
    tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import structlog

    structlog.configure(
        processors=[structlog.stdlib.render_to_log_kwargs],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    caplog.set_level(logging.WARNING, logger="common.config_loader")

    _write(tmp_config_dir, "demo", "x: 1\n")
    monkeypatch.setenv("WARN_VAR", "5")

    get_int(file="demo", key="x", env="WARN_VAR")
    get_int(file="demo", key="x", env="WARN_VAR")
    get_int(file="demo", key="x", env="WARN_VAR")

    override_records = [r for r in caplog.records if "config_env_override_used" in r.message]
    assert len(override_records) == 1


# ---------- dotted-key resolution ----------

def test_resolve_returns_none_when_path_missing(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a:\n  b: 1\n")
    with pytest.raises(ConfigError, match="Missing required config value"):
        get_int(file="demo", key="a.c.d", env=None)


def test_resolve_returns_none_when_intermediate_not_dict(tmp_config_dir: Path) -> None:
    _write(tmp_config_dir, "demo", "a: 5\n")
    with pytest.raises(ConfigError, match="Missing required config value"):
        get_int(file="demo", key="a.b", env=None)
