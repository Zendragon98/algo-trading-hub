#!/usr/bin/env bash
# Patch VM .env with MM quote sizing, pull latest image, restart engine.
# Run on VM:  sudo bash /opt/algo-trading-hub/deploy/gcp/scripts/vm-update-mm-sizing.sh
set -euo pipefail

ENV="${1:-/opt/algo-trading-hub/deploy/gcp/.env}"
COMPOSE_DIR="${2:-/opt/algo-trading-hub/deploy/gcp}"

set_kv() {
  local k="$1" v="$2"
  if sudo grep -q "^${k}=" "$ENV"; then
    sudo sed -i "s|^${k}=.*|${k}=${v}|" "$ENV"
  else
    echo "${k}=${v}" | sudo tee -a "$ENV" >/dev/null
  fi
}

echo "== MM sizing env =="
set_kv MM_QUOTE_SIZE_PCT 0.02
set_kv MM2_RISK_PER_TRADE_PCT 0.02
set_kv MM2_MAX_INVENTORY_NOTIONAL 1500
set_kv MM2_MAX_INVENTORY_NOTIONAL_TOTAL 3000
set_kv MM2_QTY 0.005
sudo grep -E '^(MM_QUOTE_SIZE_PCT|MM2_RISK_PER_TRADE_PCT|MM2_MAX_INVENTORY|MM2_QTY)=' "$ENV"

echo "== pull + restart =="
cd "$COMPOSE_DIR"
sudo docker compose pull
sudo docker compose up -d --force-recreate --remove-orphans

for i in $(seq 1 48); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null; then
    echo "health ok after ${i}x5s"
    break
  fi
  sleep 5
done

API_TOKEN=$(sudo grep '^API_TOKEN=' "$ENV" | cut -d= -f2-)
curl -sf -H "Authorization: Bearer ${API_TOKEN}" http://127.0.0.1:8000/api/settings \
  | python3 -c "
import sys, json
s = json.load(sys.stdin)['settings']
print('mm_quote_size_pct:', s.get('mm_quote_size_pct'))
print('mm2_risk_per_trade_pct:', s.get('mm2_risk_per_trade_pct'))
print('mm2_max_inventory_notional:', s.get('mm2_max_inventory_notional'))
print('mm2_max_inventory_notional_total:', s.get('mm2_max_inventory_notional_total'))
"
