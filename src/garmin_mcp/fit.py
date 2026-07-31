"""Download and parse .FIT files for a Garmin activity."""
from __future__ import annotations

import io
import zipfile
from typing import Any

from fitparse import FitFile

from .garmin import get_client


def _download_fit_bytes(activity_id: int | str) -> bytes:
    client = get_client()
    raw = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
    # Garmin returns a zip containing the .FIT.
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        fit_name = next(n for n in zf.namelist() if n.lower().endswith(".fit"))
        return zf.read(fit_name)


def _record_to_dict(msg) -> dict[str, Any]:
    out = {}
    for f in msg:
        out[f.name] = f.value
    return out


def parse_schema(activity_id: int | str) -> dict[str, Any]:
    """Return message types + field names + units, no data."""
    fit = FitFile(_download_fit_bytes(activity_id))
    schema: dict[str, dict[str, dict]] = {}
    for msg in fit.get_messages():
        mtype = msg.name
        if mtype not in schema:
            schema[mtype] = {}
        for f in msg:
            if f.name not in schema[mtype]:
                schema[mtype][f.name] = {"units": f.units}
    return {"messages": schema}


def parse_records(activity_id: int | str, every: int = 1) -> list[dict[str, Any]]:
    fit = FitFile(_download_fit_bytes(activity_id))
    out = []
    for i, msg in enumerate(fit.get_messages("record")):
        if i % every != 0:
            continue
        out.append(_record_to_dict(msg))
    return out


def _derive_lap_pace(lap: dict[str, Any]) -> dict[str, Any]:
    """Fill in speed/pace when Garmin's FIT omits them from the lap summary.

    Some re-encoded Garmin downloads leave every speed field null on the lap
    message (avg_speed, max_speed, and even the enhanced_* variants) while still
    carrying total_distance and total_timer_time. Derive speed/pace from those so
    callers never have to compute it themselves.
    """
    has_speed = any(
        lap.get(k) is not None
        for k in ("avg_speed", "enhanced_avg_speed")
    )
    dist = lap.get("total_distance")
    time = lap.get("total_timer_time") or lap.get("total_elapsed_time")
    if has_speed or not dist or not time:
        return lap
    mps = dist / time
    lap["avg_speed_mps"] = round(mps, 3)
    lap["pace_min_per_km"] = round((1000 / mps) / 60, 3)
    lap["pace_min_per_mi"] = round((1609.34 / mps) / 60, 3)
    return lap


def _lap_elevation(lap: dict[str, Any]) -> dict[str, Any]:
    """Surface Garmin's own per-lap ascent/descent under a stable, always-present key.

    total_ascent/total_descent come off the device's barometric altimeter and are
    already spike-filtered by Garmin, so they're passed through untouched — never
    recomputed from the record stream, whose altitudes carry spikes the device
    already rejected. A lap with no altitude source at all (treadmill, indoor) omits
    the FIT fields entirely; emit null there so the key is still present and callers
    can tell "no altimeter data" from a genuinely flat 0.
    """
    lap["elevation_gain_m"] = lap.get("total_ascent")
    lap["elevation_loss_m"] = lap.get("total_descent")
    return lap


def parse_laps(activity_id: int | str) -> list[dict[str, Any]]:
    fit = FitFile(_download_fit_bytes(activity_id))
    return [_lap_elevation(_derive_lap_pace(_record_to_dict(m))) for m in fit.get_messages("lap")]


def parse_messages(activity_id: int | str, message_type: str, every: int = 1) -> list[dict[str, Any]]:
    """Dump any FIT message type, including ones fitparse can't name (e.g. "unknown_216")."""
    fit = FitFile(_download_fit_bytes(activity_id))
    out = []
    for i, msg in enumerate(fit.get_messages(message_type)):
        if i % every != 0:
            continue
        out.append(_record_to_dict(msg))
    return out
