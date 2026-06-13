# Repository Structure

Quick map of where code lives and what to refactor next. See
[SPLIT_AUDIT.md](SPLIT_AUDIT.md) for per-file split guidance.

## Frontend (`src/`)

| Path | Purpose |
|------|---------|
| `src/routes/index.tsx` | Live console route; wires `useAlgoStream` to layout |
| `src/components/algo/dashboard/` | Console UI pieces extracted from the main route |
| `src/lib/algo-format.ts` | KPI and payoff formatters |
| `src/lib/algoStreamState.ts` | Pure live-console reducers for WebSocket events and REST hydration |
| `src/hooks/useAlgoStream.ts` | WebSocket + polling hook using `algoStreamState` |
| `src/lib/api.ts` | REST client and DTO mappers; keep in sync with `backend/api/schemas.py` |

### Dashboard Modules

| File | Contents |
|------|----------|
| `dashboard/chrome.tsx` | Top bar, startup/resync banner |
| `dashboard/kpi.tsx` | Win-rate KPI card, equity KPI card |
| `dashboard/primitives.tsx` | Panel, ToggleRow |
| `dashboard/control-panels.tsx` | Strategy picker, risk, breakers; split candidate |
| `dashboard/health.tsx` | System health collapsible |
| `dashboard/tables.tsx` | Positions, trades, live log |
| `dashboard/oms.tsx` | OMS and execution quality |
| `dashboard/index.ts` | Barrel re-exports |

## Backend (`backend/`)

| Path | Purpose |
|------|---------|
| `engine/core/engine.py` | Main orchestrator; stable hub, do not split further by default |
| `engine/core/book_resync_runtime.py` | L2 gap, reconnect, and bulk book resync |
| `engine/core/mm_universe_runtime.py` | Market-making universe refresh |
| `engine/core/clock.py`, `reconciliation.py`, ... | Focused runtime helpers |
| `engine/strategies/market_making/` | Market making 2.0 package |
| `engine/strategies/flow_momentum.py` | Tape-flow directional strategy |
| `engine/strategies/pairs_trading.py` | USDT/USDC implied-basis pairs strategy |
| `engine/execution/` | VWAP, quote executor, MM execution helpers, quote clamping |
| `common/config/` | Settings package; `common/config.py` is a compatibility shim |
| `gateways/binance/` | Binance venue adapter |
| `analytics/` | Backtests, reports, calibration, universe scans |

### Market-Making Package

```text
engine/strategies/market_making/
  __init__.py       # MarketMakingV2Strategy, is_mm_strategy, universe helpers
  strategy.py       # MM 2.0 StrategyBase implementation
  core.py           # Quote intent, inventory, tape, halts
  calibrated.py     # Per-symbol calibration
  symbol_params.py  # Spread gates and params
  universe.py       # MM2_SYMBOLS / AUTO resolution
```

Legacy shims at `engine/strategies/mm_*.py` re-export from the package for
older imports. Config still accepts `STRATEGY=market_making` as an alias for
`market_making_v2`.

## Suggested Next Refactors

See [SPLIT_AUDIT.md](SPLIT_AUDIT.md) for rationale.

| Priority | Target | Suggested action |
|---|---|---|
| 1 | `src/lib/api.ts` | Split DTOs, client, and mappers behind a stable re-export |
| 2 | `dashboard/control-panels.tsx` | Split one panel component per file |
| 3 | `analytics/mm_universe_scanner.py` | Separate scoring from REST I/O if touching universe logic |
| 4 | `pairs_trading.py` | Optional stats/reference extraction if working on pairs logic |

Not recommended: further splits of `engine.py` unless a new bounded workflow
appears with the same clarity as `book_resync_runtime`.

## Tests

- Backend: `cd backend && pytest -q`
- Frontend: `npm run build`
