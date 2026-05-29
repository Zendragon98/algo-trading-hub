#!/usr/bin/env bash
# Sync run archives to GCS (cron-friendly). Example crontab (daily 03:00 UTC):
#   0 3 * * * /opt/algo-trading-hub/deploy/gcp/scripts/sync-runs-to-gcs.sh
set -euo pipefail

BUCKET="${GCS_RUNS_BUCKET:-}"
SOURCE="${RUNS_DIR:-/var/lib/algo-trading/data/runs}"

if [[ -z "$BUCKET" ]]; then
  echo "Set GCS_RUNS_BUCKET=gs://your-bucket/algo-runs"
  exit 1
fi

if [[ ! -d "$SOURCE" ]]; then
  echo "Runs directory not found: $SOURCE"
  exit 1
fi

gsutil -m rsync -r -d "$SOURCE" "${BUCKET%/}/"
