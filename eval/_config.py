"""Eval-harness config accessors backed by ``config/eval.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_bool, get_float, get_int, get_str


@cached_accessor
def extraction_fuzzy_threshold() -> int:
    return get_int(
        file="eval",
        key="eval.extraction.fuzzy_threshold",
        env="EVAL_EXTRACTION_FUZZY_THRESHOLD",
        minimum=0,
        maximum=100,
    )


@cached_accessor
def qa_latency_sla_seconds() -> float:
    return get_float(
        file="eval",
        key="eval.qa.latency_sla_seconds",
        env="QA_EVAL_LATENCY_SLA_SECONDS",
        minimum=0.0,
    )


@cached_accessor
def soc_demo_skip_llm() -> bool:
    return get_bool(
        file="eval",
        key="eval.soc_demo.skip_llm",
        env="SOC_DEMO_SKIP_LLM",
    )


@cached_accessor
def exp004_comparison_csv() -> str:
    return get_str(
        file="eval",
        key="eval.exp004.comparison_csv",
        env="EXP004_COMPARISON_CSV",
    )


@cached_accessor
def week8_intent_min_accuracy() -> float:
    return get_float(
        file="eval",
        key="eval.week8.intent_min_accuracy",
        env="WEEK8_INTENT_MIN_ACCURACY",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def week8_intent_max_latency_ms() -> float:
    return get_float(
        file="eval",
        key="eval.week8.intent_max_latency_ms",
        env="WEEK8_INTENT_MAX_LATENCY_MS",
        minimum=0.0,
    )


__all__ = [
    "exp004_comparison_csv",
    "extraction_fuzzy_threshold",
    "qa_latency_sla_seconds",
    "soc_demo_skip_llm",
    "week8_intent_max_latency_ms",
    "week8_intent_min_accuracy",
]
