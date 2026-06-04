"""Shared Langfuse observation helpers for the Q&A query engine (JIE #258, #259).

Centralises the optional-import shim and the token-usage reporting so every
LLM stage (intent, SQL generation, synthesis, follow-ups) stays consistent
without duplicating the same ~20-line block in each module.
"""

from __future__ import annotations

from typing import Any

from common.observability.langfuse_cost import langfuse_model_for_observation

_GENERATION_UPDATE_KEYS = frozenset(
    {
        "name",
        "input",
        "output",
        "metadata",
        "version",
        "level",
        "status_message",
        "completion_start_time",
        "model",
        "model_parameters",
        "usage_details",
        "cost_details",
        "prompt",
    }
)


class _LangfuseContextV4:
    """Langfuse SDK v4: route legacy ``update_current_observation`` to generation updates."""

    @staticmethod
    def update_current_observation(**kwargs: Any) -> None:
        filtered = {k: v for k, v in kwargs.items() if k in _GENERATION_UPDATE_KEYS}
        if not filtered:
            return
        get_client().update_current_generation(**filtered)


try:
    from langfuse import get_client
    from langfuse import observe as lf_observe

    lf_context = _LangfuseContextV4()
except ImportError:

    def lf_observe(**_kwargs: Any):  # type: ignore[misc]
        def _decorator(fn: Any) -> Any:
            return fn

        return _decorator

    class _FakeLangfuseContext:
        @staticmethod
        def update_current_observation(**_kwargs: Any) -> None:
            pass

    lf_context = _FakeLangfuseContext()  # type: ignore[assignment]


def report_langfuse_usage(llm_result: dict[str, Any]) -> None:
    """Push token counts, model name, and cost from a ``complete()`` result
    to the currently active Langfuse generation observation.

    Safe to call when Langfuse is not installed — the shim silently discards
    updates.
    """
    input_tokens = llm_result.get("input_tokens") or llm_result.get("prompt_tokens")
    output_tokens = llm_result.get("output_tokens") or llm_result.get("completion_tokens")
    cost_usd = llm_result.get("cost_usd")
    model = langfuse_model_for_observation(
        llm_result.get("deployment"),
        llm_result.get("model"),
    )
    update_kwargs: dict[str, Any] = {}
    if input_tokens is not None or output_tokens is not None:
        update_kwargs["usage_details"] = {
            "input": int(input_tokens or 0),
            "output": int(output_tokens or 0),
            "total": int((input_tokens or 0) + (output_tokens or 0)),
        }
    if model:
        update_kwargs["model"] = model
    if cost_usd is not None:
        update_kwargs["cost_details"] = {"total": float(cost_usd)}
    if update_kwargs:
        lf_context.update_current_observation(**update_kwargs)
