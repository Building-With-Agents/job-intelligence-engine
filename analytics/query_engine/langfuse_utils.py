"""Shared Langfuse observation helpers for the Q&A query engine (JIE #258).

Centralises the optional-import shim and the token-usage reporting so every
LLM stage (intent, SQL generation, synthesis, follow-ups) stays consistent
without duplicating the same ~20-line block in each module.
"""

from __future__ import annotations

from typing import Any

try:
    from langfuse.decorators import langfuse_context as lf_context
    from langfuse.decorators import observe as lf_observe
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
    to the currently active Langfuse observation.

    Safe to call when Langfuse is not installed — the shim silently discards
    updates.
    """
    input_tokens = llm_result.get("input_tokens") or llm_result.get("prompt_tokens")
    output_tokens = llm_result.get("output_tokens") or llm_result.get("completion_tokens")
    model = llm_result.get("model")
    cost_usd = llm_result.get("cost_usd")
    update_kwargs: dict[str, Any] = {}
    if input_tokens is not None or output_tokens is not None:
        update_kwargs["usage"] = {
            "input": int(input_tokens or 0),
            "output": int(output_tokens or 0),
            "total": int((input_tokens or 0) + (output_tokens or 0)),
        }
    if model:
        update_kwargs["model"] = str(model)
    if cost_usd is not None:
        update_kwargs["cost_details"] = {"total": float(cost_usd)}
    if update_kwargs:
        lf_context.update_current_observation(**update_kwargs)
