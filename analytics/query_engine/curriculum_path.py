"""Curriculum path — ORM-driven inputs for curriculum-generation synthesis (Week 10).

Resolves a target canonical role from the user question, then pulls skill demand,
velocity, tool/responsibility co-occurrence, and top employers for the Borderplex
using SQLAlchemy :class:`~sqlalchemy.orm.Session` queries (no string SQL).

.. note::

   ``skill_demand_weekly`` and ``skill_velocity`` are **global** weekly rollups
   (no ``canonical_role_id`` in those tables). Role/region/period scoping is applied
   by joining to ``job_postings`` + ``extracted_intelligence`` for step 2; step 3
   intersects velocity rows with skills observed on scoped postings. ``demand_pct``
   is the share of scoped postings in the target week that mention the skill.
   ``trend_4wk_pct`` in results maps to :attr:`SkillVelocity.week_over_week_change`.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import structlog
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    String,
    Text,
    and_,
    cast,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from common.data_store.models import (
    Base,
    CanonicalRole,
    Company,
    EmployerProfile,
    ExtractedIntelligence,
    NormalizedJob,
    SkillDemandWeekly,
    SkillVelocity,
)
from enrichment.classifiers.spam_preview import get_spam_thresholds

log = structlog.get_logger()

_BORDERPLEX_SUBREGIONS: Final[tuple[str, ...]] = (
    "el_paso",
    "las_cruces",
    "ciudad_juarez",
    "regional",
)
_TEMPORAL_ORDER: Final[tuple[str, ...]] = (
    "pre_chatgpt",
    "early_genai",
    "post_gpt4",
    "agentic_era",
)

_REGION_LABEL = "borderplex"


class _JobPosting(Base):
    """Read mapping for ``dbo.job_postings`` (columns needed for curriculum queries)."""

    __tablename__ = "job_postings"
    __table_args__ = {"schema": "dbo"}

    job_posting_id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    borderplex_subregion: Mapped[str | None] = mapped_column(String(32))
    temporal_period: Mapped[str | None] = mapped_column(Text)
    canonical_role_id: Mapped[str | None] = mapped_column(Text)
    employer_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        "employer_profile_id", UUID(as_uuid=True), nullable=True
    )
    date_posted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_spam: Mapped[bool | None] = mapped_column(Boolean)
    spam_score: Mapped[float | None] = mapped_column(Float)
    is_duplicate: Mapped[bool | None] = mapped_column(Boolean)


@dataclass(frozen=True)
class CurriculumInputs:
    """Inputs for curriculum synthesis — empty lists with ``data_flags`` when sparse."""

    canonical_role: str | None
    top_skills: list[dict[str, Any]]
    rising_skills: list[dict[str, Any]]
    co_occurring: list[dict[str, Any]]
    top_employers: list[dict[str, Any]]
    period: str
    region: str
    role_matched: bool
    data_flags: dict[str, bool]
    canonical_role_label: str | None = None


def _empty_data_flags() -> dict[str, bool]:
    """Keys align with the five curriculum queries; ``True`` means that query returned no rows."""
    return {
        "canonical_role": True,
        "top_skills": True,
        "rising_skills": True,
        "co_occurring": True,
        "top_employers": True,
    }


def _extract_role_phrase(question: str, role_names: Sequence[str] | None) -> str:
    if role_names:
        for r in role_names:
            s = (r or "").strip()
            if len(s) >= 2:
                return s
    q = (question or "").strip()
    m = re.search(
        r"(?i)(?:for|teach|toward|towards)\s+(?:a|an|the)?\s*"
        r"([a-z0-9][^\n?]{2,120}?)(?:\s+in\s+the|\s+over\s+the|\s+from\s+|\?|$)",
        q,
    )
    if m:
        return m.group(1).strip(" .,;")
    m2 = re.search(r"(?i)curriculum(?:\s+for)?\s+([a-z0-9][^\n?]{2,80})", q)
    if m2:
        return m2.group(1).strip(" .,;")
    return q[:200] if q else ""


def _spam_and_dedup_ok() -> Any:
    _, reject = get_spam_thresholds()
    jp = _JobPosting
    return and_(
        or_(jp.is_spam.is_(False), jp.is_spam.is_(None)),
        or_(jp.is_duplicate.is_(False), jp.is_duplicate.is_(None)),
        or_(jp.spam_score.is_(None), jp.spam_score <= reject),
    )


def _jp_nj_ei_base() -> tuple[type[_JobPosting], type[NormalizedJob], type[ExtractedIntelligence]]:
    return _JobPosting, NormalizedJob, ExtractedIntelligence


def _scoped_posting_predicates(role_id: str | None, temporal_period: str | None, week_start: date | None) -> list[Any]:
    jp, nj, _ei = _jp_nj_ei_base()
    p: list[Any] = [
        jp.source.isnot(None),
        jp.external_id.isnot(None),
        jp.date_posted.isnot(None),
        jp.borderplex_subregion.in_(_BORDERPLEX_SUBREGIONS),
        _spam_and_dedup_ok(),
    ]
    if role_id:
        p.append(jp.canonical_role_id == role_id)
    if temporal_period:
        p.append(jp.temporal_period == temporal_period)
    if week_start is not None:
        week_expr = cast(func.date_trunc("week", func.timezone("UTC", jp.date_posted)), Date)
        p.append(week_expr == week_start)
    return p


def _join_ei_to_jp() -> Any:
    jp, nj, ei = _jp_nj_ei_base()
    return (
        ei,
        and_(
            nj.id == ei.normalized_job_id,
            nj.source == jp.source,
            nj.external_id == jp.external_id,
        ),
    )


def _match_canonical_role(session: Session, phrase: str) -> tuple[str | None, str | None, bool]:
    """Return ``(role_id, label, matched)`` from ``canonical_roles`` or EI keyword fallback."""
    p = (phrase or "").strip()
    if len(p) < 2:
        return None, None, False

    # 1) Direct label / description / representative_titles match
    like = f"%{p}%"
    stmt = (
        select(CanonicalRole.role_id, CanonicalRole.label)
        .where(
            or_(
                CanonicalRole.label.ilike(like),
                CanonicalRole.description.ilike(like),
                cast(CanonicalRole.representative_titles, Text).ilike(like),
            )
        )
        .order_by(func.length(CanonicalRole.label))
        .limit(1)
    )
    row = session.execute(stmt).first()
    if row:
        return str(row[0]), str(row[1]), True

    # 2) Token overlap on labels
    tokens = [t for t in re.split(r"\W+", p.lower()) if len(t) > 2]
    if not tokens:
        return None, None, False
    best: tuple[str, str, int] | None = None
    for cr in session.scalars(select(CanonicalRole).limit(5000)).all():
        label_l = (cr.label or "").lower()
        desc_l = (cr.description or "").lower() if cr.description else ""
        blob = f"{label_l} {desc_l}"
        score = sum(1 for t in tokens if t in blob)
        if score > 0 and (best is None or score > best[2]):
            best = (str(cr.role_id), str(cr.label), score)
    if best:
        return best[0], best[1], True

    # 3) Keyword fallback: EI tasks/responsibilities text → majority canonical_role on postings
    jp, nj, ei = _jp_nj_ei_base()
    tok = tokens[0]
    like_tok = f"%{tok}%"
    stmt_fb = (
        select(jp.canonical_role_id, func.count().label("n"))
        .select_from(ei)
        .join(nj, nj.id == ei.normalized_job_id)
        .join(
            jp,
            and_(
                nj.source == jp.source,
                nj.external_id == jp.external_id,
            ),
        )
        .where(
            and_(
                ei.extraction_failed.is_(False),
                jp.canonical_role_id.isnot(None),
                jp.borderplex_subregion.in_(_BORDERPLEX_SUBREGIONS),
                or_(
                    cast(ei.tasks, Text).ilike(like_tok),
                    cast(ei.responsibilities, Text).ilike(like_tok),
                ),
                _spam_and_dedup_ok(),
            )
        )
        .group_by(jp.canonical_role_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    r2 = session.execute(stmt_fb).first()
    if not r2 or r2[0] is None:
        return None, None, False
    rid = str(r2[0])
    label_row = session.execute(select(CanonicalRole.label).where(CanonicalRole.role_id == rid)).first()
    label = str(label_row[0]) if label_row else None
    return rid, label, True


def _latest_skill_demand_week(session: Session) -> date | None:
    w = session.scalar(select(func.max(SkillDemandWeekly.week_start)))
    return w


def _latest_velocity_week(session: Session) -> date | None:
    w = session.scalar(select(func.max(SkillVelocity.velocity_week)))
    return w


def _pick_temporal_period(session: Session) -> str | None:
    """Most recent **observed** temporal period (enum order) within Borderplex postings."""
    jp = _JobPosting
    present = {
        r[0]
        for r in session.execute(
            select(jp.temporal_period)
            .where(
                and_(
                    jp.borderplex_subregion.in_(_BORDERPLEX_SUBREGIONS),
                    jp.temporal_period.isnot(None),
                )
            )
            .distinct()
        ).all()
    }
    for name in reversed(_TEMPORAL_ORDER):
        if name in present:
            return name
    return next(iter(present), None) if present else None


def _query_top_skills(
    session: Session,
    role_id: str,
    week_start: date,
    temporal_period: str | None,
) -> tuple[list[dict[str, Any]], int]:
    jp, nj, ei = _jp_nj_ei_base()
    base_scope = and_(
        *(_scoped_posting_predicates(role_id, temporal_period, week_start)),
        ei.extraction_failed.is_(False),
    )
    total = (
        session.scalar(
            select(func.count(func.distinct(jp.job_posting_id)))
            .select_from(ei)
            .join(nj, nj.id == ei.normalized_job_id)
            .join(jp, and_(nj.source == jp.source, nj.external_id == jp.external_id))
            .where(base_scope)
        )
        or 0
    )
    if total == 0 or week_start is None:
        return [], int(total)

    # ORM-friendly: load skills JSON for scoped rows, aggregate in Python
    rows = session.execute(
        select(ei.skills, jp.job_posting_id)
        .select_from(ei)
        .join(nj, nj.id == ei.normalized_job_id)
        .join(jp, and_(nj.source == jp.source, nj.external_id == jp.external_id))
        .where(base_scope)
    ).all()
    labels_count: Counter[str] = Counter()
    postings_per_skill: dict[str, set[str]] = {}
    for skills_json, jpid in rows:
        seen: set[str] = set()
        for item in skills_json or []:
            if not isinstance(item, dict):
                continue
            lab = (item.get("skill_name") or item.get("label") or "").strip()
            if not lab:
                continue
            key = lab.lower()
            if key in seen:
                continue
            seen.add(key)
            labels_count[lab] += 1
            postings_per_skill.setdefault(lab, set()).add(jpid)

    # Intersect with skill_demand_weekly for the same week (global table "anchor")
    allowed = {
        r[0]
        for r in session.execute(
            select(SkillDemandWeekly.skill_label).where(SkillDemandWeekly.week_start == week_start)
        ).all()
    }
    lower_allowed = {a.lower() for a in allowed}
    demand_rows: list[tuple[str, int, float]] = []
    for label in labels_count:
        d = len(postings_per_skill.get(label, set()))
        pct = (d / float(total)) if total else 0.0
        if label in allowed or label.lower() in lower_allowed:
            sdw_key = next((a for a in allowed if a.lower() == label.lower()), label)
            demand_rows.append((sdw_key, d, pct))
    # If intersection empty, fall back to role-scoped demand_pct (curriculum still valid)
    if not demand_rows:
        for label, _cnt in labels_count.most_common():
            d = len(postings_per_skill.get(label, set()))
            demand_rows.append((label, d, (d / float(total)) if total else 0.0))
    demand_rows.sort(key=lambda x: x[2], reverse=True)
    demand_rows = demand_rows[:15]
    out = [
        {
            "skill_label": lab,
            "posting_count": pc,
            "demand_pct": round(pct, 4),
        }
        for lab, pc, pct in demand_rows
    ]
    return out, int(total)


def _query_rising_skills(
    session: Session,
    role_id: str,
    vel_week: date,
    week_start: date,
    temporal_period: str | None,
) -> list[dict[str, Any]]:
    top, _t = _query_top_skills(session, role_id, week_start, temporal_period)
    role_skills = {d["skill_label"].lower() for d in top}
    if not role_skills and role_id:
        # skills observed on any scoped posting in region/period (ignore week) for intersection
        jp, nj, ei = _jp_nj_ei_base()
        base_scope = and_(
            *(_scoped_posting_predicates(role_id, temporal_period, None)),
            ei.extraction_failed.is_(False),
        )
        for skills_json, _jpid in session.execute(
            select(ei.skills)
            .select_from(ei)
            .join(nj, nj.id == ei.normalized_job_id)
            .join(jp, and_(nj.source == jp.source, nj.external_id == jp.external_id))
            .where(base_scope)
        ).all():
            for item in skills_json or []:
                if isinstance(item, dict):
                    lab = (item.get("skill_name") or item.get("label") or "").strip()
                    if lab:
                        role_skills.add(lab.lower())

    if not role_skills:
        return []

    stmt = (
        select(
            SkillVelocity.skill_label,
            SkillVelocity.week_over_week_change,
            SkillVelocity.four_week_trend,
            SkillVelocity.demand_count,
        )
        .where(
            and_(
                SkillVelocity.velocity_week == vel_week,
                SkillVelocity.week_over_week_change > 0.10,
            )
        )
        .order_by(SkillVelocity.week_over_week_change.desc())
    )
    out: list[dict[str, Any]] = []
    for lab, woc, fwt, dc in session.execute(stmt).all():
        if role_skills and str(lab).lower() not in role_skills:
            continue
        out.append(
            {
                "skill_label": str(lab),
                "trend_4wk_pct": float(woc),
                "four_week_trend": str(fwt),
                "demand_count": int(dc),
            }
        )
        if len(out) >= 10:
            break
    return out


def _query_co_occurring(session: Session, role_id: str, temporal_period: str | None) -> list[dict[str, Any]]:
    jp, nj, ei = _jp_nj_ei_base()
    base_scope = and_(
        *(_scoped_posting_predicates(role_id, temporal_period, None)),
        ei.extraction_failed.is_(False),
    )
    rows = session.execute(
        select(ei.tools, ei.responsibilities)
        .select_from(ei)
        .join(nj, nj.id == ei.normalized_job_id)
        .join(jp, and_(nj.source == jp.source, nj.external_id == jp.external_id))
        .where(base_scope)
    ).all()
    ctr: Counter[tuple[str, str]] = Counter()
    for tools, resps in rows:
        t_names: list[str] = []
        for t in tools or []:
            if not isinstance(t, dict):
                continue
            n = (t.get("tool_name") or t.get("label") or "").strip()
            if n:
                t_names.append(n)
        r_texts: list[str] = []
        for r in resps or []:
            if not isinstance(r, dict):
                continue
            txt = (r.get("text") or r.get("responsibility") or r.get("description") or r.get("title") or "").strip()
            if txt:
                r_texts.append(txt[:500])
        for tn in t_names:
            for rt in r_texts:
                ctr[(tn, rt)] += 1
    ranked = ctr.most_common(20)
    return [{"tool": a, "responsibility": b, "count": c} for (a, b), c in ranked]


def _query_top_employers(session: Session, role_id: str, temporal_period: str | None) -> list[dict[str, Any]]:
    jp = _JobPosting
    cond = and_(
        *(_scoped_posting_predicates(role_id, temporal_period, None)),
        jp.employer_profile_id.isnot(None),
    )
    stmt = (
        select(EmployerProfile.id, Company.company_name, func.count().label("n"))
        .select_from(jp)
        .join(EmployerProfile, EmployerProfile.id == jp.employer_profile_id)
        .join(Company, Company.company_id == EmployerProfile.company_id)
        .where(cond)
        .group_by(EmployerProfile.id, Company.company_name)
        .order_by(func.count().desc())
        .limit(5)
    )
    return [
        {
            "employer_profile_id": str(ep_id),
            "company_name": str(cname) if cname else "",
            "job_posting_count": int(n),
        }
        for ep_id, cname, n in session.execute(stmt).all()
    ]


def build_curriculum_inputs(
    session: Session,
    question: str,
    role_names: Sequence[str] | None = None,
) -> CurriculumInputs:
    """Build :class:`CurriculumInputs` for ``question`` using ORM queries only.

    * ``data_flags`` values are **True** when the corresponding step returned no usable rows.
    * ``canonical_role`` is the matched ``canonical_roles.role_id`` (``None`` if unmatched).
    * ``period`` encodes the temporal slice used (enum + week anchors when present).
    """
    flags = _empty_data_flags()
    phrase = _extract_role_phrase(question, role_names)
    try:
        role_id, label, matched = _match_canonical_role(session, phrase)
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_path_role_resolution_error", error_type=type(exc).__name__)
        return CurriculumInputs(
            canonical_role=None,
            top_skills=[],
            rising_skills=[],
            co_occurring=[],
            top_employers=[],
            period="unresolved",
            region=_REGION_LABEL,
            role_matched=False,
            data_flags={**flags, "canonical_role": True},
        )

    flags["canonical_role"] = not matched
    if not matched or not role_id:
        return CurriculumInputs(
            canonical_role=None,
            top_skills=[],
            rising_skills=[],
            co_occurring=[],
            top_employers=[],
            period="unresolved",
            region=_REGION_LABEL,
            role_matched=False,
            data_flags=flags,
        )

    week_start = _latest_skill_demand_week(session)
    vel_week = _latest_velocity_week(session) or week_start
    temporal = _pick_temporal_period(session)

    period = f"temporal_period={temporal or 'any'}; demand_week={week_start!s}; velocity_week={vel_week!s}"

    top_skills: list[dict[str, Any]] = []
    rising: list[dict[str, Any]] = []
    co: list[dict[str, Any]] = []
    emps: list[dict[str, Any]] = []

    try:
        if week_start is not None:
            top_skills, _n = _query_top_skills(session, role_id, week_start, temporal)
        else:
            top_skills, _n = [], 0
        flags["top_skills"] = len(top_skills) == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_path_top_skills_error", error_type=type(exc).__name__)
        flags["top_skills"] = True

    try:
        if vel_week is not None and week_start is not None:
            rising = _query_rising_skills(session, role_id, vel_week, week_start, temporal)
        flags["rising_skills"] = len(rising) == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_path_rising_skills_error", error_type=type(exc).__name__)
        flags["rising_skills"] = True

    try:
        co = _query_co_occurring(session, role_id, temporal)
        flags["co_occurring"] = len(co) == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_path_co_occurring_error", error_type=type(exc).__name__)
        flags["co_occurring"] = True

    try:
        emps = _query_top_employers(session, role_id, temporal)
        flags["top_employers"] = len(emps) == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("curriculum_path_top_employers_error", error_type=type(exc).__name__)
        flags["top_employers"] = True

    return CurriculumInputs(
        canonical_role=role_id,
        top_skills=top_skills,
        rising_skills=rising,
        co_occurring=co,
        top_employers=emps,
        period=period,
        region=_REGION_LABEL,
        role_matched=True,
        data_flags=flags,
        canonical_role_label=label,
    )


__all__ = [
    "CurriculumInputs",
    "build_curriculum_inputs",
]
