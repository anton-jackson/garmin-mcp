"""HRV MCP tools."""
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


def _summary(payload):
    if not isinstance(payload, dict):
        return None
    return normalize(payload.get("hrvSummary"))


def register(mcp):
    @mcp.tool()
    def get_hrv(date_: str) -> dict[str, Any]:
        """Nightly HRV summary for a single date (yyyy-mm-dd)."""
        return _safe(lambda: _summary(get_client().get_hrv_data(date_)))

    @mcp.tool()
    def get_hrv_range(start: str, end: str) -> dict[str, Any]:
        """HRV summary across an inclusive date range (yyyy-mm-dd)."""
        def go():
            client = get_client()
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end)
            days = []
            cur = d0
            while cur <= d1:
                days.append({"date": cur.isoformat(), "hrv": _summary(client.get_hrv_data(cur.isoformat()))})
                cur += timedelta(days=1)
            return {"days": days}
        return _safe(go)
