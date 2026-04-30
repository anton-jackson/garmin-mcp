# garmin-mcp

A single-user MCP server that gives an LLM (Claude Desktop, etc.) direct access to your Garmin Connect data: activities (with full .FIT detail), sleep, and HRV.

Hosted on Google Cloud Run. Auth via a static bearer token; Garmin credentials and the cached session token live in Secret Manager and Cloud Storage.

## Tools

- `list_activities(start_date?, end_date?, limit=20, activity_type?)`
- `get_activity(activity_id, include=["summary"|"laps"|"records"|"records_downsampled"], every=10)`
- `get_activity_fields(activity_id)` — schema introspection without data
- `get_sleep(date)` / `get_sleep_range(start, end)`
- `get_hrv(date)` / `get_hrv_range(start, end)`
- `submit_mfa(code)` — only when a prior call returned `needs_mfa: true`

## Deploy

See `deploy/deploy.sh`. One-time setup creates Secret Manager entries and a GCS bucket for the cached Garmin session token.

## Client config (Claude Desktop)

```json
{
  "mcpServers": {
    "garmin": {
      "url": "https://<service>-<hash>.run.app/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```
