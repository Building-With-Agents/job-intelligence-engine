"""Unit tests for pure helpers in scripts.verify_local_db_health (no database)."""

from __future__ import annotations

from scripts.verify_local_db_health import fraction, gap_triggers_warn


def test_fraction_zero_denom() -> None:
    assert fraction(1, 0) == 0.0
    assert fraction(0, 0) == 0.0


def test_fraction_basic() -> None:
    assert fraction(1, 4) == 0.25
    assert fraction(3, 10) == 0.3


def test_gap_triggers_warn_abs() -> None:
    assert gap_triggers_warn(100, 1000, max_abs=50, max_rel=0.02) is True
    # Below abs cap and below rel vs larger_base
    assert gap_triggers_warn(15, 1000, max_abs=50, max_rel=0.02) is False


def test_gap_triggers_warn_rel() -> None:
    assert gap_triggers_warn(30, 100, max_abs=50, max_rel=0.02) is True
    assert gap_triggers_warn(1, 100, max_abs=50, max_rel=0.02) is False
