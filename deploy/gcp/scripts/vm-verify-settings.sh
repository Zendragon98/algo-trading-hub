#!/usr/bin/env bash
set -euo pipefail
ENV=/opt/algo-trading-hub/deploy/gcp/.env
API_TOKEN=$(sudo grep '^API_TOKEN=' "$ENV" | cut -d= -f2-)
curl -sf -H "Authorization: Bearer ${API_TOKEN}" http://127.0.0.1:8000/api/settings \
  | python3 -c "
import sys, json
s = json.load(sys.stdin)['settings']
print('strategy:', s.get('strategy'))
print('multi_strategy_partition:', s.get('multi_strategy_partition'))
be = s.get('breaker_enabled', {})
majors = ['max_drawdown','hwm_drawdown','daily_loss','consecutive_losses','exec_quality','reconcile_mismatch','group_unwind_failed']
print('majors_enabled:', {k: be.get(k) for k in majors})
"
curl -sf http://127.0.0.1:8000/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('status:', d.get('status'))"
curl -sf http://127.0.0.1:8000/ready
