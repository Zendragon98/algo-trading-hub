# Runtime Reference

This document collects operational reference material for running, testing, and
reviewing the backend.

## Environment Files

Backend runtime configuration is read from `backend/.env`, copied from
`backend/.env.example`.

Safe local defaults:

```dotenv
TRADING_MODE=paper
BINANCE_TESTNET=true
ENGINE_AUTOSTART=false
```

Binance Demo/Testnet keys are required before starting the engine against
account/order endpoints:

```dotenv
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

Local frontend development does not require a root `.env`; Vite proxies `/api`
and `/ws` to `127.0.0.1:8000`.

## Startup Modes

| Command | Effect |
|---|---|
| `python main.py` | Start API with engine stopped by default |
| `python main.py --no-engine` | API-only; engine never starts |
| `python main.py --engine` | Start API and engine immediately |
| `.\run.bat` | Windows backend launcher |
| `..\run-local.ps1` | Repo-root launcher for backend + frontend |

## Health and Readiness

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` is process liveness. `/ready` is trading readiness and is false while
the engine is intentionally stopped or paused.

## Configuration Reference

Settings live in `common/config/settings.py` and are grouped under
`common/config/sections/`.

| Section | Examples |
|---|---|
| `venue.py` | Binance/IBKR host, keys, REST/WS pacing |
| `engine_boot.py` | `STRATEGY`, `ENGINE_AUTOSTART`, universe bootstrap |
| `execution_core.py` | VWAP, slicing, urgency, flatten settings |
| `risk.py` | drawdown, exposure, breaker, reconcile, API token |
| `pairs.py`, `sma.py`, `blend.py`, `flow.py` | Strategy settings |
| `mm2.py`, `mm_institutional.py`, `mm_legacy.py` | Market-making settings |
| `api_persist.py` | CORS, logs, persistence, run archives |

Strategy aliases are normalized in `common/config/aliases.py`.

## REST and WebSocket Surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Process liveness |
| `GET` | `/ready` | Trading readiness |
| `GET` | `/api/state` | Full dashboard snapshot |
| `GET` | `/api/status` | Engine status |
| `GET` | `/api/positions` | Current positions |
| `GET` | `/api/orders` | Working orders |
| `GET` | `/api/execution` | Execution-quality reports |
| `GET` | `/api/logs` | Recent backend logs |
| `GET/PATCH` | `/api/settings` | Runtime settings |
| `POST` | `/api/control/start` | Start/resume engine |
| `POST` | `/api/control/pause` | Pause strategy evaluation |
| `POST` | `/api/control/stop` | Stop engine |
| `POST` | `/api/control/flatten` | Flatten venue positions |
| `POST` | `/api/control/kill` | Dashboard E-Stop; API stays up |
| `POST` | `/api/control/shutdown` | Process shutdown |
| `POST` | `/api/backtest/run` | Enqueue backtest |
| `GET` | `/api/backtest/runs` | Saved backtest results |
| `WS` | `/ws` | WebSocket event stream |

WebSocket events include `tick`, `fill`, `order`, `parent`, `execution`,
`position`, `equity`, `log`, `status`, and `breaker`.

## Run Archive

Each backend session creates a run directory under:

```text
backend/data/runs/<run-id>/
```

Typical files:

| File | Purpose |
|---|---|
| `manifest.json` | Run metadata |
| `app.log` | Human-readable logs |
| `fills.jsonl` | Venue fills |
| `orders.jsonl` | Child order updates |
| `parents.jsonl` | Parent-order progress |
| `executions.jsonl` | Completed execution reports |
| `positions.jsonl` | Position snapshots |
| `equity.jsonl` | Equity curve |
| `breakers.jsonl` | Breaker trips/clears |
| `status.jsonl` | Status and latency events |
| `events.wal.jsonl` | Optional WAL journal |

The run archive is the main evidence source for post-run review.

## Backtesting Data

Captured or downloaded 1m klines live under `backend/data/klines/`. The
`backend/data/` directory is gitignored, so a fresh clone may need to download
or capture klines before running offline backtests.

No-key smoke test:

```powershell
python -c "from common.config import Settings; from analytics.backtest.runner import run_backtest; r = run_backtest(Settings(strategy='sma'), dataset='library'); print({'run_id': r.run_id, 'strategy': r.strategy, 'bars': r.bar_count, 'return_pct': round(r.metrics.total_return_pct, 4), 'trades': r.metrics.trade_count})"
```

Download a larger dataset:

```powershell
python -m analytics.data_loader --symbols BTCUSDT --interval 1m --days 30
```

Local sample data is only suitable for setup smoke tests unless you deliberately
download or capture a report-grade sample period.

## Testing

Run all backend tests:

```powershell
python -m pytest -q
```

Useful focused tests:

```powershell
python -m pytest tests/test_main_boot.py tests/test_universe_bootstrap.py -q
python -m pytest tests/test_breaker_strategy_scope.py tests/test_config_aliases.py -q
python -m pytest tests/test_backtest_runner.py tests/test_backtest_job_api.py -q
python -m pytest tests/test_gateway_factory.py -q
```

## Helper Scripts

| Script | Purpose |
|---|---|
| `run.bat` | Windows backend launcher |
| `scripts/monitor_health.py` | Local health/log summary for a running backend |
| `scripts/test_strategies_live.py` | Paper/testnet strategy soak helper; localhost by default |
| `check_fees.py` | Binance fee audit helper |

`check_fees.py` is a standalone helper and not part of the engine hot path.

## Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| Dashboard shows `0` equity on boot | Engine is stopped; press Start to seed balances |
| `/health` ok but `/ready` false | Engine stopped/paused or market/user-data freshness not ready |
| Binance balance missing | Wrong key type/account, no Futures balance, or engine not started |
| WebSocket reconnect messages | Usually browser/backend reconnects; state rehydrates via `/api/state` |
| `-1021` Binance error | Clock skew; sync host clock |
| `reduce_only rejected` during flatten | Venue may already be flat; check reconcile/flatten logs |
| Backtest has few bars | Checked-in kline sample is intentionally small |

For production operations, see [`../../docs/OPERATIONS.md`](../../docs/OPERATIONS.md).
