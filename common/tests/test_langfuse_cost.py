"""Tests for Langfuse cost attribution helpers (JIE #259)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from common.observability.langfuse import LangfuseTracer
from common.observability.langfuse_cost import langfuse_model_for_observation


class TestLangfuseModelForObservation:
    def test_prefers_deployment_over_api_model(self) -> None:
        assert (
            langfuse_model_for_observation("chat-gpt41mini", "gpt-4.1-mini-2025-04-14")
            == "chat-gpt41mini"
        )

    def test_registered_api_name_passthrough(self) -> None:
        assert langfuse_model_for_observation(None, "gpt-4.1-2025-04-14") == "gpt-4.1-2025-04-14"

    def test_unregistered_versioned_mini_maps_to_deployment(self) -> None:
        assert langfuse_model_for_observation(None, "gpt-4.1-mini-preview") == "chat-gpt41mini"

    def test_empty_returns_none(self) -> None:
        assert langfuse_model_for_observation(None, None) is None


class TestReportLangfuseUsage:
    @patch("analytics.query_engine.langfuse_utils.get_client")
    def test_sends_usage_details_cost_and_deployment_model(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        from analytics.query_engine.langfuse_utils import report_langfuse_usage

        client = MagicMock()
        mock_get_client.return_value = client

        report_langfuse_usage(
            {
                "deployment": "chat-gpt41",
                "model": "gpt-4.1-2025-04-14",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.001534,
            }
        )

        client.update_current_generation.assert_called_once()
        kwargs = client.update_current_generation.call_args.kwargs
        assert kwargs["model"] == "chat-gpt41"
        assert kwargs["usage_details"] == {"input": 100, "output": 50, "total": 150}
        assert kwargs["cost_details"] == {"total": pytest.approx(0.001534)}


class TestLangfuseTracerLogEvent:
    def test_log_event_sets_model_usage_and_cost_on_observation(self) -> None:
        tracer = LangfuseTracer(agent_id="test-agent")
        mock_obs = MagicMock()
        tracer._observation_stack.append(mock_obs)  # noqa: SLF001
        tracer._traces.append({"events": []})  # noqa: SLF001

        tracer.log_event(
            "llm_success",
            {
                "input_tokens": 10,
                "output_tokens": 20,
                "cost_usd": 0.00042,
                "model": "chat-gpt41mini",
                "output": {"text": "hi"},
            },
        )

        mock_obs.update.assert_called_once()
        kwargs = mock_obs.update.call_args.kwargs
        assert kwargs["model"] == "chat-gpt41mini"
        assert kwargs["usage_details"] == {"input": 10, "output": 20, "total": 30}
        assert kwargs["cost_details"] == {"total": 0.00042}
        assert kwargs["output"] == {"text": "hi"}
