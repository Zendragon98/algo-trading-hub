# Backend Architecture

This document describes how the backend process is wired. It mirrors the
repository structure rather than replacing it.

## Diagram Anchors

| Diagram | Use |
|---|---|
| [`architecture-system.mmd`](architecture-system.mmd) | End-to-end backend/frontend/venue context |
| [`architecture-lifecycle.mmd`](architecture-lifecycle.mmd) | `main.py` boot, `Engine.start`, shutdown |
| [`architecture-events.mmd`](architecture-events.mmd) | EventBus, JSONL persistence, WebSocket fan-out |
| [`architecture-gateway.mmd`](architecture-gateway.mmd) | Gateway seam and Binance adapter |
| [`architecture-frontend.mmd`](architecture-frontend.mmd) | Frontend hydrate and WebSocket consumption |

## Process Model

`backend/main.py` creates the runtime:

1. Load `Settings` from defaults and `backend/.env`.
2. Create one `EventBus`.
3. Resolve `AUTO` symbol universes and partition the multi-strategy universe.
4. Bootstrap a run archive and optional WAL.
5. Configure logging (after archive path is known).
6. Create a gateway through `gateways.factory.create_gateway`.
7. Create strategies, then the `Engine`, then wire equity and position providers.
8. Auto-start the engine if `ENGINE_AUTOSTART=true` or `--engine` flag is set.
9. Start the `AnalyticsWorkerSupervisor`.
10. Create the FastAPI app with `api.server.create_app`.
11. Serve uvicorn on the same asyncio process.

The trading process is a single live writer. The API exposes state and control,
but engine state is owned by `Engine`, not by the dashboard.

Analytics jobs run outside the hot trading loop through
`analytics.worker_supervisor` and `analytics.worker_main`.

## API Layer (`api/`)

| Path | Role |
|---|---|
| `api/server.py` | FastAPI app factory, CORS, auth middleware, route registration |
| `api/schemas.py` | Pydantic DTOs mirrored by frontend TypeScript models |
| `api/serializers.py` | Engine/domain objects to DTOs |
| `api/ws.py` | WebSocket subscriber for EventBus events |
| `api/routes/control.py` | Start, pause, resume, stop, flatten, E-Stop, strategy, breakers |
| `api/routes/backtest.py` | Backtest dataset, job, and result endpoints |
| `api/routes/settings.py` | Settings read/update with masking and normalization |
| `api/routes/*` | State, positions, orders, trades, logs, reports, klines |

Control endpoints live under `/api/control/*`. When `API_TOKEN` is set, these
routes require `Authorization: Bearer <token>`.

## Engine Core (`engine/core/`)

`Engine` is the orchestrator. It owns:

- strategy selection and hot-swap,
- market data subscriptions,
- heartbeat and background loops,
- risk exits,
- signal dispatch,
- OMS and execution routing,
- portfolio and position state,
- reconciliation against venue truth,
- strategy analytics snapshots.

Supporting core modules keep bounded runtime workflows outside the main class:

| Module | Responsibility |
|---|---|
| `clock.py` | 1 Hz heartbeat |
| `connection_monitor.py` | Market/user-data freshness |
| `reconciliation.py` | Position reconcile loop |
| `order_reconciliation.py` | Venue open-orders vs OMS |
| `book_resync_runtime.py` | L2 resync after gaps/reconnects |
| `mm_universe_runtime.py` | Market-making universe refresh |
| `state.py` | Engine state containers |

## Gateway Layer (`gateways/`)

The engine depends on `GatewayInterface`, not on Binance directly.

| Path | Role |
|---|---|
| `gateways/gateway_interface.py` | Abstract venue contract |
| `gateways/factory.py` | Builds the configured gateway from `VENUE` |
| `gateways/binance/` | Binance USDT-M Futures REST and WebSocket adapter |
| `gateways/ibkr/` | IBKR connector scaffold conforming to the same interface |

Binance is the fully runnable venue adapter used by the default local review
flow. The IBKR package is kept as an interface-compliant connector scaffold:
`VENUE=ibkr` proves factory wiring and settings shape, but the trading methods
still raise `NotImplementedError` until the IB API calls are implemented.

The Binance adapter is split by connection type:

- `rest_client.py`: signed REST wrapper and pacing.
- `market_connection.py`: public market-data streams.
- `order_connection.py`: order REST and user-data stream lifecycle.
- `account_connection.py`: balances and positions.
- `binance_gateway.py`: composition layer implementing `GatewayInterface`.

## Common Layer (`common/`)

`common/` holds shared contracts used across API, engine, gateways, analytics,
and tests.

| Path | Role |
|---|---|
| `config/settings.py` | Full `Settings` model |
| `config/sections/` | Settings grouped by venue, strategy, risk, execution, API/persistence |
| `config/aliases.py` | Strategy alias normalization |
| `events.py` | EventBus and event envelope |
| `types.py` | Shared dataclasses such as signals, fills, orders, features |
| `enums.py` | Shared enums including `EventType` |
| `breaker_registry.py` | Canonical breaker definitions |
| `universe_bootstrap.py` | Startup universe resolution helpers |
| `multi_strategy_universe.py` | Multi-strategy universe partitioning at boot |

## Persistence

Runtime state and audit evidence are written under `backend/data/`:

- `data/runs/<run-id>/`: per-run JSONL archives and logs.
- `data/klines/`: backtest kline library.
- `data/backtest_runs/`: saved offline backtest results.
- `data/jobs/`: analytics job records.

`engine/persistence/` owns event recording, WAL replay, market capture, and run
bootstrap. JSONL records use the same event envelope as `/ws`, but the archive
contains additional audit streams such as markouts and optional ticks. A run
can be reviewed without replaying live trading.

## Dashboard Boundary

The browser never talks to Binance. It reads:

- `GET /api/state` for authoritative snapshots.
- `/ws` for incremental events.
- specific REST routes for logs, klines, settings, control, and backtests.

This boundary is why the backend is the source of truth for positions, orders,
portfolio state, and operational status.
