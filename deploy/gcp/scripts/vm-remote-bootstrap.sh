#!/usr/bin/env bash
# One-shot VM bootstrap: STRATEGY=all, major breakers off, engine running.
set -euo pipefail

ENV=/opt/algo-trading-hub/deploy/gcp/.env
COMPOSE_DIR=/opt/algo-trading-hub/deploy/gcp
API=http://127.0.0.1:8000

set_kv() {
  local k="$1" v="$2"
  if sudo grep -q "^${k}=" "$ENV"; then
    sudo sed -i "s|^${k}=.*|${k}=${v}|" "$ENV"
  else
    echo "${k}=${v}" | sudo tee -a "$ENV" >/dev/null
  fi
}

echo "== persist env =="
set_kv STRATEGY all
set_kv MULTI_STRATEGY_PARTITION true
set_kv ENGINE_AUTOSTART true
set_kv BREAKER_ENABLED '{"max_drawdown":false,"hwm_drawdown":false,"daily_loss":false,"consecutive_losses":false,"exec_quality":false,"reconcile_mismatch":false,"group_unwind_failed":false}'
sudo grep -E '^(STRATEGY|MULTI_STRATEGY_PARTITION|ENGINE_AUTOSTART|BREAKER_ENABLED)=' "$ENV"

echo "== recreate container =="
cd "$COMPOSE_DIR"
sudo docker compose pull
sudo docker compose up -d --force-recreate --remove-orphans
for i in $(seq 1 36); do
  if curl -sf "$API/health" >/dev/null; then
    echo "health ok after ${i}x5s"
    break
  fi
  sleep 5
done

API_TOKEN=$(sudo grep '^API_TOKEN=' "$ENV" | cut -d= -f2-)
auth=(-H "Authorization: Bearer ${API_TOKEN}" -H "Content-Type: application/json")

echo "== disable major breakers =="
curl -sf -X PATCH "${auth[@]}" "$API/api/control/breakers/enabled" -d '{
  "patch": {
    "max_drawdown": false,
    "hwm_drawdown": false,
    "daily_loss": false,
    "consecutive_losses": false,
    "exec_quality": false,
    "reconcile_mismatch": false,
    "group_unwind_failed": false
  }
}' | python3 -c "import sys,json; d=json.load(sys.stdin); majors=('max_drawdown','hwm_drawdown','daily_loss','consecutive_losses','exec_quality','reconcile_mismatch','group_unwind_failed'); print({k:d.get('enabled',{}).get(k) for k in majors})"

echo "== re-arm latched =="
curl -sf -X POST "${auth[@]}" "$API/api/control/breakers/rearm" -d '{}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('active:', [x['code'] for x in d.get('active',[])])"

echo "== start engine =="
curl -sf -X POST "${auth[@]}" "$API/api/control/start" -d '{}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('status:', d.get('status'))"

for i in $(seq 1 36); do
  if curl -sf "$API/ready" | grep -q '"ready":true'; then
    echo "ready"
    break
  fi
  sleep 5
done

echo "== final =="
curl -sf "$API/api/status" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('engine:', d.get('status'), 'strategy:', d.get('strategy'))"
curl -sf "${auth[@]}" "$API/api/control/breakers" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); majors=('max_drawdown','hwm_drawdown','daily_loss','consecutive_losses','exec_quality','reconcile_mismatch','group_unwind_failed'); print('majors enabled:', {k:d.get('enabled',{}).get(k) for k in majors}); print('active:', [x['code'] for x in d.get('active',[])])"
