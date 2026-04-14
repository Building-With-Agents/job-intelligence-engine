"""Regional demand — Borderplex subregions from ``dbo.geo_demand_weekly``."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.relation_safe import read_sql_relation_safe

_SUBREGION_LABELS = {
    "el_paso": "El Paso",
    "las_cruces": "Las Cruces",
    "ciudad_juarez": "Juárez",
    "regional": "Regional",
}


@st.cache_data(ttl=120, show_spinner=False)
def _load_geo_demand_weekly() -> tuple[pd.DataFrame, str | None]:
    from dashboard.readonly_engine import get_dashboard_engine

    q = """
        SELECT week_start, borderplex_subregion, posting_count
        FROM dbo.geo_demand_weekly
        ORDER BY week_start ASC, borderplex_subregion ASC
    """
    return read_sql_relation_safe(
        q,
        get_dashboard_engine(),
        user_hint="`dbo.geo_demand_weekly` is missing. Run migrations and the analytics geo-demand step.",
    )


def render_regional_heatmap() -> None:
    st.title("Regional Heatmap")
    st.caption("Demand intensity across Borderplex subregions over time.")

    try:
        df, hint = _load_geo_demand_weekly()
    except Exception:
        st.error("Could not load geographic demand data. Check the database connection.")
        return

    if hint:
        st.warning(hint)
    if df.empty:
        st.info(
            "No rows in **geo_demand_weekly** yet. After the analytics pipeline aggregates "
            "postings by ``borderplex_subregion`` and week, charts will appear here."
        )
        return

    df = df.copy()
    df["subregion_label"] = df["borderplex_subregion"].map(
        lambda x: _SUBREGION_LABELS.get(str(x).strip().lower(), str(x))
    )

    weeks = sorted(df["week_start"].dropna().unique().tolist())
    if not weeks:
        st.info("No valid week_start values in geo demand data.")
        return

    st.subheader("Demand intensity (posting count)")
    pivot = df.pivot_table(
        index="subregion_label",
        columns="week_start",
        values="posting_count",
        aggfunc="sum",
        fill_value=0,
    )
    try:
        fig = px.imshow(
            pivot.values,
            labels=dict(x="Week", y="Subregion", color="Postings"),
            x=[str(c) for c in pivot.columns],
            y=list(pivot.index),
            aspect="auto",
            color_continuous_scale="Blues",
        )
        fig.update_layout(margin=dict(l=8, r=8, t=32, b=8))
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.warning("Could not render heatmap; showing table instead.")
        st.dataframe(pivot, width="stretch")

    st.subheader("Temporal comparison — share of demand by period")
    agg = df.groupby(["week_start", "subregion_label"], as_index=False)["posting_count"].sum()
    totals = agg.groupby("week_start")["posting_count"].transform("sum")
    agg["share"] = 0.0
    mask = totals > 0
    agg.loc[mask, "share"] = agg.loc[mask, "posting_count"] / totals[mask]

    try:
        line_fig = px.line(
            agg,
            x="week_start",
            y="share",
            color="subregion_label",
            markers=True,
            labels={"share": "Share of weekly postings", "week_start": "Week"},
        )
        st.plotly_chart(line_fig, use_container_width=True)
    except Exception:
        st.dataframe(agg, width="stretch", hide_index=True)

    st.subheader("Per subregion coverage")
    expected = set(_SUBREGION_LABELS.values())
    present = set(df["subregion_label"].unique())
    missing = sorted(expected - present)
    if missing:
        st.info(
            "No demand rows yet for: **"
            + "**, **".join(missing)
            + "**. This is normal if enrichment has not classified postings into every subregion."
        )
    else:
        st.success("All four Borderplex buckets have at least one geo-demand row in this range.")
