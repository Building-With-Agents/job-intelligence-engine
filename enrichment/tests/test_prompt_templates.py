"""Unit tests for the shared candidate-based classifier prompt template."""

from __future__ import annotations

from enrichment.classifiers._prompt_templates import build_code_classifier_prompt

_CANDIDATES = [
    {"code": "541511", "title": "Custom Computer Programming Services"},
    {"code": "541512", "title": "Computer Systems Design Services"},
]


def test_naics_style_prompt_includes_intro_line() -> None:
    """response_field branch (NAICS-style) embeds intro_line at the top."""
    intro = "Classify this job into one NAICS 2022 industry from the list below."
    prompt = build_code_classifier_prompt(
        "Software Engineer",
        "We build custom software.",
        _CANDIDATES,
        intro_line=intro,
        candidates_heading="NAICS candidates (choose exactly one code from this list, or unknown):",
        unknown_value="unknown",
        response_field="naics_code",
    )
    assert prompt.startswith(intro)
    assert "541511: Custom Computer Programming Services" in prompt
    assert "naics_code" in prompt


def test_soc_style_prompt_includes_intro_line() -> None:
    """no-response_field branch (SOC-style) must also embed intro_line — was silently dropped before."""
    intro = "Classify this job into one SOC occupation from the candidate list below."
    prompt = build_code_classifier_prompt(
        "Software Engineer",
        "We build custom software.",
        [{"code": "15-1252", "title": "Software Developers"}],
        intro_line=intro,
        candidates_heading="SOC Candidates:",
        unknown_value="unclassified",
    )
    assert prompt.startswith(intro)
    assert "SOC Candidates:" in prompt
    assert "15-1252: Software Developers" in prompt
    assert "unclassified" in prompt


def test_soc_style_prompt_distinct_intro_lines_render_distinctly() -> None:
    """Changing intro_line on the SOC branch must change the rendered prompt."""
    common = dict(
        title="Software Engineer",
        description="desc",
        candidates=[{"code": "15-1252", "title": "Software Developers"}],
        candidates_heading="SOC Candidates:",
        unknown_value="unclassified",
    )
    a = build_code_classifier_prompt(intro_line="Intro A.", **common)
    b = build_code_classifier_prompt(intro_line="Intro B.", **common)
    assert a != b
    assert a.startswith("Intro A.")
    assert b.startswith("Intro B.")
