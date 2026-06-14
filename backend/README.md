# Algo Trading Backend

Python backend for the React console at the repository root. It owns the
trading engine, FastAPI control surface, WebSocket stream, Binance gateway,
analytics worker, risk controls, execution stack, and run archives.

The backend is intentionally the whole non-dashboard infrastructure layer. The
frontend observes and controls it, but the backend is the source of truth for
positions, orders, portfolio state, risk state, persistence, and venue access.

## Backend at a Glance

| Area | Path | Responsibility |
|---|---|---|
| Entrypoint | `main.py` | Load settings, create gateway, register strategies, start FastAPI/uvicorn |
| API | `api/` | REST routes, schemas, serializers, `/ws` event stream |
| Engine | `engine/` | Trading lifecycle, market data, strategy, risk, execution, portfolio |
| Gateways | `gateways/` | Venue abstraction; Binance implementation and IBKR skeleton |
| Common | `common/` | Settings, aliases, events, enums, shared types, logging |
| Analytics | `analytics/` | Backtests, calibration, reports, worker jobs |
| Runtime docs | `docs/` | Architecture diagrams and backend reference docs |
| Tests | `tests/` | Backend pytest suite; mocks live here |
| Data | `data/` | Runtime caches and run archives; mostly gitignored |

## Reading Path

| If you want to understand... | Read | Diagram anchor |
|---|---|---|
| Backend process, API, gateway, persistence | [`docs/backend-architecture.md`](docs/backend-architecture.md) | `architecture-system.mmd`, `architecture-lifecycle.mmd`, `architecture-events.mmd` |
| Market data, signals, strategies, analytics | [`docs/market-data-and-strategies.md`](docs/market-data-and-strategies.md) | `architecture-tick.mmd`, `architecture-strategies.mmd` |
| Risk, execution, flattening, positions | [`docs/risk-execution-and-portfolio.md`](docs/risk-execution-and-portfolio.md) | `architecture-execution.mmd`, `architecture-breakers.mmd`, `architecture-data-sync.mmd`, `architecture-control.mmd` |
| Config, API contract, run archive, tests | [`docs/runtime-reference.md`](docs/runtime-reference.md) | REST/WebSocket and run-archive references |
| Repository-wide docs and report alignment | [`../docs/README.md`](../docs/README.md) | QF635 report map |

Editable Mermaid sources live in [`docs/`](docs/). They are version-controlled
architecture evidence and should be updated when behaviour changes.

## Quick Start

From `backend/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python main.py
```

You can also use `.\run.bat` from `backend/`, or run both backend and frontend
from the repository root with:

```powershell
.\run-local.ps1
```

Default local review settings:

```dotenv
TRADING_MODE=paper
BINANCE_TESTNET=true
ENGINE_AUTOSTART=false
```

With `ENGINE_AUTOSTART=false`, the API and dashboard start while the engine is
stopped. Binance balances and positions are loaded only when the engine starts
and connects to the venue.

API:

```text
http://127.0.0.1:8000
```

Health checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

See [`docs/runtime-reference.md`](docs/runtime-reference.md) for API-only
startup, offline backtest smoke tests, run archives, and troubleshooting.

## Source Layout

```text
backend/
  main.py                    entrypoint: Engine + FastAPI
  run.bat                    Windows backend launcher
  requirements.txt           Python dependencies
  pyproject.toml             pytest + ruff config
  .env.example               backend environment template

  api/                       FastAPI routes, schemas, /ws
  analytics/                 backtests, calibration, reports, worker jobs
  calibration_defaults/      checked-in default calibration JSON
  common/                    settings, events, shared types
  docs/                      backend docs + architecture diagrams
  engine/                    trading system core
  gateways/                  venue adapters
  scripts/                   backend helper scripts
  tests/                     pytest suite
  data/                      runtime data and run archives
```

## Engine Subsystems

| Subsystem | Path | Notes |
|---|---|---|
| Core orchestration | `engine/core/` | Engine lifecycle, clock, reconciliation, connection monitor |
| Market data | `engine/market_data/` | L2 book, trade tape, feature store, data quality |
| Strategies | `engine/strategies/` | Pairs, SMA, blended, flow momentum, market making, signal netting |
| Risk | `engine/risk/` | Pre-trade, breakers, stops, exposure, venue sizing |
| Execution | `engine/execution/` | Algo wheel, slicer, VWAP, QuoteExecutor, quality guards |
| Orders | `engine/orders/` | Parent/child OMS state |
| Position | `engine/position/` | Position tracker, strategy ledger, venue PnL |
| Portfolio | `engine/portfolio/` | Cash, equity, portfolio snapshots |
| Performance | `engine/performance/` | KPIs, attribution, session costs, strategy hub |
| Persistence | `engine/persistence/` | Event recorder, WAL, market capture, run bootstrap |
| Observability | `engine/observability/` | Alerts and latency tracking |

## Trading Modes

| Mode | Use | Safety behaviour |
|---|---|---|
| `paper` | Default local/testnet mode | Sandbox/testnet endpoints allowed |
| `live` | Real-money mode | Fails fast if pointed at sandbox endpoints |

`TRADING_MODE` is venue-agnostic. For Binance live trading, also set
`BINANCE_TESTNET=false` and use mainnet REST/WebSocket hosts.

## Common Commands

Run backend:

```powershell
python main.py
```

Run API without starting the engine:

```powershell
python main.py --no-engine
```

Run with engine autostart:

```powershell
python main.py --engine
```

Run backend tests:

```powershell
python -m pytest -q
```

Run a no-key offline backtest smoke test:

```powershell
python -c "from common.config import Settings; from analytics.backtest.runner import run_backtest; r = run_backtest(Settings(strategy='sma'), dataset='library'); print({'run_id': r.run_id, 'strategy': r.strategy, 'bars': r.bar_count, 'return_pct': round(r.metrics.total_return_pct, 4), 'trades': r.metrics.trade_count})"
```

## Notes for Reviewers

- The backend does not depend on browser state. The dashboard reads snapshots
  and events from the backend.
- Binance API keys belong in `backend/.env`, never in source control.
- Mocks are confined to `backend/tests/`.
- Runtime outputs under `backend/data/` are not source code.
- Detailed strategy performance claims should come from final backtests or
  paper/live runs, not from the local smoke-test kline library.

## Contributor Notes

- Treat `backend/` as the import root. Use imports such as `from common...` and
  `from engine...`; avoid ad hoc relative import workarounds.
- Keep production mocks and fakes out of runtime paths. Test doubles belong in
  `backend/tests/`.
- API schema changes should be kept aligned with frontend types in
  `src/components/algo/types.ts`.
- Operator-facing behavior changes should update this README, the relevant
  backend docs, or the operations/security docs in `../docs/`.
