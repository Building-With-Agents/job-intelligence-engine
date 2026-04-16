"""Parse ``cohort_gap_cache.gap_data`` JSON into surplus / deficit rows (tolerant of shapes)."""

from __future__ import annotations

from typing import Any


def _num(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def rows_from_gap_data(gap_data: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Return rows with keys: skill_name, cohort_pct, market_pct, gap (cohort - market).

    Larger positive gap => cohort has more supply than market expects (surplus).
    Larger negative gap => cohort short vs market (deficit).
    """
    if gap_data is None:
        return [], "No gap data in this row."
    if isinstance(gap_data, str):
        return [], "gap_data is not structured JSON."

    rows: list[dict[str, Any]] = []

    if isinstance(gap_data, list):
        for item in gap_data:
            if not isinstance(item, dict):
                continue
            name = (
                item.get("skill_name")
                or item.get("skill")
                or item.get("label")
                or item.get("name")
                or ""
            )
            c = _num(item.get("cohort_pct") or item.get("cohort_share") or item.get("cohort"))
            m = _num(item.get("market_pct") or item.get("market_share") or item.get("market"))
            g = _num(item.get("gap") or item.get("delta"))
            if g is None and c is not None and m is not None:
                g = c - m
            if name and g is not None:
                rows.append(
                    {
                        "skill_name": str(name),
                        "cohort_pct": c,
                        "market_pct": m,
                        "gap": g,
                    }
                )
        return rows, None

    if isinstance(gap_data, dict):
        if isinstance(gap_data.get("skills"), list):
            return rows_from_gap_data(gap_data["skills"])
        for key in ("surplus", "deficit", "gaps", "skill_gaps"):
            block = gap_data.get(key)
            if isinstance(block, list):
                sub, _ = rows_from_gap_data(block)
                rows.extend(sub)
        if rows:
            return rows, None
        # flat dict skill -> numbers
        for k, v in gap_data.items():
            if k in ("cohort_key", "computed_at", "meta"):
                continue
            if isinstance(v, dict):
                c = _num(v.get("cohort_pct") or v.get("cohort_share"))
                m = _num(v.get("market_pct") or v.get("market_share"))
                g = _num(v.get("gap") or v.get("delta"))
                if g is None and c is not None and m is not None:
                    g = c - m
                if g is not None:
                    rows.append(
                        {
                            "skill_name": str(k),
                            "cohort_pct": c,
                            "market_pct": m,
                            "gap": g,
                        }
                    )
        if rows:
            return rows, None

    return [], "Could not parse gap_data — expected a list of skill objects or a known object shape."


def split_surplus_deficit(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    surplus = [r for r in rows if (r.get("gap") or 0) > 0]
    deficit = [r for r in rows if (r.get("gap") or 0) < 0]
    surplus.sort(key=lambda r: abs(float(r.get("gap") or 0)), reverse=True)
    deficit.sort(key=lambda r: abs(float(r.get("gap") or 0)), reverse=True)
    return surplus, deficit
