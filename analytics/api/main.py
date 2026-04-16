"""CLI entry: `python -m analytics.api.main` — runs the FastAPI app with uvicorn."""

from __future__ import annotations


def main() -> None:
    import uvicorn

    uvicorn.run("analytics.api.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
