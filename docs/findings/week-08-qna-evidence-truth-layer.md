# Week 8 Findings — QnA evidence truth layer (#117)

## What I Tested

- Deterministic evidence construction from `QueryResultPayload` into `EvidenceBundle`
- Refusal handling for `router_error` and zero-row results
- Volume and confidence policy for sparse vs adequate samples
- Temporal coverage derivation, including partial-period responses and truncated result sets
- End-to-end `run_analytics_qna()` wiring after the evidence layer was implemented

## What I Found

- The missing Development 1 truth layer blocked the public QnA path because `run_analytics_qna()` called `build_evidence_bundle()` unconditionally.
- The evidence contract was stable enough to support a deterministic builder without changing `schemas.py`.
- Zero rows and low-volume results need to remain separate user states; otherwise the UI shows conflicting warnings for the same response.
- Salary-oriented intents need an explicit metric guard, because posting counts alone are not sufficient evidence for salary claims.

## Recommendation

- Keep `build_evidence_bundle()` as the deterministic policy gate before any synthesis call.
- Require future router work to keep returning explicit posting-count and period fields whenever possible so transparency stays accurate.
- Preserve the distinction between `NO_DATA` refusals and `SPARSE` caveated answers all the way through API and Streamlit consumers.

## Tradeoffs Acknowledged

- Volume support is best-effort when grouped rows are returned; without a dedicated distinct-postings field, the builder sums explicit row-level posting counts.
- Period coverage is derived heuristically from returned columns because `QueryResultPayload` does not yet expose a canonical period field.
- Structural refusal rules are intentionally conservative for salary intents; broader intent-specific validation can be added once routing labels are fully merged.

## Data / Evidence

- Added `analytics/tests/test_qna_evidence.py` for zero rows, sparse samples, low classifier confidence, truncation, router errors, and missing salary metrics.
- Updated `analytics/tests/test_qna_synthesis.py` so the pipeline path now runs without a skip and verifies the evidence-backed response shape.
- Verification run:
  `.venv/bin/python -m pytest analytics/tests/test_qna_synthesis.py analytics/tests/test_qna_evidence.py analytics/tests/test_query_engine_schemas.py -q`
- Lint run:
  `.venv/bin/python -m ruff check analytics/query_engine/evidence.py analytics/query_engine/fixtures.py analytics/query_engine/__init__.py analytics/query_engine/synthesis.py analytics/tests/test_qna_evidence.py analytics/tests/test_qna_synthesis.py`
