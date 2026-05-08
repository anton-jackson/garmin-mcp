"""Daily wellness summaries: resting HR, HRV, and weight."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..format import normalize
from ..garmin import NeedsMFA, get_client


def _safe(fn):
    try:
        return fn()
    except NeedsMFA:
        return {"needs_mfa": True}


def _hrv_summary(payload):
    if not isinstance(payload, dict):
        return None
    return normalize(payload.get("hrvSummary"))


def _weight_summary(payload):
    if not isinstance(payload, dict):
        return None
    avg = payload.get("totalAverage")
    if not isinstance(avg, dict) or not avg:
        return None
    return normalize(avg)


def register(mcp):
    @mcp.tool()
    def get_daily_metrics(start: str, end: str | None = None) -> dict[str, Any]:
        """Resting HR, HRV, and weight summaries for a day or inclusive date range (yyyy-mm-dd). Omit `end` for a single day."""
        def go():
            client = get_client()
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end) if end else d0
            days = []
            cur = d0
            while cur <= d1:
                ds = cur.isoformat()
                days.append({
                    "date": ds,
                    "rhr": normalize(client.get_rhr_day(ds)),
                    "hrv": _hrv_summary(client.get_hrv_data(ds)),
                    "weight": _weight_summary(client.get_body_composition(ds)),
                })
                cur += timedelta(days=1)
            return {"days": days}
        return _safe(go)
