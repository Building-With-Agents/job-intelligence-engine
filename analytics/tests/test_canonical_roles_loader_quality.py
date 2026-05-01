"""Clustering loader quality gate (#327 Phase 4 wiring)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from analytics.canonical_roles.loader import load_posting_cluster_features


def test_load_posting_cluster_features_includes_quality_predicate_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_execute(sql, params=None, **_kwargs):
        captured["sql"] = str(sql)
        captured["params"] = dict(params) if params else {}

        class _R:
            def mappings(self):
                return self

            def all(self):
                return []

        return _R()

    import analytics.canonical_roles.loader as cr_loader

    monkeypatch.setattr(cr_loader, "cluster_input_min_quality_score", lambda: 0.48)
    session = MagicMock()
    session.execute = _fake_execute  # type: ignore[method-assign]

    load_posting_cluster_features(session)

    assert "jp.quality_score >=" in captured["sql"]
    assert captured["params"].get("min_quality_score") == 0.48
