#!/usr/bin/env python3
"""Run the Analytics FastAPI app (Week 8). Usage from job_intelligence/:

    PYTHONPATH=. python scripts/run_analytics_api.py

Then open http://127.0.0.1:8000/docs

Example curl (requires PYTHON_DATABASE_URL, LLM env, and a valid ``JIE_API_KEYS`` secret for #226):

    curl -sS -X POST http://127.0.0.1:8000/analytics/query \\
      -H "Content-Type: application/json" \\
      -H "X-Tenant-Id: borderplex" \\
      -H "X-User-Email: you@example.com" \\
      -H "X-Request-Id: $(uuidgen || python -c \"import uuid; print(uuid.uuid4())\")" \\
      -H "X-API-Key: <secret-from-JIE_API_KEYS>" \\
      -d '{"question":"What are the top skills by posting count?"}'

    curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis \\
      -H "Content-Type: application/json" \\
      -d '{"cohort_key":"demo-cohort","week_start":null}'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root (job_intelligence) is on sys.path when run as a script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env", override=False)

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("ANALYTICS_API_HOST", "127.0.0.1")
    port = int(os.getenv("ANALYTICS_API_PORT", "8000"))
    uvicorn.run(
        "analytics.api.app:app",
        host=host,
        port=port,
        reload=os.getenv("ANALYTICS_API_RELOAD", "1") == "1",
    )
