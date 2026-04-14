"""Ask the Data — natural language Q&A via REST ``POST /analytics/query``."""

from __future__ import annotations

import hashlib
from typing import Any, Iterator

import streamlit as st

from dashboard.analytics_query_client import post_analytics_query


def _word_stream(text: str) -> Iterator[str]:
    if not text:
        yield ""
        return
    parts = text.split()
    for i, w in enumerate(parts):
        yield w + (" " if i < len(parts) - 1 else "")


def _confidence_warning(confidence: Any) -> str | None:
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return None
    if c < 0.5:
        return "Low confidence — treat this answer as exploratory."
    if c < 0.7:
        return "Moderate confidence — verify key figures against source data."
    return None


def render_ask_the_data() -> None:
    st.title("Ask the Data")
    st.caption(
        "Questions are sent to **POST /analytics/query** (REST only). "
        "Set ``DASHBOARD_ANALYTICS_QUERY_MOCK=0`` and ``ANALYTICS_QUERY_BASE_URL`` when the API is live."
    )

    if "ask_messages" not in st.session_state:
        st.session_state.ask_messages = []

    messages: list[dict[str, Any]] = st.session_state.ask_messages

    pending = st.session_state.pop("followup_query", None)
    user_prompt = st.chat_input("Ask a question about hiring, skills, or trends…")
    trigger = pending or user_prompt

    if trigger:
        messages.append({"role": "user", "content": trigger})
        result = post_analytics_query(trigger)
        if result.get("ok"):
            messages.append(
                {
                    "role": "assistant",
                    "content": str(result.get("answer") or ""),
                    "result": {k: v for k, v in result.items() if k != "ok"},
                }
            )
            st.session_state.stream_idx = len(messages) - 1
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "error": result.get("error", "Unknown error."),
                }
            )
        st.session_state.ask_messages = messages
        st.rerun()

    stream_idx = st.session_state.pop("stream_idx", None)

    for i, m in enumerate(messages):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(m.get("content") or "")
                continue

            if m.get("error"):
                st.error(m["error"])
                continue

            body = m.get("content") or ""
            if m["role"] == "assistant" and i == stream_idx and body:
                st.write_stream(_word_stream(body))
            elif body:
                st.markdown(body)

            res = m.get("result") or {}
            evidence = res.get("evidence")
            if evidence:
                st.markdown("**Evidence**")
                if isinstance(evidence, list):
                    for ev in evidence:
                        st.markdown(f"- {ev}")
                else:
                    st.markdown(str(evidence))

            cw = _confidence_warning(res.get("confidence"))
            if cw:
                st.warning(cw)

            sql_g = res.get("sql_generated")
            if sql_g:
                with st.expander("Generated SQL", expanded=False):
                    st.code(str(sql_g), language="sql")

            cost = res.get("cost_usd")
            if cost is not None:
                try:
                    st.caption(f"Estimated query cost: ${float(cost):.4f} USD")
                except (TypeError, ValueError):
                    st.caption(f"Estimated query cost: {cost}")

            follow = res.get("follow_up_questions")
            is_latest = i == len(messages) - 1 and m.get("role") == "assistant" and not m.get("error")
            if is_latest and isinstance(follow, list) and follow:
                st.markdown("**Follow-up**")
                n = len(follow)
                cols = st.columns(min(n, 4))
                for idx, q in enumerate(follow):
                    if not isinstance(q, str) or not q.strip():
                        continue
                    key = hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]
                    col = cols[idx % len(cols)]
                    with col:
                        if st.button(q[:80] + ("…" if len(q) > 80 else ""), key=f"chip_{i}_{key}"):
                            st.session_state.followup_query = q
                            st.rerun()

    if not messages:
        st.info("Ask a question below to get started. Mock responses are enabled until the API is ready.")
