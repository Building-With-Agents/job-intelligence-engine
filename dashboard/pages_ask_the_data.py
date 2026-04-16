"""Streamlit — Ask the Data (Week 8, GitHub #117)."""

from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.types.query_request import QueryRequest
from dashboard.readonly_engine import check_dashboard_db_connection, get_dashboard_engine


def render_ask_the_data() -> None:
    st.title("Ask the Data")
    st.caption("Natural-language Q&A over guardrailed PostgreSQL (read-only).")

    if not check_dashboard_db_connection():
        st.warning(
            "This page requires PostgreSQL. Set `PYTHON_DATABASE_URL` or "
            "`PYTHON_DATABASE_URL_READONLY` and restart the app."
        )
        return

    default_q = (
        "Which canonical role labels have the most job postings? "
        "Group by role label and show counts."
    )
    query = st.text_area("Your question", value=default_q, height=100)
    if st.button("Run query", type="primary"):
        with st.spinner("Classifying intent, generating SQL, and synthesizing…"):
            try:
                engine = get_dashboard_engine()
                with Session(engine) as session:
                    resp = run_guardrailed_analytics_query(
                        QueryRequest(query=query.strip() or default_q),
                        session=session,
                    )
            except Exception as exc:
                st.error(f"Request failed: {type(exc).__name__}")
                st.caption(str(exc)[:500])
                return

        st.subheader("Answer")
        st.write(resp.answer_text or "(empty)")

        st.metric("Total LLM cost (USD)", f"{resp.total_cost_usd:.6f}")
        if resp.cost_breakdown_usd:
            st.json(resp.cost_breakdown_usd)

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Confidence**", resp.confidence)
            st.write("Low confidence flag", resp.confidence_flagged_low)
        with c2:
            st.write("**Volume**", resp.volume_flagged_low)
            if resp.volume_warning:
                st.info(resp.volume_warning)

        st.write("**Periods described**", resp.periods_described or "—")
        if resp.confidence_explanation:
            st.caption(resp.confidence_explanation)

        if resp.refused:
            st.warning(resp.refusal_message or "Refused")
            if resp.sql_execution_error_detail:
                with st.expander("Database error (debug)", expanded=False):
                    st.code(resp.sql_execution_error_detail, language="text")

        if resp.citations:
            with st.expander("Citations", expanded=True):
                st.json([c.model_dump(mode="json") for c in resp.citations])

        if resp.follow_up_questions:
            st.subheader("Suggested follow-ups")
            for fq in resp.follow_up_questions:
                st.write(f"- {fq}")
