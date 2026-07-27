"""Activity-related MCP tools."""
from __future__ import annotations

from typing import Any

from ..fit import parse_laps, parse_messages, parse_records, parse_schema
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
        """Fetch activity detail. include any of: summary, laps, records,
        records_downsampled, hr_zones, power_zones.

        Defaults to ['summary', 'laps']. Laps are manually triggered by the athlete,
        so when they're present they encode intentional segments (per-lap pace, HR,
        elevation gain) — always look at them for run structure, pacing, or split
        analysis. Summary alone won't show the shape of the activity.

        Add 'records_downsampled' (uses `every`, default 10) when you need the
        elevation profile, GPS track, or sub-lap HR/pace dynamics. Use 'records'
        (full resolution, one point per second) only when downsampled isn't enough.

        Also accepts "messages:<fit_message_name>" to dump a raw FIT message type
        verbatim (e.g. "messages:unknown_216" to inspect a message fitparse doesn't
        recognize by name, such as ClimbPro data) — add ":<every>" to downsample it,
        e.g. "messages:record:10".
        """
        include = include or ["summary", "laps"]
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
            if "hr_zones" in include:
                out["hr_zones"] = normalize(client.get_activity_hr_in_timezones(activity_id))
            if "power_zones" in include:
                out["power_zones"] = normalize(client.get_activity_power_in_timezones(activity_id))
            for item in include:
                if not item.startswith("messages:"):
                    continue
                parts = item.split(":", 2)
                message_type = parts[1]
                msg_every = int(parts[2]) if len(parts) > 2 else 1
                out.setdefault("messages", {})[message_type] = normalize(
                    parse_messages(activity_id, message_type, every=msg_every)
                )
            return _active_calories(out)
        return _safe(go)

    @mcp.tool()
    def get_activity_fields(activity_id: int) -> dict[str, Any]:
        """Return the .FIT message/field schema for an activity, without the data."""
        return _safe(lambda: normalize(parse_schema(activity_id)))

    @mcp.tool()
    def estimate_activity_macros_burned(
        activity_id: int,
        zone_carb_fractions: list[float] | None = None,
    ) -> dict[str, Any]:
        """Estimate carb/fat macros burned during an activity using a caller-provided RER table.

        Args:
            activity_id: Garmin activity ID.
            zone_carb_fractions: Optional. Carb-energy fraction per HR bin,
                length 6, ordered [below-Z1, Z1, Z2, Z3, Z4, Z5]. Each value
                0..1; fat fraction is 1 - carb. The below-Z1 entry covers
                warmup/recovery time below the Z1 lower boundary (e.g. resting
                walks), which is heavily fat-dominant. Example fat-adapted
                athlete: [0.05, 0.10, 0.20, 0.45, 0.75, 0.90].

                Omit this argument for interval workouts where HR lag biases
                time-in-zone — the tool then returns only durationMin and
                activeCalories, leaving the carb/fat split to the caller (who
                has the user's stated session structure).

        Time weights use total activity duration as denominator so sub-Z1 time
        gets its own RER attribution rather than being redistributed across
        Z1..Z5. Time-in-zone for Z1..Z5 is binned by Garmin.

        Returns: durationMin and activeCalories always. When
        zone_carb_fractions is provided and the activity is aerobic, also
        returns carbKcal, carbG, fatKcal, fatG. Strength activities and the
        interval path (no zone_carb_fractions) return only durationMin and
        activeCalories.
        """
        if zone_carb_fractions is not None:
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

            if zone_carb_fractions is None or _is_strength(activity_type):
                return normalize({
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
