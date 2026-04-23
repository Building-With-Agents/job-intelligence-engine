#!/usr/bin/env python3
"""Verify curriculum golden questions — ``job_postings`` + ``extracted_intelligence`` + ``skills``.

Path (no ``canonical_roles``; skill demand from posting text + extraction):
  ``job_postings`` (``source``, ``external_id``) → ``normalized_jobs`` →
  ``extracted_intelligence`` (JSONB ``skills``: ``skill_name``, optional ``skill_id``) →
  match to ``dbo.skills`` when IDs/names align.

  python scripts/verify_curriculum_golden_questions_data.py

With ``PYTHON_DATABASE_URL``, ``_sql_probe()`` runs live row-count SQL (Borderplex + theme
+ #197 guard) against PostgreSQL.
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
    n_skill_mentions: int
    n_taxonomy_hits: int
    note: str


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


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


def _it197(jp: dict) -> bool:
    """Issue #197: exclude non-IT bucket."""
    return (jp.get("role_classification") or "").strip() != "N/A Not an IT role"


def _status(n_posts: int, n_ei: int) -> str:
    if n_posts < 1 or n_ei < 1:
        return "FAIL"
    if n_posts < 10 or n_ei < 10:
        return "PARTIAL"
    return "PASS"


def from_fixtures() -> list[Out]:
    njs_list = _load("normalized_jobs.json")
    njs: dict[tuple[str, str], int] = {}
    for n in njs_list:
        njs[(str(n.get("source") or ""), str(n.get("external_id") or ""))] = int(n["id"])
    ei_list = _load("extracted_intelligence.json")
    ei_by_nj: dict[int, dict] = {int(e["normalized_job_id"]): e for e in ei_list}
    jps = _load("job_postings.json")
    skills = _load("skills.json")
    sk_ids = {str(s.get("skill_id", "")).lower().replace("-", "") for s in skills}
    sk_names_lower = {str(s.get("skill_name", "")).lower() for s in skills if s.get("skill_name")}

    def txt(jp: dict) -> str:
        return f"{jp.get('job_title', '')} {jp.get('job_description', '')} {jp.get('role_classification', '')}".lower()

    def walk(pred: Callable[[dict], bool]) -> Out:
        n_posts = 0
        n_ei = 0
        sm = 0
        tax = 0
        for jp in jps:
            if not _bpx(jp) or not _it197(jp):
                continue
            if not pred(jp):
                continue
            n_posts += 1
            k = (str(jp.get("source") or ""), str(jp.get("external_id") or ""))
            nj_id = njs.get(k)
            if nj_id is None or nj_id not in ei_by_nj:
                continue
            ei = ei_by_nj[nj_id]
            if ei.get("extraction_failed") is True:
                continue
            n_ei += 1
            for s in ei.get("skills") or []:
                if not isinstance(s, dict):
                    continue
                name = (s.get("skill_name") or "").strip()
                if name:
                    sm += 1
                sid = str(s.get("skill_id") or "").lower().replace("-", "")
                if (sid and sid in sk_ids) or (name and name.lower() in sk_names_lower):
                    tax += 1
        st = _status(n_posts, n_ei)
        if st == "FAIL":
            nnote = "no Borderplex + IT-guard + theme match with `extracted_intelligence` for this track."
        elif st == "PARTIAL":
            thin = []
            if n_posts < 10:
                thin.append(f"`job_postings`={n_posts}(<10)")
            if n_ei < 10:
                thin.append(f"`extracted_intelligence`={n_ei}(<10)")
            nnote = "Sparse: " + "; ".join(thin)
        else:
            nnote = "Skill-frequency curriculum signal: group `skill_name` from EI; join to `skills` for taxonomy. "
        nnote += f"Counts: postings={n_posts}, EI={n_ei}, skill lines={sm}, `skills` hits≈{tax}."
        return Out(st, n_posts, n_ei, sm, tax, nnote)

    def fin_pred(j: dict) -> bool:
        return (
            bool(
                re.search(
                    r"fintech|payment(s| processing)?|bank(ing| tech)?|stripe|merchant|pci-?dss|"
                    r"financial services( tech)?|swift (payment)?|visa( direct)?|payment gateway",
                    txt(j),
                )
            )
            or (str(j.get("naics_code") or ""))[:2] == "52"
        )

    themes: list[Callable[[dict], bool]] = [
        lambda j: bool(
            re.search(
                r"data engineer|data eng\b|etl|data pipeline|databricks|snowflake|dbt|apache (spark|kafka)",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"llm|langchain|langgraph|ai agent|agentic|prompt eng|openai|rag\b|generative|anthropic|vector",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"cyber|secops|security (engineer|analyst)|\bcissp\b|comptia|iam\b|"
                r"penetration|zero trust|soc (analyst|tier)",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"cloud (architect|engineer|infrastructure|solution|platform)|\b(gcp|azure|aws) (architect|solutions?)|"
                r"\bkubernetes\b|\bterraform\b",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"frontend|front-?end|\breact\b|vue|angular|typescript|svelte|ui engineer|web (developer|engineer)",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"mlops|kubeflow|ml (platform|infrastructure)|sagemaker|vertex ai|"
                r"model (serving|registry|monitoring|deployment)",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"devops|sre\b|site reliability|infrastructure as code|grafana|prometheus|"
                r"argocd|ci/cd|jenkins(?!-)",
                txt(j),
            )
        ),
        lambda j: bool(
            re.search(
                r"help( ?desk| ?support|desk)?|it support|system(s)? admin(istrator)?|"
                r"network (engineer|admin|technician)|\bcomptia\b|desktop support|service desk|a\+/|network\+",
                txt(j),
            )
        ),
        fin_pred,
        lambda j: bool(
            re.search(
                r"\behr\b|emr|\bepic\b|cerner|hipaa|clinical( data| informatics)?|"
                r"health( care)?( it|informatics)?|fhir|interoperab",
                txt(j),
            )
        ),
    ]
    return [walk(p) for p in themes]


def _sql_probe() -> list[tuple[str, int, int]] | None:
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
    patterns = [
        (r"data engineer|etl|databricks|snowflake|dbt|spark|kafka", "Q1 data eng"),
        (r"llm|langchain|langgraph|ai agent|prompt|RAG|generative|openai|anthropic", "Q2 AI"),
        (r"cyber|secops|security|CISSP|comptia|penetration|zero trust|IAM", "Q3 cyber"),
        (r"cloud|kubernetes|terraform|gcp|azure|aws", "Q4 cloud"),
        (r"frontend|react|vue|angular|typescript|web (developer|engineer)", "Q5 frontend"),
        (r"mlops|kubeflow|sagemaker|ml platform|model (serving|deployment)", "Q6 MLOps"),
        (r"devops|sre|site reliability|grafana|prometheus|infrastructure as code|ci\/cd|jenkins", "Q7 DevOps"),
        (
            r"help desk|it support|system(s)? admin|network (engineer|admin|technician)|comptia|service desk",
            "Q8 support",
        ),
        (r"fintech|payment|bank(ing)?|stripe|merchant|pci|financial", "Q9 fintech"),
        (r"EHR|EMR|epic|cerner|hipaa|clinical|health( care)?( IT|informatics)?|fhir", "Q10 health"),
    ]
    out: list[tuple[str, int, int]] = []
    eng = create_engine(url, pool_pre_ping=True)
    with eng.connect() as c:
        for pat, label in patterns:
            if "Q9" in label:
                base = f"""
            WITH m AS (
                SELECT nj.id AS norm_id
                FROM dbo.job_postings jp
                INNER JOIN dbo.normalized_jobs nj
                    ON nj.source = jp.source AND nj.external_id = jp.external_id
                WHERE {bpx} {it_guard}
                AND (
                    (COALESCE(jp.job_title,'') || COALESCE(jp.job_description,'') || COALESCE(jp.role_classification,'')) ~* :re
                    OR (jp.naics_code IS NOT NULL AND SUBSTRING(jp.naics_code, 1, 2) = '52')
                )
            )"""
            else:
                base = f"""
            WITH m AS (
                SELECT nj.id AS norm_id
                FROM dbo.job_postings jp
                INNER JOIN dbo.normalized_jobs nj
                    ON nj.source = jp.source AND nj.external_id = jp.external_id
                WHERE {bpx} {it_guard}
                AND (COALESCE(jp.job_title,'') || COALESCE(jp.job_description,'') || COALESCE(jp.role_classification,'')) ~* :re
            )"""
            q = (
                base
                + """
            SELECT
                (SELECT COUNT(DISTINCT norm_id) FROM m),
                (SELECT COUNT(*)
                 FROM m JOIN dbo.extracted_intelligence ei
                    ON ei.normalized_job_id = m.norm_id AND (ei.extraction_failed IS NULL OR ei.extraction_failed = FALSE)
                );
            """
            )
            a, b = c.execute(
                text(q),
                {"re": pat},
            ).one()
            out.append((label, int(a or 0), int(b or 0)))
    return out


def main() -> int:
    probe = _sql_probe()
    if probe is not None:
        print("Live DB probe (Borderplex + #197 + theme, join `extracted_intelligence`):\n")
        for label, a, b in probe:
            print(f"  {label}:  norm_ids={a},  EI_rows={b}")
        print()
    res = from_fixtures()
    for i, o in enumerate(res, start=1):
        print(
            f"Q{i}\t{o.status}\tposts={o.n_posts}\tEI={o.n_ei}\tskills_m={o.n_skill_mentions}\t"
            f"tax≈{o.n_taxonomy_hits}\n  {o.note}\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
