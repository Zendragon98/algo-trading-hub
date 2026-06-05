#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-/opt/algo-trading-hub/deploy/gcp/.env}"

set_kv() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "${ENV}"; then
    sed -i "s|^${k}=.*|${k}=${v}|" "${ENV}"
  else
    echo "${k}=${v}" >> "${ENV}"
  fi
}

# Persist major breaker disable across container restarts (JSON map).
set_kv BREAKER_ENABLED '{"max_drawdown":false,"hwm_drawdown":false,"daily_loss":false,"consecutive_losses":false,"exec_quality":false,"reconcile_mismatch":false,"group_unwind_failed":false}'

grep '^BREAKER_ENABLED=' "${ENV}"
