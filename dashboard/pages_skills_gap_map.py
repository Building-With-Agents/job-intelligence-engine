"""Skills gap map — cohort vs market from ``dbo.cohort_gap_cache``."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.gap_data_parse import rows_from_gap_data, split_surplus_deficit
from dashboard.relation_safe import read_sql_relation_safe


@st.cache_data(ttl=120, show_spinner=False)
def _load_cohort_gap_cache() -> tuple[pd.DataFrame, str | None]:
    from dashboard.readonly_engine import get_dashboard_engine

    q = """
        SELECT id, cohort_key, computed_at, gap_data
        FROM dbo.cohort_gap_cache
        ORDER BY computed_at DESC NULLS LAST, id DESC
    """
    return read_sql_relation_safe(
        q,
        get_dashboard_engine(),
        user_hint="`dbo.cohort_gap_cache` is missing. Run migrations (``common.data_store.migrations``) "
        "or wait for the Analytics agent to populate gap analysis.",
    )


def render_skills_gap_map() -> None:
    st.title("Skills Gap Map")
    st.caption("Compare cohort skill mix to market demand using cached gap analysis.")

    try:
        df, hint = _load_cohort_gap_cache()
    except Exception:
        st.error("Could not load cohort gap data. Check the database connection.")
        return

    if hint:
        st.warning(hint)
    if df.empty:
        st.info(
            "No rows in **cohort_gap_cache** yet. When the analytics pipeline writes gap analysis "
            "for cohorts, ranked surplus and deficit skills will appear here."
        )
        return

    keys = sorted(df["cohort_key"].dropna().astype(str).unique().tolist())
    pick = st.selectbox("Cohort", keys) if keys else None
    if pick:
        sub = df[df["cohort_key"].astype(str) == pick]
    else:
        sub = df

    row0 = sub.iloc[0]
    gap_data = row0.get("gap_data")
    if hasattr(gap_data, "item"):
        gap_data = gap_data.item()

    rows, parse_hint = rows_from_gap_data(gap_data)
    if parse_hint and not rows:
        st.warning(parse_hint)
        st.json(gap_data if gap_data is not None else {})
        return

    if not rows:
        st.info(
            "Gap data exists but no skill-level entries were found. "
            "Expected JSON lists or objects with cohort vs market shares (see architecture docs)."
        )
        if gap_data is not None:
            with st.expander("Raw gap_data"):
                st.json(gap_data if isinstance(gap_data, (dict, list)) else str(gap_data))
        return

    ranked = sorted(rows, key=lambda r: abs(float(r.get("gap") or 0)), reverse=True)
    surplus, deficit = split_surplus_deficit(ranked)

    st.metric("Skills analyzed", len(ranked))
    if row0.get("computed_at") is not None:
        st.caption(f"Computed at: **{row0.get('computed_at')}**  ·  cohort: **{row0.get('cohort_key')}**")

    st.subheader("Ranked by gap size (largest mismatch first)")
    show = pd.DataFrame(ranked)
    st.dataframe(show, width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Surplus** (cohort share > market)")
        if surplus:
            st.bar_chart(
                pd.DataFrame(surplus).set_index("skill_name")["gap"].head(25),
                horizontal=True,
            )
        else:
            st.info("No surplus skills detected with the current gap sign convention.")
    with c2:
        st.markdown("**Deficit** (cohort share < market)")
        if deficit:
            st.bar_chart(
                pd.DataFrame(deficit).set_index("skill_name")["gap"].head(25),
                horizontal=True,
            )
        else:
            st.info("No deficit skills detected with the current gap sign convention.")
