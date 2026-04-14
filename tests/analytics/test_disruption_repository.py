"""Unit tests for ``analytics.disruption.repository`` read paths."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from analytics.disruption.models import TEMPORAL_PERIOD_SEQUENCE
from analytics.disruption.repository import (
    DisruptionFingerprintRepository,
    DisruptionRepositoryQueryError,
    _agent_table_prefix,
    _ai_keyword_density,
    _mix_from_counts,
    _normalize_period_aggregate_rows,
    _sorted_periods,
)


def _mapping_result(rows: list[dict]) -> MagicMock:
    out = MagicMock()
    out.mappings.return_value.all.return_value = rows
    return out


def test_fetch_canonical_roles_returns_empty_without_session() -> None:
    repo = DisruptionFingerprintRepository()
    assert repo.fetch_canonical_roles(None) == []


def test_fetch_period_snapshots_returns_empty_without_session() -> None:
    repo = DisruptionFingerprintRepository()
    assert repo.fetch_period_snapshots("any-role", None) == []


def test_fetch_period_snapshots_returns_empty_for_blank_role_id() -> None:
    session = MagicMock()
    repo = DisruptionFingerprintRepository()
    assert repo.fetch_period_snapshots("", session) == []
    assert repo.fetch_period_snapshots("   ", session) == []
    session.execute.assert_not_called()


def test_jie_sql_schema_env_overrides_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIE_SQL_SCHEMA", "public")
    assert _agent_table_prefix(MagicMock()) == "public."


def test_jie_sql_schema_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIE_SQL_SCHEMA", "not-valid!")
    with pytest.raises(DisruptionRepositoryQueryError, match="JIE_SQL_SCHEMA"):
        _agent_table_prefix(MagicMock())


def test_fetch_canonical_roles_ordered_strings() -> None:
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = ["role-z", "role-a"]
    session.execute.return_value = exec_result

    out = DisruptionFingerprintRepository().fetch_canonical_roles(session)
    assert out == ["role-z", "role-a"]
    session.execute.assert_called_once()


def test_fetch_period_snapshots_merges_period_skill_tool_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "analytics.disruption.repository.get_spam_thresholds",
        lambda: (0.7, 0.9),
    )
    period_rows = [
        {
            "temporal_period": "agentic_era",
            "posting_count": 4,
            "ai_relevance_avg": None,
            "responsibility_density": 0.15,
        },
        {
            "temporal_period": "post_gpt4",
            "posting_count": 10,
            "ai_relevance_avg": 0.8,
            "responsibility_density": 0.25,
        },
    ]
    skill_rows = [{"temporal_period": "post_gpt4", "label": "python", "cnt": 5}]
    tool_rows = [{"temporal_period": "post_gpt4", "label": "ChatGPT", "cnt": 2}]
    task_rows = [{"temporal_period": "post_gpt4", "label": "review code", "cnt": 3}]

    session = MagicMock()
    session.execute.side_effect = [
        _mapping_result(period_rows),
        _mapping_result(skill_rows),
        _mapping_result(tool_rows),
        _mapping_result(task_rows),
    ]

    snaps = DisruptionFingerprintRepository().fetch_period_snapshots("role-1", session)
    assert session.execute.call_count == 4

    periods = [s.temporal_period for s in snaps]
    assert periods == _sorted_periods({"post_gpt4", "agentic_era"})
    assert periods == list(TEMPORAL_PERIOD_SEQUENCE[2:])

    post = next(s for s in snaps if s.temporal_period == "post_gpt4")
    assert post.posting_count == 10
    assert post.skill_mix["python"] == pytest.approx(0.5)
    assert post.tool_mix["ChatGPT"] == pytest.approx(0.2)
    assert post.task_mix["review code"] == pytest.approx(0.3)
    assert post.ai_requirement_density == pytest.approx(0.8)
    assert post.responsibility_density == pytest.approx(0.25)
    assert post.has_observed_data is True

    agentic = next(s for s in snaps if s.temporal_period == "agentic_era")
    assert agentic.posting_count == 4
    assert agentic.skill_mix == {}
    assert agentic.ai_requirement_density == 0.0
    assert agentic.responsibility_density == pytest.approx(0.15)
    assert agentic.has_observed_data is True


def test_fetch_period_snapshots_wraps_sqlalchemy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "analytics.disruption.repository.get_spam_thresholds",
        lambda: (0.7, 0.9),
    )
    session = MagicMock()
    session.execute.side_effect = SQLAlchemyError("connection reset")
    with pytest.raises(DisruptionRepositoryQueryError) as excinfo:
        DisruptionFingerprintRepository().fetch_period_snapshots("role-1", session)
    assert "period_agg" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, SQLAlchemyError)


def test_fetch_canonical_roles_wraps_sqlalchemy_error() -> None:
    session = MagicMock()
    session.execute.side_effect = SQLAlchemyError("read only")
    with pytest.raises(DisruptionRepositoryQueryError, match="fetch_canonical_roles"):
        DisruptionFingerprintRepository().fetch_canonical_roles(session)


def test_fetch_period_snapshots_empty_period_query_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "analytics.disruption.repository.get_spam_thresholds",
        lambda: (0.7, 0.9),
    )
    session = MagicMock()
    session.execute.return_value = _mapping_result([])
    assert DisruptionFingerprintRepository().fetch_period_snapshots("r", session) == []
    session.execute.assert_called_once()


def test_normalize_period_aggregate_rows_skips_unknown_period_and_bad_counts() -> None:
    rows = [
        {"temporal_period": "post_gpt4", "posting_count": 3, "ai_relevance_avg": None, "responsibility_density": 0.1},
        {"temporal_period": "unknown_bucket", "posting_count": 99, "ai_relevance_avg": None, "responsibility_density": 0.0},
        {"temporal_period": "pre_chatgpt", "posting_count": "not-an-int", "ai_relevance_avg": None, "responsibility_density": 0.0},
        {"temporal_period": "early_genai", "posting_count": 0, "ai_relevance_avg": None, "responsibility_density": 0.0},
    ]
    out = _normalize_period_aggregate_rows(rows)
    assert set(out.keys()) == {"post_gpt4"}
    assert out["post_gpt4"][0] == 3


def test_sorted_periods_and_mix_helpers() -> None:
    assert _sorted_periods({"post_gpt4", "pre_chatgpt"}) == ["pre_chatgpt", "post_gpt4"]
    rows = [{"temporal_period": "post_gpt4", "label": "a", "cnt": 2}]
    assert _mix_from_counts(rows, {"post_gpt4": 8}) == {"post_gpt4": {"a": 0.25}}
    d = _ai_keyword_density({"python": 0.5, "machine learning": 0.5}, {"ChatGPT": 1.0})
    assert 0.0 < d <= 1.0
