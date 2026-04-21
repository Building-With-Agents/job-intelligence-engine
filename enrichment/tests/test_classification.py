"""Pure classification tests — no database."""

from __future__ import annotations

from enrichment.classification import (
    build_job_corpus,
    classify_job,
    classify_role,
    classify_seniority,
    filter_industry_sectors_for_role_classification,
    flatten_extraction_json,
    is_excluded_industry_sector_title,
    tokenize,
)


def test_tokenize_basic() -> None:
    assert tokenize("Senior Data Engineer") == {"senior", "data", "engineer"}
    assert tokenize("") == set()
    assert "a" not in tokenize("a b cd")  # min len 2


def test_classify_role_hint_data_engineer() -> None:
    role = classify_role(
        "Senior Data Engineer",
        "senior data engineer",
        [("1", "Irrelevant Title")],
        [],
    )
    assert role == "Data Engineering"


def test_classify_role_tech_overlap() -> None:
    refs = [("t1", "Cloud Infrastructure"), ("t2", "Other")]
    corpus = "we need cloud and infrastructure experience"
    role = classify_role("Platform SRE", corpus, refs, [])
    assert role == "Cloud Infrastructure"


def test_classify_role_sector_fallback() -> None:
    role = classify_role(
        "Hospital IT Lead",
        "hospital healthcare technology patient systems",
        [],
        [("s1", "Healthcare Technology")],
    )
    assert role == "Healthcare Technology"


def test_classify_role_na_placeholder_sector_does_not_beat_real_sector() -> None:
    """N/A Not an IT role must not win sector fallback when a real sector scores higher."""
    role = classify_role(
        "Hospital IT Lead",
        "hospital healthcare technology patient systems",
        [],
        [
            ("bad", "N/A Not an IT role"),
            ("s1", "Healthcare Technology"),
        ],
    )
    assert role == "Healthcare Technology"


def test_classify_role_only_meta_sectors_yields_unclassified() -> None:
    """After filtering placeholder sectors, empty list → no sector fallback."""
    role = classify_role(
        "Obscure Role",
        "it department not listed obscure",
        [],
        [("na", "N/A Not an IT role")],
    )
    assert role == "unclassified"


def test_classify_role_sector_fallback_requires_stronger_overlap() -> None:
    """Single-token sector overlap scores 1; sector bar is 2 when min_score is 1."""
    role = classify_role(
        "Store Clerk",
        "retail sales register",
        [],
        [("s1", "Retail")],
    )
    assert role == "unclassified"


def test_is_excluded_industry_sector_title() -> None:
    assert is_excluded_industry_sector_title("N/A Not an IT role")
    assert is_excluded_industry_sector_title("  not classified (misc) ")
    assert not is_excluded_industry_sector_title("Healthcare Technology")


def test_filter_industry_sectors_for_role_classification() -> None:
    rows = [
        ("a", "N/A Not an IT role"),
        ("b", "Healthcare Technology"),
    ]
    assert filter_industry_sectors_for_role_classification(rows) == [("b", "Healthcare Technology")]


def test_classify_role_unclassified() -> None:
    role = classify_role(
        "Obscure Title XYZ",
        "obscure title xyz",
        [("t1", "Quantum Cryptography")],
        [("s1", "Aerospace Defense")],
    )
    assert role == "unclassified"


def test_classify_role_tie_breaker_lexicographic_id() -> None:
    """Equal overlap and title length → larger ref id wins."""
    role = classify_role(
        "Job",
        "beta fish",
        [("t1", "Alpha Beta"), ("t2", "Gamma Beta")],
        [],
    )
    assert role == "Gamma Beta"


def test_classify_seniority_executive_over_senior() -> None:
    assert classify_seniority("Senior VP of Engineering", None, None) == "executive"


def test_classify_seniority_lead_backend() -> None:
    assert classify_seniority("Lead Backend Engineer", None, None) == "lead"


def test_classify_seniority_junior() -> None:
    assert classify_seniority("Junior Software Engineer", None, None) == "junior"


def test_classify_seniority_intern_flag() -> None:
    assert classify_seniority("Software Engineer", None, None, is_internship=True) == "intern"


def test_classify_seniority_mid_ic_fallback() -> None:
    assert classify_seniority("Machine Learning Engineer", None, None) == "mid"


def test_classify_seniority_unknown() -> None:
    assert classify_seniority("Specialist", None, None) == "unknown"


def test_classify_seniority_description_fallback() -> None:
    assert classify_seniority("Specialist", "This is a senior level role.", None) == "senior"


def test_flatten_extraction_json_nested() -> None:
    skills = [{"skill_name": "Python", "source_span": {"text": "Python 3"}}]
    tasks = [{"task_description": "Build APIs"}]
    out = flatten_extraction_json(skills, None, tasks, None, None)
    assert "python" in out.lower()
    assert "apis" in out.lower()


def test_build_job_corpus_with_extraction() -> None:
    ext = {
        "skills": [{"label": "Go"}],
        "tools": [],
        "tasks": [],
        "responsibilities": [],
        "context": [],
    }
    c = build_job_corpus("Engineer", "Use tools", ext)
    assert "go" in c.lower()


def test_classify_job_integration() -> None:
    tech = [("1", "Software Engineering")]
    role, sen = classify_job(
        "Junior Software Engineer",
        None,
        None,
        tech,
        [],
    )
    assert role == "Software Engineering"
    assert sen == "junior"
