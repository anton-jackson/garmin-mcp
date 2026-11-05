"""Daily wellness summaries: resting HR, HRV, weight, and calories burned."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from garminconnect import GarminConnectConnectionError

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


def _user_summary(client, ds):
    # get_user_summary raises (rather than returning empty) when Garmin has no
    # summary for the day, e.g. a future date. Don't let that sink the other metrics.
    try:
        return client.get_user_summary(ds)
    except GarminConnectConnectionError:
        return None


def _calorie_summary(payload):
    """Daily calorie burn as shown in the Garmin app: total = resting + active.

    `active` is Garmin's activeKilocalories, which already covers both all-day
    movement and recorded activities. `resting` is bmrKilocalories.
    """
    if not isinstance(payload, dict):
        return None
    out = {
        "total": payload.get("totalKilocalories"),
        "active": payload.get("activeKilocalories"),
        "resting": payload.get("bmrKilocalories"),
    }
    if all(v is None for v in out.values()):
        return None
    return out


def _date_range(start: str, end: str | None):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else d0
    cur = d0
    while cur <= d1:
        yield cur.isoformat()
        cur += timedelta(days=1)


def register(mcp):
    @mcp.tool()
    def get_daily_metrics(start: str, end: str | None = None) -> dict[str, Any]:
        """Resting HR, HRV, and weight summaries for a day or inclusive date range (yyyy-mm-dd). Omit `end` for a single day."""
        def go():
            client = get_client()
            days = []
            for ds in _date_range(start, end):
                days.append({
                    "date": ds,
                    "rhr": normalize(client.get_rhr_day(ds)),
                    "hrv": _hrv_summary(client.get_hrv_data(ds)),
                    "weight": _weight_summary(client.get_body_composition(ds)),
                })
            return {"days": days}
        return _safe(go)

    @mcp.tool()
    def get_daily_calories(start: str, end: str | None = None) -> dict[str, Any]:
        """Whole-day calories burned for a day or inclusive date range (yyyy-mm-dd). Omit `end` for a single day.

        Matches the Garmin app/watch daily total: total = resting + active, where
        active includes all-day movement plus recorded activities (don't add
        activity calories on top). For today the values are accrued so far, not a
        full-day projection. `calories` is null for days with no Garmin summary.
        """
        def go():
            client = get_client()
            days = [
                {"date": ds, "calories": _calorie_summary(_user_summary(client, ds))}
                for ds in _date_range(start, end)
            ]
            return {"days": days}
        return _safe(go)
