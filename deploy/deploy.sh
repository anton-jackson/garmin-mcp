#!/usr/bin/env bash
# Deploy garmin-mcp to Cloud Run.
# One-time setup below sets up Secret Manager + GCS bucket. Re-runs of `gcloud run deploy`
# pick up code changes. Adjust PROJECT/REGION as needed.
set -euo pipefail

PROJECT="${PROJECT:-garmin-mcp}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-garmin-mcp}"
BUCKET="${BUCKET:-${PROJECT}-garmin-session}"
SA="${SA:-garmin-mcp-runtime}"
SA_EMAIL="${SA}@${PROJECT}.iam.gserviceaccount.com"

usage() {
  cat <<EOF
Usage: $0 <command>

Commands:
  setup    One-time: enable APIs, create bucket, service account, secrets
  secrets  Create/update Garmin email/password + MCP auth token secrets
  deploy   Build + deploy to Cloud Run (idempotent)
EOF
}

cmd="${1:-}"
case "$cmd" in
  setup)
    gcloud config set project "$PROJECT"
    gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
      secretmanager.googleapis.com storage.googleapis.com
    gsutil mb -l "$REGION" "gs://${BUCKET}" || true
    gcloud iam service-accounts create "$SA" --display-name="garmin-mcp runtime" || true
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:${SA_EMAIL}" --role="roles/secretmanager.secretAccessor"
    gsutil iam ch "serviceAccount:${SA_EMAIL}:objectAdmin" "gs://${BUCKET}"
    echo "Setup complete. Next: $0 secrets"
    ;;

  secrets)
    read -rp "Garmin email: " email
    read -rsp "Garmin password: " password; echo
    token="$(openssl rand -hex 32)"
    printf '%s' "$email"    | gcloud secrets create garmin-email    --data-file=- 2>/dev/null \
      || printf '%s' "$email"    | gcloud secrets versions add garmin-email    --data-file=-
    printf '%s' "$password" | gcloud secrets create garmin-password --data-file=- 2>/dev/null \
      || printf '%s' "$password" | gcloud secrets versions add garmin-password --data-file=-
    printf '%s' "$token"    | gcloud secrets create mcp-auth-token  --data-file=- 2>/dev/null \
      || printf '%s' "$token"    | gcloud secrets versions add mcp-auth-token  --data-file=-
    echo
    echo "MCP_AUTH_TOKEN (save this for your client config):"
    echo "$token"
    ;;

  deploy)
    gcloud run deploy "$SERVICE" \
      --source=. \
      --region="$REGION" \
      --service-account="$SA_EMAIL" \
      --allow-unauthenticated \
      --min-instances=0 \
      --max-instances=1 \
      --concurrency=10 \
      --memory=512Mi \
      --timeout=300 \
      --set-env-vars="GARMIN_SESSION_BUCKET=${BUCKET},GARMIN_SESSION_DIR=/tmp/garminconnect" \
      --set-secrets="GARMIN_EMAIL=garmin-email:latest,GARMIN_PASSWORD=garmin-password:latest,MCP_AUTH_TOKEN=mcp-auth-token:latest"
    ;;

  *)
    usage; exit 1 ;;
esac
