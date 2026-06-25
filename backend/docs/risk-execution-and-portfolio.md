# Risk, Execution, and Portfolio

This document explains how signals become orders, how orders are protected, and
how position truth flows back into the engine and dashboard.

## Diagram Anchors

| Diagram | Use |
|---|---|
| [`architecture-tick.mmd`](architecture-tick.mmd) | 1 Hz risk/strategy/dispatch loop |
| [`architecture-execution.mmd`](architecture-execution.mmd) | Parent order and VWAP child-order path |
| [`architecture-breakers.mmd`](architecture-breakers.mmd) | Circuit breaker states |
| [`architecture-data-sync.mmd`](architecture-data-sync.mmd) | Venue position truth and dashboard sync |
| [`architecture-control.mmd`](architecture-control.mmd) | Operator controls and engine effects |

## Execution Path

Alpha strategies emit `Signal` objects. The engine sends them through:

```text
Signal -> PreTradeValidator -> RiskManager -> ExecutionRouter
       -> AlgoWheel -> Slicer -> VwapExecutor -> OrderManager -> Gateway
```

Market making emits `QuoteIntent` objects instead:

```text
QuoteIntent -> QuoteExecutor -> OrderManager -> Gateway
```

This split is important: alpha strategies trade parent orders through VWAP,
while MM2 keeps standing post-only quotes alive with cancel/replace logic.

## Execution Modules (`engine/execution/`)

| Module | Role |
|---|---|
| `execution_router.py` | Parent-order creation and routing |
| `algo_wheel.py` | FRONTLOAD, NORMAL, BACKLOAD schedule choice |
| `slicer.py` | Parent quantity split into child slices |
| `vwap_executor.py` | LIMIT slices, timeout, cancel, market fallback |
| `quote_executor.py` | Market-making quote refresh and cancel/replace |
| `submit_guard.py` | Open-parent and rate-limit guard |
| `quality_guard.py` | Execution-quality breaker logic |
| `execution_metrics.py` | Arrival price, VWAP, slippage, fill ratio, duration |
| `slippage_guard.py` | In-flight slippage checks |
| `mm_execution.py` | MM quote execution mode helpers |
| `quote_clamp.py` | Prevent quote crossing |

Execution quality reports stream as `PARENT_UPDATE` and `EXECUTION_REPORT`
events and are exposed through `/api/execution`.

## Risk Stack (`engine/risk/`)

| Layer | Module | Purpose |
|---|---|---|
| Pre-trade | `pretrade_validator.py` | Dedup, spread checks, venue floor/cap, pair-group validation |
| Limits | `risk_manager.py`, `limits.py` | Per-trade risk, gross notional, risk exits |
| Exposure | `exposure_tracker.py`, `order_exposure_guard.py` | Symbol and order exposure |
| Venue sizing | `venue_sizing.py` | Min qty, max qty, notional checks |
| Market data | `market_data_guard.py` | Stale/wide-spread vetoes |
| Stops | `stop_loss.py` | Engine-managed per-leg brackets |
| Losses | `loss_tracker.py`, `pnl_tracker.py` | Consecutive losses and PnL state |
| Margin | `margin_ratio_guard.py` | Margin ratio monitoring; emits ExitIntent to reduce largest position when threshold breached |
| MM flow | `mm_flow_guard.py` | Toxic flow, jump, depletion guards |
| Breakers | `circuit_breaker.py`, `common/breaker_registry.py` | Minor/major halt logic |

Strategies that return `manages_own_risk() == True` bypass fixed per-leg
`StopLossMonitor` brackets for their symbols. They still pass through
pre-trade, exposure, gross-notional, and portfolio-level controls.

## Circuit Breakers

Breaker severities:

- `MINOR`: temporary veto/pause, clears after cooldown.
- `MAJOR`: flatten and latch until explicit rearm.

Examples:

| Breaker | Scope | Typical effect |
|---|---|---|
| `stale_tick` | symbol | Pause entries for stale public market data |
| `wide_spread` | symbol | Veto entry on poor spread |
| `stale_market_data` | engine | Pause entries when the public market-data WebSocket is silent |
| `stale_user_data` | engine | Pause entries when user-data WebSocket is stale while orders are working |
| `toxic_flow` | symbol | Pause MM quotes |
| `reconcile_mismatch` | engine | Flatten and latch on venue/local drift |
| `max_drawdown` | engine | Flatten and latch |
| `operator_halt` | engine | Manual halt |

Reduce-only exits bypass entry breakers so flatten, stop-loss, and take-profit
orders can reach the venue.

## Operator Controls

| Control | Endpoint | Effect |
|---|---|---|
| Start | `POST /api/control/start` | Connect and start loops |
| Pause / Resume | `POST /api/control/pause`, `POST /api/control/resume` | Stop/resume strategy evaluation |
| Stop | `POST /api/control/stop` | Optional flatten and disconnect |
| Flatten | `POST /api/control/flatten` | Pause, cancel, sync venue, close legs; resume if was RUNNING |
| E-Stop | `POST /api/control/kill` | Flatten + `Engine.stop()`; API stays up |
| Shutdown | `POST /api/control/shutdown` | Flatten positions, stop engine, and exit process |
| Strategy | `POST /api/control/strategy` | Hot-swap strategy |
| Risk | `PATCH /api/control/risk` | Update `max_risk_pct` |
| Breakers | `GET /api/control/breakers`, `PATCH /api/control/breakers/enabled`, `POST /api/control/breakers/trip`, `POST /api/control/breakers/rearm` | Inspect, enable/disable, trip, rearm |

## Flatten Path

`POST /api/control/flatten` runs `_flatten_and_wait_for_flat()`:

1. Pause strategy evaluation.
2. Cancel open venue/local working orders.
3. Pull venue positions.
4. Close open legs with market, aggressive VWAP, or passive VWAP.
5. Poll the venue until flat or timeout.
6. Resume trading if the engine was RUNNING before flatten; remain paused if already paused.

Flatten orders are reduce-only. Small/wide-spread positions use market closes;
larger tight-spread positions can use VWAP flatten settings.

## Orders and OMS (`engine/orders/`)

`OrderManager` tracks parent and child order state. It receives local submits,
venue acknowledgements, fills, cancels, and reconcile updates. Child
`clientOrderId` values are deterministic per parent slice to support safe
retries.

## Position and Portfolio Truth

| Path | Role |
|---|---|
| `engine/position/position_tracker.py` | Per-symbol size, entry, realized PnL |
| `engine/position/strategy_ledger.py` | Per-strategy attribution in `STRATEGY=all` |
| `engine/position/venue_pnl.py` | Venue fill/PnL helpers |
| `engine/portfolio/portfolio.py` | Wallets, cash, equity curve, snapshots |

Binance is the source of truth for account and position state. The engine
aligns through:

- startup REST account snapshot,
- user-data WebSocket `ACCOUNT_UPDATE`,
- user-data reconnect resync,
- periodic reconcile when user-data is idle,
- dashboard REST polling as a UI safety net.

The dashboard should be treated as indicative until user-data is fresh and
reconcile state is clean.

## Performance and Attribution

`engine/performance/` maintains:

- rolling/session win rate,
- profit factor,
- session costs,
- close attribution,
- strategy analytics for the dashboard.

Run-level reports can be built from the JSONL archive without rerunning live
trading.
