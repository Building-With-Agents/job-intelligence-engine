"""Langfuse model-name normalization for cost attribution (JIE #259).

Maps Azure deployment names and API response model strings to registry names
registered via ``scripts/setup_langfuse_models.py``.
"""

from __future__ import annotations

# Names created by setup_langfuse_models.py — keep in sync with that script.
REGISTERED_LANGFUSE_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "chat-gpt41mini",
        "chat-gpt41",
        "gpt-4.1-mini-2025-04-14",
        "gpt-4.1-2025-04-14",
    }
)

# PRICING tier key → Langfuse registry deployment name (Azure OpenAI).
_TIER_TO_DEPLOYMENT: dict[str, str] = {
    "gpt-4.1-mini": "chat-gpt41mini",
    "gpt-4.1": "chat-gpt41",
}


def langfuse_model_for_observation(
    deployment: str | None,
    api_model: str | None,
) -> str | None:
    """Return the model string Langfuse should use for pricing lookup.

    Prefer the routed deployment (``chat-gpt41mini`` / ``chat-gpt41``) over the
    API response model name so observations match the model registry.
    """
    dep = (deployment or "").strip()
    if dep:
        return dep

    api = (api_model or "").strip()
    if not api:
        return None
    if api in REGISTERED_LANGFUSE_MODEL_NAMES:
        return api
    if api in _TIER_TO_DEPLOYMENT.values():
        return api

    # Versioned Azure API names (substring, longest match first).
    if "gpt-4.1-mini" in api:
        return "chat-gpt41mini"
    if "gpt-4.1" in api:
        return "chat-gpt41"

    return api
