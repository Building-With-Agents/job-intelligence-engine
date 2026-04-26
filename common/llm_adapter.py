"""Centralized LLM adapter for all agents.

Every LLM call in the pipeline flows through here. Handles:
- Making the actual API call
- Timing and token tracking
- Cost computation
- Logging every call to llm_audit_log via log_extraction_event()
- Retry once on timeout, then return extraction_failed=True
- Exponential back-off on 429 (1s → 2s → 4s → 8s); queue is implicit (caller holds batch)
- SkillsExtractionAlert event emitted after 3 back-off cycles (if bus registered)
- Optional Langfuse tracing via register_tracer()

All LLM calls from any agent must flow through this adapter — no per-agent logging.
Uses structlog only. No credentials in code — env vars only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json as _json
import os
import time
import uuid
from contextlib import nullcontext
from typing import Any

import structlog

from common.data_store.database import session_scope
from common.data_store.models import LLMAuditLog
from common.observability.langfuse import LangfuseTracer

log = structlog.get_logger()


def _langfuse_extra_metadata() -> dict[str, Any]:
    """Merge request-scoped structlog context (e.g. ``X-Request-Id``) into Langfuse spans (JIE #222)."""
    extra: dict[str, Any] = {}
    try:
        import structlog.contextvars as scv

        ctx = scv.get_contextvars()
        rid = (ctx.get("request_id") or "").strip()
        if rid:
            extra["request_id"] = rid
        tid = (ctx.get("tenant_id") or "").strip()
        if tid:
            extra["tenant_id"] = tid
        uid = (ctx.get("user_email") or "").strip()
        if uid:
            extra["user_email"] = uid
        kid = (ctx.get("key_id") or "").strip()
        if kid:
            extra["key_id"] = kid
    except Exception:
        return {}
    return extra


def _span_metadata(base: dict[str, Any]) -> dict[str, Any]:
    return {**base, **_langfuse_extra_metadata()}


def _parse_output_for_trace(text: str, max_chars: int = 4000) -> str | dict | list:
    """Parse JSON output so Langfuse renders it as a collapsible tree."""
    truncated = text[:max_chars]
    try:
        return _json.loads(truncated)
    except (ValueError, TypeError):
        return truncated


# Optional bus for emitting SkillsExtractionAlert; set via register_alert_bus()
_alert_bus: Any = None

# Optional Langfuse tracer; set via register_tracer() so every LLM call can be traced
_tracer: LangfuseTracer | None = None


def get_tracer() -> LangfuseTracer | None:
    """Return the currently registered tracer (None if not set)."""
    return _tracer


# ---------------------------------------------------------------------------
# Pricing (per token) — all providers/models, configurable via env vars
#
# Keys map 1:1 to MODEL_TIER_MAP values. Add new models here and add
# their deployment/API names to MODEL_TIER_MAP below.
# ---------------------------------------------------------------------------


def _per_token_from_yaml(yaml_key: str, env_var: str) -> float:
    """Resolve a per-token cost.

    YAML stores per-million-tokens (vendor-quote convention) under
    ``config/llm_costs.yaml``; this helper divides by 1,000,000. Legacy
    ``*_COST_PER_TOKEN`` env vars override per-token directly (their
    historical unit) during the deprecation window (issue #210).
    Defaults live in YAML — there are none in this file.
    """
    from common.config_loader import get_float

    raw_env = os.getenv(env_var) if env_var else None
    if raw_env is not None and raw_env.strip():
        try:
            return float(raw_env)
        except (TypeError, ValueError):
            pass  # fall through to YAML

    per_million = get_float(
        file="llm_costs",
        key=yaml_key,
        env=None,
        minimum=0.0,
    )
    return per_million / 1_000_000


def _build_pricing() -> dict[str, dict[str, float]]:
    """Resolve PRICING table from ``config/llm_costs.yaml`` plus legacy env overrides.

    Defaults live in ``config/llm_costs.yaml``; this function intentionally
    has no numeric values.
    """
    return {
        # Anthropic
        "sonnet": {
            "input": _per_token_from_yaml(
                "llm_costs.sonnet.input_per_million_tokens",
                "SONNET_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.sonnet.output_per_million_tokens",
                "SONNET_OUTPUT_COST_PER_TOKEN",
            ),
        },
        "haiku": {
            "input": _per_token_from_yaml(
                "llm_costs.haiku.input_per_million_tokens",
                "HAIKU_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.haiku.output_per_million_tokens",
                "HAIKU_OUTPUT_COST_PER_TOKEN",
            ),
        },
        # Azure OpenAI / OpenAI
        "gpt-4.1-mini": {
            "input": _per_token_from_yaml(
                "llm_costs.gpt41mini.input_per_million_tokens",
                "GPT41MINI_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gpt41mini.output_per_million_tokens",
                "GPT41MINI_OUTPUT_COST_PER_TOKEN",
            ),
        },
        "gpt-4.1": {
            "input": _per_token_from_yaml(
                "llm_costs.gpt41.input_per_million_tokens",
                "GPT41_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gpt41.output_per_million_tokens",
                "GPT41_OUTPUT_COST_PER_TOKEN",
            ),
        },
        "gpt-4o": {
            "input": _per_token_from_yaml(
                "llm_costs.gpt4o.input_per_million_tokens",
                "GPT4O_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gpt4o.output_per_million_tokens",
                "GPT4O_OUTPUT_COST_PER_TOKEN",
            ),
        },
        "gpt-4o-mini": {
            "input": _per_token_from_yaml(
                "llm_costs.gpt4omini.input_per_million_tokens",
                "GPT4OMINI_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gpt4omini.output_per_million_tokens",
                "GPT4OMINI_OUTPUT_COST_PER_TOKEN",
            ),
        },
        # Gemini
        "gemini-2.5-flash": {
            "input": _per_token_from_yaml(
                "llm_costs.gemini_flash.input_per_million_tokens",
                "GEMINI_FLASH_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gemini_flash.output_per_million_tokens",
                "GEMINI_FLASH_OUTPUT_COST_PER_TOKEN",
            ),
        },
        "gemini-2.5-pro": {
            "input": _per_token_from_yaml(
                "llm_costs.gemini_pro.input_per_million_tokens",
                "GEMINI_PRO_INPUT_COST_PER_TOKEN",
            ),
            "output": _per_token_from_yaml(
                "llm_costs.gemini_pro.output_per_million_tokens",
                "GEMINI_PRO_OUTPUT_COST_PER_TOKEN",
            ),
        },
    }


PRICING: dict[str, dict[str, float]] = _build_pricing()

# Model/deployment name → PRICING key.
# Covers: Anthropic model IDs, Azure API model names, Azure deployment names,
# Gemini model names. Add new entries here when adding deployments.
MODEL_TIER_MAP: dict[str, str] = {
    # Anthropic
    "claude-sonnet-4-5": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
    # Azure OpenAI — model names returned by the API
    "gpt-4.1-mini-2025-04-14": "gpt-4.1-mini",
    "gpt-4.1-2025-04-14": "gpt-4.1",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    # Azure OpenAI — common deployment names (AZURE_OPENAI_DEPLOYMENT_NAME)
    "chat-gpt41mini": "gpt-4.1-mini",
    "chat-gpt41": "gpt-4.1",
    "chat-gpt4o": "gpt-4o",
    "chat-gpt4o-mini": "gpt-4o-mini",
    # Gemini
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
}


def resolve_llm_route(role: str | None = None) -> tuple[str, str]:
    """Resolve (provider, model_or_deployment) for a pipeline role.

    Resolution (legacy env wins during deprecation, then YAML, then fail fast):
    1. LLM_{ROLE} env var (e.g. LLM_SYNTHESIS for role="synthesis")
       - If value contains ':', split as provider:model (e.g. "gemini:gemini-2.5-pro")
       - Otherwise, use LLM_PROVIDER (env or YAML) as the provider
    2. ``llm.routes.<role>`` from ``config/llm.yaml`` (same colon-split logic)
    3. LLM_DEFAULT env var (same colon-split logic)
    4. ``llm.routes.default`` from ``config/llm.yaml``
    5. Raise ValueError with clear message

    Roles: synthesis, extraction, extraction_tasks, extraction_responsibilities,
           extraction_naics, extraction_employer, classification, analytics
    """
    from common.config_loader import _resolve

    default_provider = os.getenv("LLM_PROVIDER")
    if not default_provider:
        yaml_provider = _resolve("llm", "llm.provider")
        default_provider = str(yaml_provider) if yaml_provider else "azure_openai"

    def _parse(value: str) -> tuple[str, str]:
        if ":" in value:
            provider, model = value.split(":", 1)
            return provider.strip(), model.strip()
        return default_provider, value.strip()

    # 1. Role-specific: LLM_{ROLE} env, then YAML
    if role:
        role_var = f"LLM_{role.upper()}"
        val = os.getenv(role_var)
        if val:
            return _parse(val)
        yaml_route = _resolve("llm", f"llm.routes.{role}")
        if yaml_route:
            return _parse(str(yaml_route))

    # 2. Global default: LLM_DEFAULT env, then YAML
    default = os.getenv("LLM_DEFAULT")
    if default:
        return _parse(default)
    yaml_default = _resolve("llm", "llm.routes.default")
    if yaml_default:
        return _parse(str(yaml_default))

    # 3. Fail fast
    role_hint = f"LLM_{role.upper()}" if role else "LLM_DEFAULT"
    raise ValueError(
        f"No LLM deployment configured. Set {role_hint} or LLM_DEFAULT in your .env "
        f"(or fill in config/llm.yaml -> llm.routes). Copy the LLM section from .env.example."
    )


# Back-off settings
_BACKOFF_SEQUENCE = [1, 2, 4, 8]
_ALERT_AFTER_CYCLES = 3


# ---------------------------------------------------------------------------
# Cost computation
# ---------------------------------------------------------------------------


def resolve_model_tier(model: str) -> str:
    """Resolve a model or deployment name to a PRICING key.

    Resolution order:
    1. Exact match in MODEL_TIER_MAP
    2. Substring match (e.g. "gpt-4.1-mini-2025-04-14" contains "gpt-4.1-mini")
    3. LLM_DEFAULT env var → MODEL_TIER_MAP lookup
    4. LLM_PROVIDER env var default (azure_openai → gpt-4.1-mini, gemini → gemini-2.5-flash)
    5. "sonnet" fallback with a warning log
    """
    # 1. Exact
    tier = MODEL_TIER_MAP.get(model)
    if tier:
        return tier
    # 2. Substring — handles versioned names like "gpt-4.1-mini-2025-04-14"
    # Check longer keys first to avoid "chat-gpt41mini" matching inside "gemini-2.5-flash (chat-gpt41mini)"
    for known, mapped in sorted(MODEL_TIER_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if known in model:
            return mapped
    # 3. Deployment name from env
    deployment = os.getenv("LLM_DEFAULT", "")
    if deployment:
        tier = MODEL_TIER_MAP.get(deployment)
        if tier:
            return tier
    # 4. Provider default
    provider = os.getenv("LLM_PROVIDER", "azure_openai")
    if provider == "azure_openai":
        return "gpt-4.1-mini"
    if provider == "gemini":
        from common.config_loader import get_str as _get_str

        gemini_model = _get_str(file="llm", key="llm.gemini_model", env="GEMINI_MODEL")
        return MODEL_TIER_MAP.get(gemini_model, "gemini-2.5-flash")
    if provider == "anthropic":
        return "sonnet"
    # 5. Unknown — caller gets $0.0 cost and a warning log; never silently wrong
    log.warning("unresolved_model_tier", model=model, provider=provider)
    return "unknown"


def compute_extraction_cost(input_tokens: int, output_tokens: int, model_tier: str) -> float:
    """Return cost in USD for a given token count and model tier.

    ``model_tier`` can be either a PRICING key (e.g. ``"gpt-4.1-mini"``) or a
    raw model/deployment name — :func:`resolve_model_tier` is applied automatically
    when the value is not already a direct PRICING key.
    """
    tier_key = model_tier if model_tier in PRICING else resolve_model_tier(model_tier)
    tier = PRICING.get(tier_key)
    if not tier:
        log.warning("unknown_model_tier", model_tier=model_tier, resolved=tier_key)
        return 0.0
    return (input_tokens * tier["input"]) + (output_tokens * tier["output"])


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def log_extraction_event(
    agent_name: str,
    prompt: str,
    model: str,
    provider: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    success: bool,
    error_reason: str | None = None,
) -> None:
    """Centralized logging: write one row to llm_audit_log after every LLM call.

    Never raises — logging must not break the pipeline. All agents use this
    via the adapter; no per-agent logging. Each call opens its own short-lived
    SQLAlchemy session via ``session_scope()``, so async extraction tasks do not
    share Session objects even when many LLM calls finish close together.
    """
    try:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        with session_scope() as session:
            session.add(
                LLMAuditLog(
                    agent_name=agent_name,
                    prompt_hash=prompt_hash,
                    model=model,
                    provider=provider,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    token_count=input_tokens + output_tokens,
                    cost_usd=cost_usd,
                    success=success,
                    error_reason=error_reason,
                )
            )
    except Exception as exc:
        log.warning("llm_audit_log_write_failed", error=str(exc))


def handle_extraction_failure(
    agent_name: str,
    model_tier: str,
    error_reason: str,
    latency_ms: int = 0,
) -> dict[str, Any]:
    """Return standard failure result after timeout retry or other error.

    Retry-on-timeout is applied in complete() (once); then this result is returned
    with extraction_failed=True.
    """
    return {
        "content": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "model_tier": model_tier,
        "success": False,
        "extraction_failed": True,
        "latency_ms": latency_ms,
        "error_reason": error_reason,
    }


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


def complete(
    prompt: str,
    agent_name: str,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 1000,
    correlation_id: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Make an LLM call, log it via log_extraction_event(), and return the result.

    Retries once on timeout; on second timeout or other API error returns
    via handle_extraction_failure() with extraction_failed=True.

    If a tracer is registered via register_tracer(), the call is wrapped in a
    Langfuse span; correlation_id is passed through when provided, else a new uuid.

    Returns a dict with:
        - content: str
        - input_tokens: int
        - output_tokens: int
        - cost_usd: float
        - model_tier: str
        - success: bool
        - extraction_failed: bool (True after timeout retry or API error)
    """
    if role and not model:
        resolved_provider, resolved_model = resolve_llm_route(role)
        provider = resolved_provider
        model = resolved_model
    else:
        provider = os.getenv("LLM_PROVIDER", "anthropic")

    # Mock provider: return ground truth data, no API calls
    if provider == "mock":
        from common.mock_llm_provider import mock_complete

        correlation_id = correlation_id or str(uuid.uuid4())
        span_ctx = (
            _tracer.start_span(
                agent_name,
                correlation_id=correlation_id,
                input=prompt,
                metadata=_span_metadata({"agent_name": agent_name, "model": "mock-sonnet-v1"}),
            )
            if _tracer
            else nullcontext()
        )
        with span_ctx:
            result = mock_complete(prompt, agent_name, model=model, system=system, max_tokens=max_tokens)
            log_extraction_event(
                agent_name=agent_name,
                prompt=prompt,
                model="mock-sonnet-v1",
                provider="mock",
                latency_ms=result.get("latency_ms", 0),
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                cost_usd=result["cost_usd"],
                success=True,
            )
            if _tracer:
                with contextlib.suppress(Exception):
                    _tracer.log_event(
                        "llm_success",
                        {
                            "input_tokens": result["input_tokens"],
                            "output_tokens": result["output_tokens"],
                            "cost_usd": result["cost_usd"],
                            "output": _parse_output_for_trace(result["content"]),
                        },
                    )
            return result

    # Gemini provider: use google-generativeai SDK
    if provider == "gemini":
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "The 'google-generativeai' package is required when LLM_PROVIDER=gemini. "
                "Install with: pip install google-generativeai"
            ) from exc

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        from common.config_loader import get_str as _get_str

        gemini_model = model or _get_str(
            file="llm",
            key="llm.gemini_model",
            env="GEMINI_MODEL",
        )
        gmodel = genai.GenerativeModel(gemini_model, system_instruction=system or None)

        correlation_id = correlation_id or str(uuid.uuid4())
        span_ctx = (
            _tracer.start_span(
                agent_name,
                correlation_id=correlation_id,
                input=prompt,
                metadata=_span_metadata({"agent_name": agent_name, "model": gemini_model}),
            )
            if _tracer
            else nullcontext()
        )
        with span_ctx:
            start = time.monotonic()
            try:
                response = gmodel.generate_content(prompt)
                latency_ms = int((time.monotonic() - start) * 1000)
                content = response.text
                usage = response.usage_metadata
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                cost_usd = compute_extraction_cost(input_tokens, output_tokens, gemini_model)

                log_extraction_event(
                    agent_name=agent_name,
                    prompt=prompt,
                    model=gemini_model,
                    provider="gemini",
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    success=True,
                )
                if _tracer:
                    with contextlib.suppress(Exception):
                        _tracer.log_event(
                            "llm_success",
                            {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cost_usd": cost_usd,
                                "output": _parse_output_for_trace(content),
                            },
                        )
                return {
                    "content": content,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "latency_ms": latency_ms,
                    "model": gemini_model,
                    "model_tier": "gemini-flash",
                    "success": True,
                    "extraction_failed": False,
                }
            except Exception as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                log_extraction_event(
                    agent_name=agent_name,
                    prompt=prompt,
                    model=gemini_model,
                    provider="gemini",
                    latency_ms=latency_ms,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    success=False,
                    error_reason=str(exc),
                )
                return {
                    "content": "",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                    "latency_ms": latency_ms,
                    "model": gemini_model,
                    "model_tier": "gemini-flash",
                    "success": False,
                    "extraction_failed": True,
                }

    # Azure OpenAI provider: use langchain-openai (consistent with llm_client.py)
    if provider == "azure_openai":
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError as exc:
            raise ImportError(
                "The 'langchain-openai' package is required when LLM_PROVIDER=azure_openai. "
                "Install with: pip install langchain-openai"
            ) from exc

        deployment = model or os.getenv("LLM_DEFAULT", "chat-gpt41mini")
        model_tier = resolve_model_tier(deployment)
        azure_llm = AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
            azure_deployment=deployment,
            temperature=0.1,
            max_tokens=max_tokens,
        )

        correlation_id = correlation_id or str(uuid.uuid4())
        span_metadata = _span_metadata({"agent_name": agent_name, "model": deployment, "model_tier": model_tier})
        span_ctx = (
            _tracer.start_span(agent_name, correlation_id=correlation_id, input=prompt, metadata=span_metadata)
            if _tracer
            else nullcontext()
        )

        lc_messages: list[Any] = []
        if system:
            from langchain_core.messages import HumanMessage, SystemMessage

            lc_messages.append(SystemMessage(content=system))
            lc_messages.append(HumanMessage(content=prompt))
        else:
            from langchain_core.messages import HumanMessage

            lc_messages.append(HumanMessage(content=prompt))

        backoff_cycles = 0

        with span_ctx:
            while True:
                for attempt in range(2):
                    start = time.monotonic()
                    try:
                        msg = azure_llm.invoke(lc_messages)
                        latency_ms = int((time.monotonic() - start) * 1000)
                        content = msg.content if hasattr(msg, "content") else str(msg)

                        # Extract actual model name from response metadata
                        response_model = deployment
                        input_tokens = 0
                        output_tokens = 0
                        if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
                            actual_model = msg.response_metadata.get("model_name") or msg.response_metadata.get("model")
                            if actual_model:
                                response_model = actual_model
                            usage = msg.response_metadata.get("token_usage") or msg.response_metadata.get("usage")
                            if isinstance(usage, dict):
                                input_tokens = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0))
                                output_tokens = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0))

                        if not input_tokens:
                            input_tokens = len(prompt) // 4
                            output_tokens = len(content) // 4

                        cost_usd = compute_extraction_cost(input_tokens, output_tokens, model_tier)

                        log_extraction_event(
                            agent_name=agent_name,
                            prompt=prompt,
                            model=response_model,
                            provider="azure_openai",
                            latency_ms=latency_ms,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost_usd,
                            success=True,
                        )

                        log.info(
                            "llm_call_success",
                            agent=agent_name,
                            model=response_model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=round(cost_usd, 6),
                            latency_ms=latency_ms,
                        )

                        if _tracer:
                            with contextlib.suppress(Exception):
                                _tracer.record_latency("llm_call", seconds=latency_ms / 1000.0)
                                _tracer.log_event(
                                    "llm_success",
                                    {
                                        "input_tokens": input_tokens,
                                        "output_tokens": output_tokens,
                                        "cost_usd": round(cost_usd, 6),
                                        "output": _parse_output_for_trace(content),
                                    },
                                )

                        return {
                            "content": content,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cost_usd": cost_usd,
                            "model": response_model,
                            "model_tier": model_tier,
                            "success": True,
                            "extraction_failed": False,
                        }

                    except Exception as exc:
                        latency_ms = int((time.monotonic() - start) * 1000)
                        error_str = str(exc)
                        is_timeout = "timeout" in error_str.lower()
                        is_rate_limit = "429" in error_str or "rate limit" in error_str.lower()

                        if is_timeout and attempt == 0:
                            log.warning("llm_timeout", agent=agent_name, attempt=attempt + 1)
                            continue

                        if is_rate_limit:
                            break  # outer while loop handles backoff

                        log_extraction_event(
                            agent_name=agent_name,
                            prompt=prompt,
                            model=deployment,
                            provider="azure_openai",
                            latency_ms=latency_ms,
                            input_tokens=0,
                            output_tokens=0,
                            cost_usd=0.0,
                            success=False,
                            error_reason=error_str,
                        )
                        if _tracer:
                            with contextlib.suppress(Exception):
                                _tracer.record_error(exc, context={"agent_name": agent_name, "model": deployment})
                        return handle_extraction_failure(
                            agent_name=agent_name,
                            model_tier=model_tier,
                            error_reason=error_str,
                            latency_ms=latency_ms,
                        )
                else:
                    continue

                # Rate limit back-off (reached via 429 break)
                backoff_cycles += 1
                wait = _BACKOFF_SEQUENCE[min(backoff_cycles - 1, len(_BACKOFF_SEQUENCE) - 1)]
                log.warning("llm_rate_limit_backoff", agent=agent_name, cycle=backoff_cycles, wait_s=wait)
                if backoff_cycles >= _ALERT_AFTER_CYCLES:
                    _emit_skills_extraction_alert(agent_name, backoff_cycles)
                time.sleep(wait)

    # Anthropic provider (fallback for direct Anthropic API usage)
    try:
        from anthropic import Anthropic, APIStatusError, APITimeoutError
    except ImportError as exc:
        raise ImportError(
            "The 'anthropic' package is required when LLM_PROVIDER=anthropic. Install with: pip install anthropic"
        ) from exc

    model = model or os.getenv("LLM_DEFAULT", "claude-sonnet-4-5")
    model_tier = MODEL_TIER_MAP.get(model, "sonnet")
    client = Anthropic()

    correlation_id = correlation_id or str(uuid.uuid4())
    span_metadata = _span_metadata({"agent_name": agent_name, "model": model, "model_tier": model_tier})
    span_ctx = (
        _tracer.start_span(agent_name, correlation_id=correlation_id, input=prompt, metadata=span_metadata)
        if _tracer
        else nullcontext()
    )

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    backoff_cycles = 0

    with span_ctx:
        # --- Retry loop for rate limits ---
        while True:
            # --- Single attempt with one timeout retry ---
            for attempt in range(2):
                start = time.monotonic()
                try:
                    response = client.messages.create(**kwargs)
                    latency_ms = int((time.monotonic() - start) * 1000)

                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens
                    cost_usd = compute_extraction_cost(input_tokens, output_tokens, model_tier)
                    content = response.content[0].text

                    log_extraction_event(
                        agent_name=agent_name,
                        prompt=prompt,
                        model=model,
                        provider=provider,
                        latency_ms=latency_ms,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                        success=True,
                    )

                    log.info(
                        "llm_call_success",
                        agent=agent_name,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=round(cost_usd, 6),
                        latency_ms=latency_ms,
                    )

                    if _tracer:
                        with contextlib.suppress(Exception):
                            _tracer.record_latency("llm_call", seconds=latency_ms / 1000.0)
                            _tracer.log_event(
                                "llm_success",
                                {
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "cost_usd": round(cost_usd, 6),
                                    "output": _parse_output_for_trace(content),
                                },
                            )

                    return {
                        "content": content,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": cost_usd,
                        "model": getattr(response, "model", model),
                        "model_tier": model_tier,
                        "success": True,
                        "extraction_failed": False,
                    }

                except APITimeoutError as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    log.warning("llm_timeout", agent=agent_name, attempt=attempt + 1)
                    if attempt == 0:
                        continue  # retry once
                    # Both attempts timed out — log and return extraction_failed
                    log_extraction_event(
                        agent_name=agent_name,
                        prompt=prompt,
                        model=model,
                        provider=provider,
                        latency_ms=latency_ms,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        success=False,
                        error_reason=f"timeout: {exc}",
                    )
                    if _tracer:
                        with contextlib.suppress(Exception):
                            _tracer.record_error(exc, context={"agent_name": agent_name, "model": model})
                    return handle_extraction_failure(
                        agent_name=agent_name,
                        model_tier=model_tier,
                        error_reason=f"timeout: {exc}",
                        latency_ms=latency_ms,
                    )

                except APIStatusError as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    if exc.status_code == 429:
                        # Rate limit — break out of attempt loop, handle below
                        break
                    # Any other API error — log and return extraction_failed
                    log_extraction_event(
                        agent_name=agent_name,
                        prompt=prompt,
                        model=model,
                        provider=provider,
                        latency_ms=latency_ms,
                        input_tokens=0,
                        output_tokens=0,
                        cost_usd=0.0,
                        success=False,
                        error_reason=f"{exc.status_code}: {exc.message}",
                    )
                    if _tracer:
                        with contextlib.suppress(Exception):
                            _tracer.record_error(exc, context={"agent_name": agent_name, "model": model})
                    return handle_extraction_failure(
                        agent_name=agent_name,
                        model_tier=model_tier,
                        error_reason=f"{exc.status_code}: {exc.message}",
                        latency_ms=latency_ms,
                    )
                else:
                    break  # success — exit attempt loop

            else:
                # Only reached if attempt loop ended without a 429 break
                continue

            # --- Rate limit back-off ---
            backoff_cycles += 1
            wait = _BACKOFF_SEQUENCE[min(backoff_cycles - 1, len(_BACKOFF_SEQUENCE) - 1)]
            log.warning("llm_rate_limit_backoff", agent=agent_name, cycle=backoff_cycles, wait_s=wait)

            if backoff_cycles >= _ALERT_AFTER_CYCLES:
                _emit_skills_extraction_alert(agent_name, backoff_cycles)

            time.sleep(wait)


# ---------------------------------------------------------------------------
# Alert emission
# ---------------------------------------------------------------------------


def register_alert_bus(bus: Any) -> None:
    """Register the event bus so SkillsExtractionAlert can be published.

    Call once from the pipeline runner or orchestration layer. If not set,
    only structlog is used when the back-off threshold is exceeded.
    """
    global _alert_bus
    _alert_bus = bus


def register_tracer(tracer: LangfuseTracer | None) -> None:
    """Register a Langfuse tracer so every LLM call is automatically traced.

    Call once from the pipeline or orchestration layer. If not set, no
    tracing is performed; tracing is optional and must never break the pipeline.
    """
    global _tracer
    _tracer = tracer


def _emit_skills_extraction_alert(agent_name: str, backoff_cycles: int) -> None:
    """Emit SkillsExtractionAlert after 3+ rate limit back-off cycles.

    Logs via structlog always; publishes EventEnvelope if register_alert_bus()
    was called (Orchestration is the sole consumer).
    """
    log.error(
        "SkillsExtractionAlert",
        agent=agent_name,
        backoff_cycles=backoff_cycles,
        message="Rate limit back-off threshold exceeded. Pipeline may be degraded.",
    )
    if _alert_bus is None:
        return
    try:
        from common.event_envelope import EventEnvelope

        event = EventEnvelope(
            correlation_id="",
            agent_id="skills-extraction-agent",
            payload={
                "event_type": "SkillsExtractionAlert",
                "agent_name": agent_name,
                "backoff_cycles": backoff_cycles,
                "message": "Rate limit back-off threshold exceeded. Pipeline may be degraded.",
            },
        )
        _alert_bus.publish(event)
    except Exception as exc:
        log.warning("SkillsExtractionAlert_publish_failed", error=str(exc))
