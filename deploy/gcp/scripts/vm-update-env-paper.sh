#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-/opt/algo-trading-hub/deploy/gcp/.env}"

set_kv() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "${ENV}"; then
    sed -i "s/^${k}=.*/${k}=${v}/" "${ENV}"
  else
    echo "${k}=${v}" >> "${ENV}"
  fi
}

set_kv ENGINE_AUTOSTART true
set_kv DAILY_LOSS_KILL_PCT 0
set_kv HWM_DRAWDOWN_KILL_PCT 0
set_kv MAX_CONSECUTIVE_LOSSES 0
set_kv DAILY_LOSS_KILL_USD 0

grep -E '^(ENGINE_AUTOSTART|STRATEGY|DAILY_LOSS|HWM|MAX_CONSECUTIVE)' "${ENV}"
