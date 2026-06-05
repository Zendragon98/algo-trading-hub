#!/usr/bin/env bash
# Start engine (STRATEGY=all), disable major circuit breakers, re-arm latched breaches.
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/opt/algo-trading-hub/deploy/gcp}"
ENV_FILE="${COMPOSE_DIR}/.env"
API="http://127.0.0.1:8000"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing ${ENV_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source <(grep -E '^API_TOKEN=' "${ENV_FILE}" | sed 's/^/export /')

if [[ -z "${API_TOKEN:-}" ]]; then
  echo "API_TOKEN not set in ${ENV_FILE}" >&2
  exit 1
fi

auth=( -H "Authorization: Bearer ${API_TOKEN}" -H "Content-Type: application/json" )

echo "== breakers before =="
curl -sf "${auth[@]}" "${API}/api/control/breakers" | python3 -m json.tool | head -80 || true

echo "== disable major breakers =="
curl -sf -X PATCH "${auth[@]}" "${API}/api/control/breakers/enabled" -d '{
  "patch": {
    "max_drawdown": false,
    "hwm_drawdown": false,
    "daily_loss": false,
    "consecutive_losses": false,
    "exec_quality": false,
    "reconcile_mismatch": false,
    "group_unwind_failed": false
  }
}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('enabled majors off:', {k:v for k,v in d.get('enabled',{}).items() if k in ('max_drawdown','hwm_drawdown','daily_loss','consecutive_losses','exec_quality','reconcile_mismatch','group_unwind_failed')})"

echo "== re-arm any latched breakers =="
curl -sf -X POST "${auth[@]}" "${API}/api/control/breakers/rearm" -d '{}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('active after rearm:', [x['code'] for x in d.get('active',[])])"

echo "== start engine =="
curl -sf -X POST "${auth[@]}" "${API}/api/control/start" -d '{}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('status:', d.get('status'))"

echo "== wait for ready =="
for i in $(seq 1 36); do
  if curl -sf "${API}/ready" | grep -q '"ready":true'; then
    echo "ready after ${i}x5s"
    break
  fi
  sleep 5
done

echo "== final status =="
curl -sf "${API}/api/status" | python3 -m json.tool | head -40
curl -sf "${API}/ready" | python3 -m json.tool
