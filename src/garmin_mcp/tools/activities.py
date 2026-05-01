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
            return {"activities": normalize(items[:limit])}
        return _safe(go)

    @mcp.tool()
    def get_activity(
        activity_id: int,
        include: list[str] | None = None,
        every: int = 10,
    ) -> dict[str, Any]:
        """Fetch activity detail. include any of: summary, laps, records, records_downsampled, training_status."""
        include = include or ["summary"]
        def go():
            out: dict[str, Any] = {}
            client = get_client()
            # Fetch summary when explicitly requested or needed to resolve the activity date
            summary_data = None
            if "summary" in include or "training_status" in include:
                summary_data = normalize(client.get_activity(activity_id))
            if "summary" in include:
                out["summary"] = summary_data
            if "laps" in include:
                out["laps"] = normalize(parse_laps(activity_id))
            if "records" in include:
                out["records"] = normalize(parse_records(activity_id, every=1))
            if "records_downsampled" in include:
                out["records"] = normalize(parse_records(activity_id, every=every))
            if "training_status" in include and summary_data:
                start = summary_data.get("startTimeLocal") or summary_data.get("startTimeGMT", "")
                activity_date = start[:10] if start else None
                if activity_date:
                    out["training_status"] = normalize(client.get_training_status(activity_date))
            return out
        return _safe(go)

    @mcp.tool()
    def get_activity_fields(activity_id: int) -> dict[str, Any]:
        """Return the .FIT message/field schema for an activity, without the data."""
        return _safe(lambda: normalize(parse_schema(activity_id)))

    @mcp.tool()
    def submit_mfa(code: str) -> dict[str, Any]:
        """Provide an MFA code if a prior call returned needs_mfa."""
        return {"ok": _submit_mfa(code)}
