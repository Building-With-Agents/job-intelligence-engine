#!/usr/bin/env python3
"""Verify employer golden-question data availability (JIE seed fixtures or live DB).

Without ``PYTHON_DATABASE_URL``: loads ``scripts/pg-seed-data/fixtures/`` and counts
rows matching the same intent as the verification SQL (``companies`` + ``employer_profiles``
+ ``job_postings``; ``role_classification`` guard #197).

With ``PYTHON_DATABASE_URL`` set: runs equivalent SQL in PostgreSQL.

  python scripts/verify_employer_golden_questions_data.py
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "scripts" / "pg-seed-data" / "fixtures"


@dataclass
class Row:
    n: int
    note: str
    use_threshold: int = 10  # < this => PARTIAL


def _norm_cid(s: str | None) -> str:
    if not s:
        return ""
    return str(s).lower().replace("-", "")


def _ts(jp: dict) -> datetime | None:
    for key in ("date_posted", "publish_date", "createdat"):
        d = jp.get(key)
        if not d or not isinstance(d, str):
            continue
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _within_months(jp: dict, months: int) -> bool:
    dt = _ts(jp)
    if dt is None:
        return True
    return (datetime.now(timezone.utc) - dt).days <= months * 31


def _txt(jp: dict) -> str:
    return f"{jp.get('job_title') or ''}\n{jp.get('job_description') or ''}".lower()


def _bpx(jp: dict) -> bool:
    """Borderplex-ish row (aligns with analytics / Q&A region filters)."""
    if jp.get("borderplex_subregion"):
        return True
    loc = f"{jp.get('location') or ''} {jp.get('county') or ''}".lower()
    return bool(
        re.search(
            r"el paso|las cruces|juarez|santa teresa|sunland|border|dona ana|otero",
            loc,
        )
    )


def _it_role_ok(jp: dict) -> bool:
    """Issue #197 — exclude non-IT bucket."""
    return (jp.get("role_classification") or "").strip() != "N/A Not an IT role"


def _status(n: int, t: int = 10) -> str:
    if n <= 0:
        return "FAIL"
    if n < t:
        return "PARTIAL"
    return "PASS"


def from_fixtures() -> list[Row]:
    companies = { _norm_cid(c.get("company_id")): c for c in _load("companies.json") }
    eps = _load("employer_profiles.json")
    ep_by_cid = { _norm_cid(e.get("company_id")): e for e in eps }
    ep_by_pid: dict[str, dict] = {}
    for e in eps:
        ep_by_pid[_norm_cid(e.get("id"))] = e
    jps: list[dict] = _load("job_postings.json")

    def ep_for(jp: dict) -> dict | None:
        cid = _norm_cid(jp.get("company_id"))
        e = ep_by_cid.get(cid)
        if e is None and jp.get("employer_profile_id"):
            e = ep_by_pid.get(_norm_cid(jp.get("employer_profile_id")))
        return e

    def cname(jp: dict) -> str:
        c = companies.get(_norm_cid(jp.get("company_id")))
        return (c or {}).get("company_name") or ""

    rows: list[Row] = []

    # 1: Highest IT posting volume in Borderplex (no recency filter); role_classification guard
    n1 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        n1 += 1
    rows.append(
        Row(
            n1,
            "Borderplex + `role_classification` != 'N/A Not an IT role' (#197); "
            "rank employers by `COUNT(*)` per `company_id` (join `companies`).",
        ),
    )

    # 2: agentic_era + tool keywords, distinct companies
    tools = re.compile(
        r"copilot|langchain|langgraph|\bllm\b|openai|cursor|chatgpt|anthropic",
        re.I,
    )
    cos: set[str] = set()
    for jp in jps:
        if (jp.get("temporal_period") or "").lower() != "agentic_era":
            continue
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        if not tools.search(_txt(jp)):
            continue
        cn = cname(jp)
        if cn:
            cos.add(cn)
    n2 = len(cos)
    rows.append(Row(n2, f"{n2} distinct companies (agentic_era+tool text); per-employer share = extra GROUP BY query"))

    # 3: Federal/contractor + IT + clearance
    c_pat = re.compile(
        r"clearance|secret|top secret|dod|defense contractor|aerospace|government.?tech|federal|ts/sci|security clearance",
        re.I,
    )
    itish = re.compile(
        r"software|developer|it |information technology|network|cyber|data engineer|systems? admin",
        re.I,
    )
    n3 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        t = _txt(jp)
        e = ep_for(jp) or {}
        st = (e.get("sector") or "") + " " + cname(jp) + " " + t
        if not c_pat.search(st) or not itish.search(t):
            continue
        n3 += 1
    rows.append(Row(n3, "clearance/contractor-family + IT-ish; employer_profiles.sector in JOIN"))

    # 4: Health + informatics + AI
    h = re.compile(
        r"health|ehr|emr|hospital|clinical|informatics|epic|cerner|hipaa|healthcare",
        re.I,
    )
    rolep = re.compile(
        r"data analyst|clinical|informatics|health.?it",
        re.I,
    )
    ai_ = re.compile(
        r"\bml\b|machine learning|llm|artificial intelligence|\bai\b(?!-)",
        re.I,
    )
    n4 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        t = _txt(jp)
        if h.search(t) and rolep.search(t) and ai_.search(t):
            n4 += 1
    rows.append(Row(n4, "health+analyst-class+AI in description/title; sector via employer join optional"))

    # 5: fintech + post_gpt4|agentic_era
    fin = re.compile(r"fintech|payments?|banking|merchant|stripe|visa|processing", re.I)
    n5 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        tp = (jp.get("temporal_period") or "").lower()
        if tp not in ("post_gpt4", "agentic_era"):
            continue
        t = _txt(jp)
        na = (jp.get("naics_code") or "")[:2]
        if not fin.search(t) and na != "52":
            continue
        n5 += 1
    rows.append(Row(n5, "post_gpt4|agentic_era + fintech text or NAICS 52**"))

    # 6: Data eng/sci + 12m + AI skill
    d_role = re.compile(
        r"data engineer|data scientist|data science",
        re.I,
    )
    ai_sk = re.compile(
        r"llm|pytorch|tensorflow|machine learning|\bmlops\b|\bgenai\b|generative|langchain|openai",
        re.I,
    )
    n6 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        t = _txt(jp)
        rc = (jp.get("role_classification") or "").lower()
        if not d_role.search(t) and "data" not in rc and "engineer" not in rc and "scientist" not in rc:
            continue
        if not ai_sk.search(t):
            continue
        if not _within_months(jp, 14):
            continue
        n6 += 1
    rows.append(Row(n6, "data engineer|scientist + AI skill text + 14m if dated"))

    # 7: Non-academic employers, entry-level IT (junior/intern/title heuristics)
    acad_name = re.compile(
        r"utep|nmsu|e\.?p\.?c\.?c|new mexico state|dona ana community|el paso community",
        re.I,
    )
    entry_re = re.compile(
        r"entry|graduate|associat|junior|intern|early career|level i\b| i-",
        re.I,
    )
    n7 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        if acad_name.search(cname(jp).lower()):
            continue
        sl = (jp.get("seniority_level") or "").lower()
        if sl in ("junior", "intern") or entry_re.search(
            f"{jp.get('job_title') or ''} {jp.get('job_description') or ''}"[:800],
        ):
            n7 += 1
    rows.append(
        Row(
            n7,
            "Non-academic (company name) + Borderplex + IT role + entry-level signals; "
            "GROUP BY employer for volume (#197).",
        ),
    )

    # 8: Employers with widest variety of IT role types (distinct role_classification per company)
    rc_by_c: dict[str, set[str]] = defaultdict(set)
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        cid = jp.get("company_id") or ""
        rc = (jp.get("role_classification") or "").strip()
        if cid and rc:
            rc_by_c[str(cid)].add(rc)
    n8 = sum(1 for s in rc_by_c.values() if len(s) >= 3)
    rows.append(
        Row(
            n8,
            f"Employers with ≥3 distinct `role_classification` in Borderplex+IT: {n8} "
            "(answer: `COUNT(DISTINCT role_classification) GROUP BY company_id` #197).",
        ),
    )

    # 9: legal / e-discovery
    lp = re.compile(
        r"e-?discovery|ediscovery|legal(\s+)?tech|litigation support|attorney|docket|law firm",
        re.I,
    )
    n9 = 0
    n9_12 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        t = _txt(jp)
        if not lp.search(t):
            continue
        n9 += 1
        if _within_months(jp, 14):
            n9_12 += 1
    rows.append(Row(n9, f"legal|ediscovery in text: total n={n9}, ≤14m dated n={n9_12}"))

    # 10: IT postings with salary_min and salary_max (compensation benchmark; no posting_freshness)
    n10 = 0
    for jp in jps:
        if not _bpx(jp) or not _it_role_ok(jp):
            continue
        if jp.get("salary_min") is None or jp.get("salary_max") is None:
            continue
        n10 += 1
    rows.append(
        Row(
            n10,
            "Borderplex + IT + `salary_min`/`salary_max` populated; rank employers for comp bench (#197).",
        ),
    )

    return rows


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def from_sql() -> list[Row] | None:
    url = os.environ.get("PYTHON_DATABASE_URL", "").strip()
    if not url:
        return None
    from sqlalchemy import create_engine, text

    eng = create_engine(url, pool_pre_ping=True)
    # PostgreSQL ~* is case-insensitive POSIX regex (not Python (?i) syntax).
    bpx_sql = """(
        jp.borderplex_subregion IS NOT NULL
        OR LOWER(COALESCE(jp.location, '')) ~* 'el[[:space:]]*paso|las[[:space:]]*cruces|juarez|santa[[:space:]]*teresa|sunland|border|dona|otero'
    )"""
    it_guard = "AND TRIM(COALESCE(jp.role_classification, '')) IS DISTINCT FROM 'N/A Not an IT role'"

    stmts: list[str] = [
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}""",
        f"""SELECT COUNT(DISTINCT c.company_id) FROM dbo.job_postings jp
     INNER JOIN dbo.companies c ON c.company_id::text = jp.company_id::text
     INNER JOIN dbo.employer_profiles ep ON ep.company_id::text = c.company_id::text
     WHERE {bpx_sql} {it_guard}
     AND LOWER(TRIM(COALESCE(jp.temporal_period, ''))) = 'agentic_era'
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* '(copilot|langchain|langgraph|llm|openai|cursor|chatgpt|anthropic)'""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     INNER JOIN dbo.companies c ON c.company_id::text = jp.company_id::text
     LEFT JOIN dbo.employer_profiles ep ON ep.company_id::text = c.company_id::text
     WHERE {bpx_sql} {it_guard}
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* '(clearance|secret|top secret|dod|defense|aerospace|federal|security clearance|government|contractor)'
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '') || ' ' || COALESCE(ep.sector, '')) ~* '(software|engineer|developer|it |cyber|data|network|information technolog)'""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* '(health|hospital|ehr|emr|clinical|informatics|hipaa|healthcare)'
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* '(analyst|informatics|data|clinical)'
     AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* '(^|[^a-z]|[[:space:]])(ml|llm|machine learning|artificial intelligence| ai )'""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}
     AND LOWER(TRIM(COALESCE(jp.temporal_period, ''))) IN ('post_gpt4', 'agentic_era')
     AND ( (COALESCE(jp.job_title, '') || COALESCE(jp.job_description, '')) ~* '(fintech|payment|bank|merchant|stripe|processing|visa)'
         OR (jp.naics_code IS NOT NULL AND SUBSTRING(jp.naics_code, 1, 2) = '52') )""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}
     AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.role_classification, '')) ~* '(data engineer|data scientist|data science)'
     AND (COALESCE(jp.job_title, '') || COALESCE(jp.job_description, '')) ~* '(llm|ml |machine learning|openai|pytorch|tensorflow|artificial|generative)'
     AND COALESCE(jp.date_posted, jp.publish_date) >= (CURRENT_TIMESTAMP - INTERVAL '14 months')""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     INNER JOIN dbo.companies c ON c.company_id::text = jp.company_id::text
     WHERE {bpx_sql} {it_guard}
     AND c.company_name !~* '(utep|new mexico state|nmsu|e\\.?p\\.?c\\.?c|dona ana|el paso community|community college)'
     AND (
       LOWER(TRIM(COALESCE(jp.seniority_level, ''))) IN ('junior', 'intern')
       OR (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~* 'entry( |-|level)?|graduate|associat|early career|junior|intern' )""",
        f"""SELECT COUNT(*) FROM (
     SELECT jp.company_id
     FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard} AND jp.company_id IS NOT NULL
     GROUP BY jp.company_id
     HAVING COUNT(DISTINCT TRIM(COALESCE(jp.role_classification, ''))) >= 3
    ) t""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}
     AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~* '(e-?discovery|ediscovery|legal.?tech|litigation|law firm|attorney|docket)'
     AND COALESCE(jp.date_posted, jp.publish_date) >= (CURRENT_TIMESTAMP - INTERVAL '14 months')""",
        f"""SELECT COUNT(*) FROM dbo.job_postings jp
     WHERE {bpx_sql} {it_guard}
     AND jp.salary_min IS NOT NULL AND jp.salary_max IS NOT NULL""",
    ]
    out: list[Row] = []
    notes = [
        "Q1 Borderplex + IT role guard (#197), volume by employer",
        "Q2 join companies+employer_profiles+job_postings, agentic_era+AI tools",
        "Q3 clearance + IT-ish",
        "Q4 health IT + role + AI (regex for ml/ai may need tuning on PG)",
        "Q5 fintech/NAICS52 + period",
        "Q6 data roles + AI + 14m",
        "Q7 non-academic + entry-level IT",
        "Q8 employers with >=3 distinct role_classification (variety)",
        "Q9 legal+14m",
        "Q10 IT + salary_min/salary_max populated",
    ]
    with eng.connect() as c:
        for s, n in zip(stmts, notes, strict=True):
            n_val = c.execute(text(s)).scalar() or 0
            out.append(Row(int(n_val), f"DB: {n}"))

    return out


def main() -> int:
    sqlr = from_sql()
    if sqlr is not None:
        res = sqlr
        where = "live `PYTHON_DATABASE_URL`"
    else:
        res = from_fixtures()
        where = f"ORM-equivalent on `{FIX.name}` (no DB in env when script ran)"
    for i, r in enumerate(res, start=1):
        s = _status(r.n, r.use_threshold)
        print(f"Q{i}\t{s}\t{r.n}\t{where}\n  {r.note}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
