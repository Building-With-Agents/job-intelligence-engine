"""Azure OpenAI text embeddings with audit logging — shared across agents."""

from __future__ import annotations

import os
import random
import re
import time
from typing import Any

import httpx
import structlog

from common.config_loader import cached_accessor, get_float

log = structlog.get_logger()

_EMBED_MAX_RETRIES = 5
_EMBED_BASE_DELAY = 1.0  # seconds
_EMBEDDING_AUDIT_PROMPT_MAX_CHARS = 8000
_EMBEDDING_AUDIT_ERROR_MAX_CHARS = 1000


@cached_accessor
def _embedding_input_usd_per_1k_tokens() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.embedding.input_usd_per_1k_tokens",
        env="EMBEDDING_INPUT_USD_PER_1K_TOKENS",
        minimum=0.0,
    )


def _embedding_audit_prompt(texts: list[str]) -> str:
    raw = "\n".join(texts)
    if len(raw) <= _EMBEDDING_AUDIT_PROMPT_MAX_CHARS:
        return raw
    return raw[:_EMBEDDING_AUDIT_PROMPT_MAX_CHARS] + "\n...[truncated for audit hash]"


def _embedding_usage_input_tokens(data: Any) -> int:
    """Parse Azure/OpenAI embeddings response usage; owners may extend for other shapes (#108)."""
    if not isinstance(data, dict):
        log.debug("embedding_usage_missing", reason="response_not_dict")
        return 0
    usage = data.get("usage")
    if not isinstance(usage, dict):
        log.debug("embedding_usage_missing", reason="no_usage_dict")
        return 0
    pt = usage.get("prompt_tokens")
    if isinstance(pt, int) and pt >= 0:
        return pt
    tt = usage.get("total_tokens")
    if isinstance(tt, int) and tt >= 0:
        return tt
    log.debug("embedding_usage_missing", reason="no_prompt_or_total_tokens")
    return 0


def _embedding_cost_usd(input_tokens: int) -> float:
    """Rough $/1K input tokens; override via skills_extraction.embedding config (#108)."""
    return (input_tokens / 1000.0) * _embedding_input_usd_per_1k_tokens()


def _embedding_error_reason(prefix: str, detail: str | None = None) -> str:
    """Normalize and cap embedding audit error text for llm_audit_log."""
    message = prefix.strip()
    extra = (detail or "").strip()
    if extra:
        message = f"{message}: {extra}" if message else extra
    if len(message) <= _EMBEDDING_AUDIT_ERROR_MAX_CHARS:
        return message
    return message[: _EMBEDDING_AUDIT_ERROR_MAX_CHARS - 3] + "..."


def _response_json_or_none(resp: httpx.Response) -> Any | None:
    """Best-effort JSON parse for audit token extraction on success/failure."""
    try:
        return resp.json()
    except Exception:
        return None


def _log_embedding_audit_event(
    texts: list[str],
    data: Any,
    latency_ms: int,
    *,
    agent_name: str = "taxonomy-resolver",
    success: bool = True,
    error_reason: str | None = None,
) -> None:
    """Write one dbo.llm_audit_log row per embedding HTTP attempt (issue #108)."""
    from common.llm_adapter import log_extraction_event

    input_tokens = _embedding_usage_input_tokens(data)
    log_extraction_event(
        agent_name=agent_name,
        prompt=_embedding_audit_prompt(texts),
        model="text-embedding-3-small",
        provider="azure-openai",
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=0,
        cost_usd=_embedding_cost_usd(input_tokens),
        success=success,
        error_reason=error_reason,
    )


def _extract_retry_after_embedding(error_message: str) -> int | None:
    """Extract retry-after seconds from Azure embedding 429 error."""
    match = re.search(r"retry after (\d+)\s*seconds?", error_message, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _embedding_retry_delay(retry_after: int | None, attempt: int) -> float:
    """Return retry delay with jitter to avoid lockstep 429 retries across workers."""
    if retry_after:
        return float(retry_after)
    base_delay = _EMBED_BASE_DELAY * (2 ** (attempt - 1))
    return base_delay * random.uniform(1.0, 1.3)


def embed_texts_azure(
    texts: list[str],
    *,
    audit_agent_name: str = "taxonomy-resolver",
) -> list[list[float]] | None:
    """Call Azure OpenAI Embeddings API with retry on 429. Returns None if env or request fails.

    Retries up to 5 times with exponential backoff + jitter on 429 rate limits.
    Honors Retry-After from error message when available.

    audit_agent_name
        Written to ``llm_audit_log`` via ``log_extraction_event`` for every HTTP
        attempt (success and failure). Use ``enrichment-dedup`` for job posting
        fuzzy dedup so shared cost tracking stays comparable.

    Env: AZURE_OPENAI_EMBEDDING_ENDPOINT, AZURE_OPENAI_EMBEDDING_API_KEY,
         AZURE_OPENAI_EMBEDDING_API_VERSION, AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME.
    """
    endpoint = (os.getenv("AZURE_OPENAI_EMBEDDING_ENDPOINT") or "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_EMBEDDING_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_EMBEDDING_API_VERSION", "2024-02-01")
    deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
    if not endpoint or not api_key or not deployment:
        log.debug(
            "embedding_skip",
            reason="missing_env",
            has_endpoint=bool(endpoint),
            has_key=bool(api_key),
            has_deployment=bool(deployment),
        )
        return None
    if not texts:
        return []
    url = f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version={api_version}"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload: dict[str, Any] = {"input": texts if len(texts) > 1 else texts[0]}

    latency_ms = 0
    data: Any = None

    for attempt in range(1, _EMBED_MAX_RETRIES + 1):
        attempt_started = time.perf_counter()
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                latency_ms = int((time.perf_counter() - attempt_started) * 1000)
                response_data = _response_json_or_none(resp)
                if resp.status_code == 429:
                    retry_after = _extract_retry_after_embedding(resp.text)
                    delay = _embedding_retry_delay(retry_after, attempt)
                    _log_embedding_audit_event(
                        texts,
                        response_data,
                        latency_ms,
                        agent_name=audit_agent_name,
                        success=False,
                        error_reason=_embedding_error_reason("429_rate_limited", resp.text),
                    )
                    log.warning(
                        "embedding_rate_limited",
                        attempt=attempt,
                        delay_s=round(delay, 2),
                        retry_after=retry_after,
                    )
                    if attempt == _EMBED_MAX_RETRIES:
                        log.error("embedding_rate_limit_exhausted", attempts=_EMBED_MAX_RETRIES)
                        return None
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                if response_data is None:
                    raise ValueError("embedding_response_json_invalid")
                data = response_data
                break
        except httpx.HTTPStatusError as exc:
            latency_ms = int((time.perf_counter() - attempt_started) * 1000)
            response = exc.response
            response_data = _response_json_or_none(response) if response is not None else None
            status_code = response.status_code if response is not None else "http_error"
            detail = response.text if response is not None else str(exc)
            _log_embedding_audit_event(
                texts,
                response_data,
                latency_ms,
                agent_name=audit_agent_name,
                success=False,
                error_reason=_embedding_error_reason(f"http_{status_code}", detail),
            )
            if response is not None and response.status_code == 429:
                retry_after = _extract_retry_after_embedding(str(exc))
                delay = _embedding_retry_delay(retry_after, attempt)
                log.warning("embedding_rate_limited", attempt=attempt, delay_s=round(delay, 2))
                if attempt == _EMBED_MAX_RETRIES:
                    return None
                time.sleep(delay)
                continue
            log.warning("embedding_api_failed", error=str(exc), attempt=attempt)
            return None
        except Exception as exc:
            latency_ms = int((time.perf_counter() - attempt_started) * 1000)
            _log_embedding_audit_event(
                texts,
                None,
                latency_ms,
                agent_name=audit_agent_name,
                success=False,
                error_reason=_embedding_error_reason(type(exc).__name__, str(exc)),
            )
            log.warning("embedding_api_failed", error=str(exc), attempt=attempt)
            return None
    else:
        return None

    _log_embedding_audit_event(texts, data, latency_ms, agent_name=audit_agent_name)

    items = data.get("data") if isinstance(data, dict) else None
    if not items or not isinstance(items, list):
        return None
    out: list[list[float]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        emb = item.get("embedding")
        if not isinstance(emb, list) or not all(isinstance(x, (int, float)) for x in emb):
            return None
        out.append([float(x) for x in emb])
    return out


__all__ = ["embed_texts_azure"]
