"""Shared prompt templates for candidate-based enrichment classifiers."""

from __future__ import annotations


def build_code_classifier_prompt(
    title: str,
    description: str | None,
    candidates: list[dict[str, str]],
    *,
    intro_line: str,
    candidates_heading: str,
    unknown_value: str,
    response_field: str | None = None,
) -> str:
    """Build a candidate-constrained prompt for SOC/NAICS-style code selection."""
    lines = [f"{c['code']}: {c['title']}" for c in candidates]
    candidates_block = "\n".join(lines)
    desc = (description or "").strip()

    if response_field:
        return f"""{intro_line}

Job title: {title}
Job description: {desc}

{candidates_heading}
{candidates_block}

Rules:
- Respond with structured data only.
- Field {response_field} must be exactly one of the codes shown above, or the literal {unknown_value}.
- Use {unknown_value} if the job text does not clearly fit any single listed option."""

    return f"""Job Title: {title}
Job Description: {desc}

{candidates_heading}
{candidates_block}

Instructions:
- ONLY choose from the list above. DO NOT invent codes.
- If none match, return '{unknown_value}'.
- Reply with exactly one token: the chosen code exactly as shown, or {unknown_value}."""
