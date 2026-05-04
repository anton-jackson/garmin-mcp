"""Activity-related MCP tools."""
from __future__ import annotations

from typing import Any

from ..fit import parse_laps, parse_records, parse_schema
from ..format import normalize
from ..garmin import NeedsMFA, get_client, submit_mfa as _submit_mfa


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except NeedsMFA:
        return {"needs_mfa": True}


def _active_calories(obj: Any) -> Any:
    """Replace `calories` (total) with `activeCalories` (calories - bmrCalories) anywhere both fields appear together."""
    if isinstance(obj, dict):
        out = {k: _active_calories(v) for k, v in obj.items()}
        if "calories" in out and "bmrCalories" in out:
            total = out.pop("calories") or 0
            bmr = out.pop("bmrCalories") or 0
            out["activeCalories"] = total - bmr
        return out
    if isinstance(obj, list):
        return [_active_calories(v) for v in obj]
    return obj


def register(mcp):
    @mcp.tool()
    def list_activities(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        activity_type: str | None = None,
    ) -> dict[str, Any]:
        """List Garmin activities. Dates are ISO yyyy-mm-dd. activity_type e.g. 'running', 'cycling'."""
        def go():
            client = get_client()
            if start_date and end_date:
                items = client.get_activities_by_date(start_date, end_date, activity_type or "")
            else:
                items = client.get_activities(0, limit)
            return {"activities": _active_calories(normalize(items))}
        return _safe(go)

    @mcp.tool()
    def get_activity(
        activity_id: int,
        include: list[str] | None = None,
        every: int = 10,
    ) -> dict[str, Any]:
        """Fetch activity detail. include any of: summary, laps, records, records_downsampled."""
        include = include or ["summary"]
        def go():
            out: dict[str, Any] = {}
            client = get_client()
            if "summary" in include:
                out["summary"] = normalize(client.get_activity(activity_id))
            if "laps" in include:
                out["laps"] = normalize(parse_laps(activity_id))
            if "records" in include:
                out["records"] = normalize(parse_records(activity_id, every=1))
            if "records_downsampled" in include:
                out["records"] = normalize(parse_records(activity_id, every=every))
            return _active_calories(out)
        return _safe(go)

    @mcp.tool()
    def get_activity_fields(activity_id: int) -> dict[str, Any]:
        """Return the .FIT message/field schema for an activity, without the data."""
        return _safe(lambda: normalize(parse_schema(activity_id)))

    @mcp.tool()
    def get_activity_fueling(activity_id: int) -> dict[str, Any]:
        """Fueling inputs for an activity: active kcal, duration, HR, and time in each HR zone.

        Returns the minimal data needed to estimate carb vs. fat fuel split agent-side.
        Carb/fat kcal and gram conversions are intentionally not computed here — pair
        zone time with an athlete profile in the calling skill.
        """
        def go():
            client = get_client()
            summary = client.get_activity(activity_id) or {}
            zones = client.get_activity_hr_in_timezones(activity_id)

            total = summary.get("calories") or 0
            bmr = summary.get("bmrCalories") or 0
            return normalize({
                "activityType": (summary.get("activityType") or {}).get("typeKey"),
                "startTimeLocal": summary.get("startTimeLocal"),
                "durationSec": summary.get("duration"),
                "distanceM": summary.get("distance"),
                "activeCalories": total - bmr,
                "avgHr": summary.get("averageHR"),
                "maxHr": summary.get("maxHR"),
                "hrTimeInZones": zones,
            })
        return _safe(go)

    @mcp.tool()
    def estimate_activity_fueling(
        activity_id: int,
        zone_carb_fractions: list[float],
    ) -> dict[str, Any]:
        """Estimate carb/fat fuel use for an activity using a caller-provided RER table.

        Args:
            activity_id: Garmin activity ID.
            zone_carb_fractions: Carb-energy fraction per HR zone, ordered Z1..Z5.
                Each value 0..1; fat fraction is 1 - carb. Example for an
                average-trained athlete: [0.15, 0.35, 0.60, 0.85, 0.95].

        Time-in-zone is binned by Garmin using the athlete's Garmin Connect zone
        boundaries — `zoneLowBoundary` (HR) is included in `zoneBreakdown` so the
        caller can sanity-check those bounds before trusting the estimate.
        """
        if len(zone_carb_fractions) != 5:
            return {"error": "zone_carb_fractions must have 5 entries, ordered Z1..Z5"}
        if not all(0.0 <= f <= 1.0 for f in zone_carb_fractions):
            return {"error": "each zone_carb_fractions entry must be between 0.0 and 1.0"}

        def go():
            client = get_client()
            summary = client.get_activity(activity_id) or {}
            zones = client.get_activity_hr_in_timezones(activity_id) or []

            total_kcal = summary.get("calories") or 0
            bmr_kcal = summary.get("bmrCalories") or 0
            active_kcal = total_kcal - bmr_kcal
            duration_sec = summary.get("duration") or 0

            zone_secs_total = sum((z.get("secsInZone") or 0) for z in zones)
            if zone_secs_total <= 0:
                return {"error": "no time-in-zone data available for this activity"}

            carb_frac = 0.0
            breakdown = []
            for z in zones:
                zn = z.get("zoneNumber")
                secs = z.get("secsInZone") or 0
                if not isinstance(zn, int) or zn < 1 or zn > 5:
                    continue
                zone_carb_pct = zone_carb_fractions[zn - 1]
                time_frac = secs / zone_secs_total
                carb_frac += time_frac * zone_carb_pct
                breakdown.append({
                    "zone": zn,
                    "secs": secs,
                    "lowBoundaryHr": z.get("zoneLowBoundary"),
                    "carbFraction": zone_carb_pct,
                })

            carb_kcal = active_kcal * carb_frac
            fat_kcal = active_kcal * (1 - carb_frac)
            return normalize({
                "activityType": (summary.get("activityType") or {}).get("typeKey"),
                "startTimeLocal": summary.get("startTimeLocal"),
                "durationSec": duration_sec,
                "distanceM": summary.get("distance"),
                "avgHr": summary.get("averageHR"),
                "maxHr": summary.get("maxHR"),
                "activeCalories": active_kcal,
                "carbFraction": round(carb_frac, 4),
                "carbKcal": round(carb_kcal, 1),
                "carbG": round(carb_kcal / 4, 1),
                "fatKcal": round(fat_kcal, 1),
                "fatG": round(fat_kcal / 9, 1),
                "carbGPerHour": round((carb_kcal / 4) / (duration_sec / 3600), 1) if duration_sec else 0,
                "zoneBreakdown": breakdown,
            })
        return _safe(go)

    @mcp.tool()
    def submit_mfa(code: str) -> dict[str, Any]:
        """Provide an MFA code if a prior call returned needs_mfa."""
        return {"ok": _submit_mfa(code)}
