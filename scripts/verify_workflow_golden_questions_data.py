#!/usr/bin/env python3
"""Verify workflow golden-question data (JIE seed fixtures or live DB).

Workflow intent: **process, pipeline, sequence** — answered from
``job_postings`` + ``employer_profiles`` (via ``companies``) + ``extracted_intelligence``
(tasks / responsibilities for operational sequence; skills for context).
``role_classification`` guard **#197** (exclude ``N/A Not an IT role``) everywhere.

  python scripts/verify_workflow_golden_questions_data.py

- **Fixture mode** (no ``PYTHON_DATABASE_URL``): ORM-equivalent counts on
  ``scripts/pg-seed-data/fixtures/``.
- **Live mode**: when ``PYTHON_DATABASE_URL`` is set, ``from_sql()`` runs
  matching probes on PostgreSQL.

**PASS** when ``n_posts >= 10`` and ``n_ei >= 10`` (same as curriculum verify).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "scripts" / "pg-seed-data" / "fixtures"


@dataclass
class Out:
    status: str
    n_posts: int
    n_ei: int
    note: str


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _norm_cid(s: str | None) -> str:
    if not s:
        return ""
    return str(s).lower().replace("-", "")


def _bpx(jp: dict) -> bool:
    loc = f"{jp.get('location') or ''} {jp.get('county') or ''} {jp.get('borderplex_subregion') or ''}".lower()
    if jp.get("borderplex_subregion"):
        return True
    return bool(
        re.search(
            r"el paso|las cruces|santa teresa|juarez|borderplex|elpaso|sunland|dona|otero",
            loc,
        )
    )


def _it_role_ok(jp: dict) -> bool:
    """Issue #197 — exclude non-IT bucket."""
    return (jp.get("role_classification") or "").strip() != "N/A Not an IT role"


def _status(n_posts: int, n_ei: int) -> str:
    if n_posts < 1 or n_ei < 1:
        return "FAIL"
    if n_posts < 10 or n_ei < 10:
        return "PARTIAL"
    return "PASS"


def _ep_for(jp: dict, ep_by_cid: dict[str, dict], ep_by_pid: dict[str, dict]) -> dict | None:
    cid = _norm_cid(jp.get("company_id"))
    e = ep_by_cid.get(cid)
    if e is None and jp.get("employer_profile_id"):
        e = ep_by_pid.get(_norm_cid(jp.get("employer_profile_id")))
    return e


def _ei_workflow_ok(ei: dict) -> bool:
    if ei.get("extraction_failed") is True:
        return False
    tasks = ei.get("tasks") or []
    resp = ei.get("responsibilities") or []
    return (isinstance(tasks, list) and len(tasks) > 0) or (isinstance(resp, list) and len(resp) > 0)


def from_fixtures() -> list[Out]:
    njs: dict[tuple[str, str], int] = {}
    for n in _load("normalized_jobs.json"):
        njs[(str(n.get("source") or ""), str(n.get("external_id") or ""))] = int(n["id"])
    ei_list = _load("extracted_intelligence.json")
    ei_by_nj: dict[int, dict] = {int(e["normalized_job_id"]): e for e in ei_list}
    jps = _load("job_postings.json")
    eps = _load("employer_profiles.json")
    ep_by_cid = {_norm_cid(e.get("company_id")): e for e in eps}
    ep_by_pid = {_norm_cid(e.get("id")): e for e in eps}

    def txt(jp: dict) -> str:
        return f"{jp.get('job_title', '')} {jp.get('job_description', '')}".lower()

    def walk(pred: Callable[[dict], bool], desc: str) -> Out:
        n_posts = 0
        n_ei = 0
        for jp in jps:
            if not _bpx(jp) or not _it_role_ok(jp):
                continue
            if _ep_for(jp, ep_by_cid, ep_by_pid) is None:
                continue
            if not pred(jp):
                continue
            n_posts += 1
            k = (str(jp.get("source") or ""), str(jp.get("external_id") or ""))
            nj_id = njs.get(k)
            if nj_id is None or nj_id not in ei_by_nj:
                continue
            ei = ei_by_nj[nj_id]
            if not _ei_workflow_ok(ei):
                continue
            n_ei += 1
        st = _status(n_posts, n_ei)
        if st == "FAIL":
            nnote = (
                "No Borderplex + IT + employer_profiles + theme match with usable "
                "`extracted_intelligence` (tasks/responsibilities) for this track."
            )
        elif st == "PARTIAL":
            thin = []
            if n_posts < 10:
                thin.append(f"`job_postings`={n_posts}(<10)")
            if n_ei < 10:
                thin.append(f"EI rows with tasks|resp={n_ei}(<10)")
            nnote = "Sparse: " + "; ".join(thin)
        else:
            nnote = (
                "Workflow path: `employer_profiles` on `company_id` / `employer_profile_id`; "
                "EI `tasks`/`responsibilities` for step-like signals; #197 on postings."
            )
        nnote += f" {desc} Counts: postings={n_posts}, n_ei={n_ei}."
        return Out(st, n_posts, n_ei, nnote)

    themes: list[tuple[Callable[[dict], bool], str]] = [
        (
            lambda j: bool(
                re.search(
                    r"ci/cd|ci cd|continuous integration|continuous deployment|"
                    r"jenkins|github actions|argocd|"
                    r"terraform apply|release pipeline|deployment pipeline",
                    txt(j),
                )
            ),
            "Q1: CI/CD & release pipeline keywords in posting text.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"\bagile\b|scrum|sprint|stand-?up|sprint planning|backlog|kanban",
                    txt(j),
                )
            ),
            "Q2: Agile / Scrum / sprint process keywords.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"data pipeline|etl|orchestrat|airflow|dbt|"
                    r"workflow|job scheduler",
                    txt(j),
                )
            ),
            "Q3: Data pipeline / ETL / orchestration.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"devops|sre\b|on-?call|incident|pager|runbook|"
                    r"grafana|prometheus",
                    txt(j),
                )
            ),
            "Q4: DevOps / SRE / incident & on-call.",
        ),
        (
            lambda j: (
                bool(re.search(r"clearance|dod|secret|federal|government", txt(j)))
                and bool(re.search(r"security|cyber|engineer|developer|network|systems", txt(j)))
            ),
            "Q5: Clearance + federal / defense hiring pipeline (IT).",
        ),
        (
            lambda j: bool(
                re.search(
                    r"ehr|emr|epic|hipaa|clinical|health( care)?( it)?|fhir|"
                    r"interoperab",
                    txt(j),
                )
            ),
            "Q6: Healthcare EHR / clinical-IT implementation workflow.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"mlops|model (deploy|registry|serving|monitoring)|"
                    r"kubeflow|sagemaker",
                    txt(j),
                )
            ),
            "Q7: MLOps / model lifecycle.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"cloud (migration|architect|strategy)|infrastructure|landing zone",
                    txt(j),
                )
            ),
            "Q8: Cloud migration / architecture phases.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"help( ?desk| ?support)?|service desk|tier (1|2|i|ii)|escalat",
                    txt(j),
                )
            ),
            "Q9: Service desk / tiering / escalation path.",
        ),
        (
            lambda j: bool(
                re.search(
                    r"cross-?functional|stakeholder|across teams|coordinat",
                    txt(j),
                )
            ),
            "Q10: Cross-team / stakeholder coordination (process).",
        ),
    ]
    return [walk(p, d) for p, d in themes]


def from_sql() -> list[tuple[str, int, int, str]] | None:
    url = (os.environ.get("PYTHON_DATABASE_URL") or "").strip()
    if not url:
        return None
    from sqlalchemy import create_engine, text

    bpx = """(
        jp.borderplex_subregion IS NOT NULL
        OR LOWER(COALESCE(jp.location, '')) ~* 'el[[:space:]]*paso|las[[:space:]]*cruces|santa[[:space:]]*teresa|juarez|border|sunland'
        OR LOWER(COALESCE(jp.county, '')) ~* 'el paso|dona|santa|otero'
    )"""
    it_guard = "AND TRIM(COALESCE(jp.role_classification, '')) IS DISTINCT FROM 'N/A Not an IT role'"
    ei_shape = """AND (ei.extraction_failed IS NULL OR ei.extraction_failed = FALSE)
        AND (jsonb_array_length(COALESCE(ei.tasks, '[]'::jsonb)) > 0
             OR jsonb_array_length(COALESCE(ei.responsibilities, '[]'::jsonb)) > 0)"""
    from_ep = """
        FROM dbo.job_postings jp
        INNER JOIN dbo.companies c ON c.company_id::text = jp.company_id::text
        INNER JOIN dbo.employer_profiles ep ON ep.company_id::text = c.company_id::text
    """
    join_ei = """
        INNER JOIN dbo.normalized_jobs nj
            ON nj.source = jp.source AND nj.external_id = jp.external_id
        INNER JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id
    """

    probes: list[tuple[str, str]] = [
        (
            """AND (
            (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(ci/cd|ci cd|continuous integration|continuous deployment|jenkins|github actions|argocd|terraform|release pipeline|deployment pipeline)' )""",
            "Q1 CI/CD",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(agile|scrum|sprint|stand-?up|sprint planning|backlog|kanban)'""",
            "Q2 agile",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(data pipeline|etl|orchestrat|airflow|dbt|workflow|job scheduler)'""",
            "Q3 ETL",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(devops|sre|on-?call|incident|pager|runbook|grafana|prometheus)'""",
            "Q4 SRE",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(clearance|dod|secret|federal|government)'
        AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(security|cyber|engineer|developer|network|systems)'""",
            "Q5 clearance",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(ehr|emr|epic|hipaa|clinical|health( care( it)?| it)|fhir|interoperab)'""",
            "Q6 health",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(mlops|model deploy|model registry|model serving|model monitoring|kubeflow|sagemaker)'""",
            "Q7 MLOps",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(cloud (migration|architect|strategy)|infrastructure|landing zone)'""",
            "Q8 cloud",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(help( ?desk| ?support)?|service desk|tier (1|2|i|ii)|escalat)'""",
            "Q9 support",
        ),
        (
            """AND (COALESCE(jp.job_title, '') || ' ' || COALESCE(jp.job_description, '')) ~*
            '(cross-?functional|stakeholder|across teams|coordinat)'""",
            "Q10 xfn",
        ),
    ]
    out: list[tuple[str, int, int, str]] = []
    eng = create_engine(url, pool_pre_ping=True)
    with eng.connect() as conn:
        for extra_where, label in probes:
            q_posts = f"""SELECT COUNT(DISTINCT jp.job_posting_id) {from_ep}
                WHERE {bpx} {it_guard} {extra_where}"""
            q_ei = f"""SELECT COUNT(DISTINCT jp.job_posting_id) {from_ep} {join_ei}
                WHERE {bpx} {it_guard} {extra_where} {ei_shape}"""
            np = int(conn.execute(text(q_posts)).scalar() or 0)
            ne = int(conn.execute(text(q_ei)).scalar() or 0)
            out.append((label, np, ne, f"DB probe: {label}"))
    return out


def main() -> int:
    sqlr = from_sql()
    if sqlr is not None:
        print("Live `PYTHON_DATABASE_URL` probe (Borderplex + #197 + employer_profiles + EI shape):\n")
        for label, np, ne, _ in sqlr:
            print(f"  {label}:  job_postings={np},  EI(tasks|resp)={ne}")
        print()
    for i, o in enumerate(from_fixtures(), start=1):
        print(f"Q{i}\t{o.status}\tposts={o.n_posts}\tEI={o.n_ei}\n  {o.note}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
