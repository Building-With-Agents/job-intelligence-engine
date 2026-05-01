"""Unit tests for deterministic quality scoring."""

from __future__ import annotations

from enrichment.classifiers.quality import score_quality


def test_score_quality_empty_is_low_but_bounded() -> None:
    r = score_quality(
        job_title="",
        job_description=None,
        extraction=None,
        extraction_failed=False,
    )
    assert 0.0 <= r.quality_score <= 1.0
    assert r.components["completeness"] == 0.0


def test_score_quality_rich_posting_scores_higher() -> None:
    thin = score_quality(
        job_title="Job",
        job_description="Short.",
        extraction=None,
        extraction_failed=False,
    )
    rich = score_quality(
        job_title="Senior Machine Learning Engineer",
        job_description=(
            "Responsibilities:\n"
            "- Build ML pipelines on AWS\n"
            "- Partner with data science on deep learning models\n"
            "- Requirements: Python, PyTorch, SQL\n\n"
            "We use Kubernetes and TensorFlow in production."
        ),
        extraction={
            "skills": [{"skill_name": "Python"}],
            "tools": [{"tool_name": "Docker"}],
            "tasks": [],
            "responsibilities": [],
            "context": [],
        },
        extraction_failed=False,
    )
    assert rich.quality_score > thin.quality_score
    assert rich.components["structural_coherence"] >= thin.components["structural_coherence"]


def test_extraction_failed_soft_penalty() -> None:
    base = score_quality(
        job_title="Data Engineer",
        job_description="We need someone to build ETL pipelines with SQL and Spark.",
        extraction={"skills": [{"label": "SQL"}], "tools": [], "tasks": [], "responsibilities": [], "context": []},
        extraction_failed=False,
    )
    failed = score_quality(
        job_title="Data Engineer",
        job_description="We need someone to build ETL pipelines with SQL and Spark.",
        extraction={"skills": [{"label": "SQL"}], "tools": [], "tasks": [], "responsibilities": [], "context": []},
        extraction_failed=True,
    )
    assert failed.quality_score <= base.quality_score


def test_component_keys() -> None:
    r = score_quality(job_title="X", job_description="Y " * 50, extraction=None, extraction_failed=False)
    assert set(r.components.keys()) == {
        "completeness",
        "clarity",
        "ai_keyword_density",
        "structural_coherence",
    }


def _unique_word_block(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


def test_score_quality_perfect_non_ai_posting_high() -> None:
    """JIE #329 — strong ops posting without AI keywords should not be capped ~0.65."""
    desc = (
        (
            "Regional Warehouse Operations Manager\n\n"
            "Responsibilities:\n"
            "- Lead safety program for forty associates\n"
            "- Coordinate inbound scheduling with vendors\n"
            "- Maintain inventory accuracy targets\n\n"
            "Qualifications:\n"
            "- Five years supervisory experience\n"
            "- Strong communication and coaching skills\n\n"
            "About the role\n"
            "We operate distribution centers across the southwest region.\n"
        )
        + "\n"
        + _unique_word_block(120)
    )
    ext = {
        "skills": [
            {"skill_name": "Forklift"},
            {"skill_name": "Inventory control"},
            {"skill_name": "Safety compliance"},
            {"skill_name": "Coaching"},
            {"skill_name": "Planning"},
        ],
        "tools": [{"tool_name": "WMS"}],
        "tasks": [{"text": "Supervise daily receiving"}],
        "responsibilities": [{"responsibility_description": "Own KPI reporting for the shift"}],
        "context": [{"text": "Cold chain environment"}],
    }
    r = score_quality(
        job_title="Regional Warehouse Operations Manager",
        job_description=desc,
        extraction=ext,
        extraction_failed=False,
    )
    assert r.quality_score >= 0.85
    assert r.components["ai_keyword_density"] < 0.85


def test_score_quality_perfect_ai_posting_high() -> None:
    """JIE #329 — AI-heavy tech posting should reach the top of the rubric."""
    desc = (
        (
            "Senior Machine Learning Engineer\n\n"
            "Responsibilities:\n"
            "- Train large language models and deploy PyTorch on Azure\n"
            "- Build ML pipelines with Kubernetes and Kafka\n"
            "- Collaborate on NLP and generative AI initiatives\n\n"
            "Requirements\n"
            "Deep learning, TensorFlow, AWS and SQL experience required.\n"
        )
        + "\n"
        + _unique_word_block(400)
    )
    ext = {
        "skills": [
            {"skill_name": "PyTorch"},
            {"skill_name": "Python"},
            {"skill_name": "SQL"},
            {"skill_name": "MLOps"},
            {"skill_name": "NLP"},
        ],
        "tools": [{"tool_name": "Kubernetes"}, {"tool_name": "Azure"}],
        "tasks": [{"text": "Ship model endpoints"}],
        "responsibilities": [{"responsibility_description": "Partner with data science"}],
        "context": [{"text": "LLM fine-tuning"}],
    }
    r = score_quality(
        job_title="Senior Machine Learning Engineer",
        job_description=desc,
        extraction=ext,
        extraction_failed=False,
    )
    assert r.quality_score >= 0.95


def test_score_quality_sparse_posting_low() -> None:
    """JIE #329 — thin title + description stays in the bottom band."""
    r = score_quality(
        job_title="X",
        job_description="Part-time helper.",
        extraction=None,
        extraction_failed=False,
    )
    assert r.quality_score < 0.45
