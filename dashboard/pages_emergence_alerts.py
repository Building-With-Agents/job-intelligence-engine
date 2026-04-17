"""Emergence-style roles from ``dbo.disruption_fingerprints`` (Emergence pattern)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.relation_safe import read_sql_relation_safe


@st.cache_data(ttl=120, show_spinner=False)
def _load_emergence_fingerprints() -> tuple[pd.DataFrame, str | None]:
    from dashboard.readonly_engine import get_dashboard_engine

    q = """
        SELECT
            df.canonical_role_id,
            df.disruption_category,
            df.disruption_intensity,
            df.skill_velocity,
            df.period_comparison,
            df.ai_intensity_trend,
            df.trajectory,
            df.computed_at,
            cr.label AS role_label,
            cr.posting_count AS cluster_posting_count
        FROM dbo.disruption_fingerprints df
        LEFT JOIN dbo.canonical_roles cr ON cr.role_id = df.canonical_role_id
        ORDER BY df.computed_at DESC NULLS LAST, df.canonical_role_id ASC
    """
    return read_sql_relation_safe(
        q,
        get_dashboard_engine(),
        user_hint="`dbo.disruption_fingerprints` (or `dbo.canonical_roles`) is missing. "
        "Apply migrations or run the analytics pipeline.",
    )


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _ai_skill_density(skill_velocity: Any, disruption_intensity: Any) -> str:
    """Prefer explicit AI/ML density from skill_velocity; fall back to disruption_intensity."""
    if isinstance(skill_velocity, list):
        ai_hits = 0
        total = 0
        for item in skill_velocity:
            if not isinstance(item, dict):
                continue
            total += 1
            lab = str(
                item.get("skill")
                or item.get("skill_name")
                or item.get("label")
                or ""
            ).lower()
            if any(k in lab for k in ("ai", "ml", "llm", "genai", "machine learning")):
                ai_hits += 1
        if total > 0:
            return f"{100.0 * ai_hits / total:.1f}%"
    if disruption_intensity is not None:
        try:
            return _pct(disruption_intensity)
        except Exception:
            pass
    return "—"


def _posting_growth_rate(period_comparison: Any) -> str:
    if not isinstance(period_comparison, list):
        return "—"
    for block in period_comparison:
        if not isinstance(block, dict):
            continue
        m = block.get("metrics")
        if isinstance(m, dict):
            for key in ("posting_growth_rate", "growth_rate", "wow_change", "posting_growth"):
                if m.get(key) is not None:
                    try:
                        v = float(m[key])
                        return f"{v * 100:.1f}%" if abs(v) <= 2 else f"{v:.2f}"
                    except (TypeError, ValueError):
                        pass
        for key in ("posting_growth_rate", "growth_rate"):
            if block.get(key) is not None:
                try:
                    v = float(block[key])
                    return f"{v * 100:.1f}%"
                except (TypeError, ValueError):
                    pass
    return "—"


def _employer_count(period_comparison: Any) -> str:
    if not isinstance(period_comparison, list):
        return "—"
    for block in period_comparison:
        if isinstance(block, dict):
            m = block.get("metrics")
            if isinstance(m, dict) and m.get("employer_count") is not None:
                return str(int(m["employer_count"]))
            if block.get("employer_count") is not None:
                return str(int(block["employer_count"]))
    return "—"


def _has_pre_chatgpt_baseline(period_comparison: Any) -> bool:
    if not isinstance(period_comparison, list):
        return False
    for block in period_comparison:
        if not isinstance(block, dict):
            continue
        per = str(block.get("period") or "").lower()
        if "pre_chatgpt" in per or "pre-chatgpt" in per:
            m = block.get("metrics")
            if isinstance(m, dict):
                pc = m.get("posting_count")
                try:
                    if pc is not None and float(pc) > 0:
                        return True
                except (TypeError, ValueError):
                    pass
            try:
                if block.get("posting_count") is not None and float(block["posting_count"]) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def render_emergence_alerts() -> None:
    st.title("Emergence Alerts")
    st.caption(
        "Roles whose disruption fingerprint includes **Emergence** — new or rapidly shifting demand."
    )

    try:
        df, hint = _load_emergence_fingerprints()
    except Exception:
        st.error("Could not load disruption fingerprints. Check the database connection.")
        return

    if hint:
        st.warning(hint)
    if df.empty:
        st.info(
            "No disruption fingerprints found. Run the disruption refresh pipeline "
            "(``scripts/smoke/disruption_refresh.py``) after populating ``canonical_roles``."
        )
        return

    out_rows = []
    for _, r in df.iterrows():
        sv = r.get("skill_velocity")
        if hasattr(sv, "item"):
            sv = sv.item()
        if isinstance(sv, str):
            try:
                sv = json.loads(sv)
            except Exception:
                sv = None
        pc = r.get("period_comparison")
        if hasattr(pc, "item"):
            pc = pc.item()
        if isinstance(pc, str):
            try:
                pc = json.loads(pc)
            except Exception:
                pc = None

        cat = r.get("disruption_category")
        if hasattr(cat, "item"):
            cat = cat.item()
        if isinstance(cat, str):
            try:
                cat = json.loads(cat)
            except Exception:
                pass
        if isinstance(cat, list):
            cat_str = ", ".join(str(c) for c in cat) if cat else "—"
        else:
            cat_str = str(cat) if cat else "—"

        missing_baseline = not _has_pre_chatgpt_baseline(pc)
        out_rows.append(
            {
                "Role": r.get("role_label") or r.get("canonical_role_id") or "—",
                "Disruption category": cat_str,
                "AI skill density": _ai_skill_density(sv, r.get("disruption_intensity")),
                "Posting growth": _posting_growth_rate(pc),
                "Employer count": _employer_count(pc),
                "Trajectory": r.get("trajectory") or "—",
                "AI intensity trend": r.get("ai_intensity_trend") or "—",
                "No pre-ChatGPT baseline": "Yes — review" if missing_baseline else "No",
            }
        )

    show = pd.DataFrame(out_rows)
    st.dataframe(show, width="stretch", hide_index=True)

    flagged = [str(r["Role"]) for _, r in show.iterrows() if r.get("No pre-ChatGPT baseline") == "Yes — review"]
    if flagged:
        st.warning(
            "**No pre-ChatGPT baseline** — emerging signals without an anchored historical window: "
            + ", ".join(flagged[:20])
            + (" …" if len(flagged) > 20 else "")
        )
