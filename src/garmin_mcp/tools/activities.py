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


def _detail_field(detail: dict, key: str) -> Any:
    """Read a field from an activity-detail response. Garmin nests calorie/duration/HR
    fields inside `summaryDTO` for single-activity fetches but returns them flat in
    list endpoints — check nested first, fall back to top-level."""
    sdto = detail.get("summaryDTO") if isinstance(detail, dict) else None
    if isinstance(sdto, dict) and key in sdto:
        return sdto[key]
    return detail.get(key) if isinstance(detail, dict) else None


def _detail_activity_type(detail: dict) -> str | None:
    atype = detail.get("activityTypeDTO") or detail.get("activityType") or {}
    return atype.get("typeKey") if isinstance(atype, dict) else None


_STRENGTH_TYPE_KEYS = {"strength_training", "fitness_equipment"}


def _is_strength(activity_type: str | None) -> bool:
    return bool(activity_type and activity_type in _STRENGTH_TYPE_KEYS)


def register(mcp):
    @mcp.tool()
    def list_activities(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        activity_type: str | None = None,
    ) -> Any:
        """List Garmin activities. Dates are ISO yyyy-mm-dd. activity_type e.g. 'running', 'cycling'.

        Returns an array of activity summaries with: activityId, activityName,
        activityType.typeKey, startTimeLocal, durationSec, activeCalories.
        """
        def go():
            client = get_client()
            if start_date and end_date:
                items = client.get_activities_by_date(start_date, end_date, activity_type or "")
            else:
                items = client.get_activities(0, limit)
            out = []
            for it in items or []:
                total = it.get("calories") or 0
                bmr = it.get("bmrCalories") or 0
                atype = it.get("activityType") or {}
                out.append({
                    "activityId": it.get("activityId"),
                    "activityName": it.get("activityName"),
                    "activityType": {"typeKey": atype.get("typeKey") if isinstance(atype, dict) else None},
                    "startTimeLocal": it.get("startTimeLocal"),
                    "durationMin": round((it.get("duration") or 0) / 60, 1),
                    "activeCalories": total - bmr,
                })
            return normalize(out)
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
        """Raw passthrough: active kcal, duration, HR, and time-in-zones for an activity.

        Debugging / inspection use only. Production callers should use
        `estimate_activity_fueling`.
        """
        def go():
            client = get_client()
            detail = client.get_activity(activity_id) or {}
            zones = client.get_activity_hr_in_timezones(activity_id)

            total = _detail_field(detail, "calories") or 0
            bmr = _detail_field(detail, "bmrCalories") or 0
            duration_sec = _detail_field(detail, "duration") or 0
            return normalize({
                "activityType": _detail_activity_type(detail),
                "startTimeLocal": detail.get("startTimeLocal"),
                "durationMin": round(duration_sec / 60, 1),
                "distanceM": _detail_field(detail, "distance"),
                "activeCalories": total - bmr,
                "avgHr": _detail_field(detail, "averageHR"),
                "maxHr": _detail_field(detail, "maxHR"),
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
            zone_carb_fractions: Carb-energy fraction per HR bin, length 6,
                ordered [below-Z1, Z1, Z2, Z3, Z4, Z5]. Each value 0..1; fat
                fraction is 1 - carb. The below-Z1 entry covers warmup/recovery
                time below the Z1 lower boundary (e.g. resting walks), which is
                heavily fat-dominant. Example fat-adapted athlete:
                [0.05, 0.10, 0.20, 0.45, 0.75, 0.90].

        Time weights use total activity duration as denominator so sub-Z1 time
        gets its own RER attribution rather than being redistributed across
        Z1..Z5. Time-in-zone for Z1..Z5 is binned by Garmin.

        Returns: durationMin, activeCalories, carbKcal, carbG, fatKcal, fatG.
        """
        if len(zone_carb_fractions) != 6:
            return {"error": "zone_carb_fractions must have 6 entries, ordered [below-Z1, Z1, Z2, Z3, Z4, Z5]"}
        if not all(0.0 <= f <= 1.0 for f in zone_carb_fractions):
            return {"error": "each zone_carb_fractions entry must be between 0.0 and 1.0"}

        def go():
            client = get_client()
            detail = client.get_activity(activity_id) or {}

            activity_type = _detail_activity_type(detail)
            total_kcal = _detail_field(detail, "calories") or 0
            bmr_kcal = _detail_field(detail, "bmrCalories") or 0
            active_kcal = total_kcal - bmr_kcal
            duration_sec = _detail_field(detail, "duration") or 0

            if _is_strength(activity_type):
                return normalize({
                    "activityType": activity_type,
                    "note": "Strength training: HR-zone carb/fat estimation does not apply. Calorie burn reported by Garmin.",
                    "durationMin": round(duration_sec / 60, 1),
                    "activeCalories": active_kcal,
                })

            zones = client.get_activity_hr_in_timezones(activity_id) or []

            zone_secs_total = sum((z.get("secsInZone") or 0) for z in zones)
            denom = max(duration_sec, zone_secs_total)
            if denom <= 0:
                return {"error": "no duration or time-in-zone data for this activity"}

            carb_frac = 0.0
            for z in zones:
                zn = z.get("zoneNumber")
                secs = z.get("secsInZone") or 0
                if not isinstance(zn, int) or zn < 1 or zn > 5:
                    continue
                carb_frac += (secs / denom) * zone_carb_fractions[zn]

            sub_z1_secs = max(0, denom - zone_secs_total)
            carb_frac += (sub_z1_secs / denom) * zone_carb_fractions[0]

            carb_kcal = active_kcal * carb_frac
            fat_kcal = active_kcal * (1 - carb_frac)

            return normalize({
                "durationMin": round(duration_sec / 60, 1),
                "activeCalories": active_kcal,
                "carbKcal": round(carb_kcal, 1),
                "carbG": round(carb_kcal / 4, 1),
                "fatKcal": round(fat_kcal, 1),
                "fatG": round(fat_kcal / 9, 1),
            })
        return _safe(go)

    @mcp.tool()
    def submit_mfa(code: str) -> dict[str, Any]:
        """Provide an MFA code if a prior call returned needs_mfa."""
        return {"ok": _submit_mfa(code)}
