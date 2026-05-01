"""Resting heart rate MCP tools."""
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


def register(mcp):
    @mcp.tool()
    def get_rhr(date_: str) -> dict[str, Any]:
        """Resting heart rate for a single date (yyyy-mm-dd)."""
        return _safe(lambda: normalize(get_client().get_rhr_day(date_)))

    @mcp.tool()
    def get_rhr_range(start: str, end: str) -> dict[str, Any]:
        """Resting heart rate across an inclusive date range (yyyy-mm-dd)."""
        def go():
            client = get_client()
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end)
            days = []
            cur = d0
            while cur <= d1:
                days.append({"date": cur.isoformat(), "rhr": normalize(client.get_rhr_day(cur.isoformat()))})
                cur += timedelta(days=1)
            return {"days": days}
        return _safe(go)
