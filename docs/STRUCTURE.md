# Repository structure

Quick map of where code lives and what to refactor next. See **[SPLIT_AUDIT.md](./SPLIT_AUDIT.md)** for per-file split guidance.

## Frontend (`src/`)

| Path | Purpose |
|------|---------|
| `src/routes/index.tsx` | Live console route — wires `useAlgoStream` to layout |
| `src/components/algo/dashboard/` | Console UI pieces (extracted from monolithic route) |
| `src/lib/algo-format.ts` | KPI / payoff formatters |
| `src/lib/algoStreamState.ts` | Pure live-console reducers (`applyWsEvent`, REST hydrate) |
| `src/hooks/useAlgoStream.ts` | WebSocket + polling hook (uses `algoStreamState`) |
| `src/lib/api.ts` | REST client + DTO mappers (keep in sync with `backend/api/schemas.py`) |

### Dashboard modules

| File | Contents |
|------|----------|
| `dashboard/chrome.tsx` | Top bar, startup/resync banner |
| `dashboard/kpi.tsx` | Win-rate KPI card, equity KPI card |
| `dashboard/primitives.tsx` | Panel, ToggleRow |
| `dashboard/control-panels.tsx` | Strategy picker, risk, breakers (**split candidate**: one panel per file) |
| `dashboard/health.tsx` | System health collapsible |
| `dashboard/tables.tsx` | Positions, trades, live log |
| `dashboard/oms.tsx` | OMS + execution quality |
| `dashboard/index.ts` | Barrel re-exports |

## Backend (`backend/`)

| Path | Purpose |
|------|---------|
| `engine/core/engine.py` | Main orchestrator (~2.7k LOC) — **stable hub; do not split further by default** |
| `engine/core/book_resync_runtime.py` | L2 gap / reconnect / bulk book resync |
| `engine/core/mm_universe_runtime.py` | MM universe refresh (analytics seam) |
| `engine/core/clock.py`, `reconciliation.py`, … | Focused runtime helpers |
| `engine/strategies/market_making/` | **MM 2.0 only** (v1 removed) |
| `engine/strategies/flow_momentum.py` | Tape-flow directional strategy |
| `engine/strategies/pairs_trading.py` | Pairs basis (optional: extract stats/reference) |
| `engine/execution/` | VWAP, quote executor, MM execution helpers, `quote_clamp.py` |
| `common/config/` | Settings package (`settings.py` + `sections/*` mixins); `common/config.py` is a shim |
| `gateways/binance/` | Venue adapter |
| `analytics/` | Universe scanner, spread pipeline (offline / refresh jobs) |

### Market making package

```
engine/strategies/market_making/
  __init__.py      # MarketMakingV2Strategy, is_mm_strategy, universe helpers
  strategy.py      # MM 2.0 StrategyBase implementation
  core.py          # Quote intent, inventory, tape, halts (was mm_core.py)
  calibrated.py    # Per-symbol calibration (was mm_calibrated.py)
  symbol_params.py # Spread gates / params (was mm_symbol_params.py)
  universe.py      # MM2_SYMBOLS / AUTO resolution
```

Legacy shims at `engine/strategies/mm_*.py` re-export from the package for older imports.

**Removed:** `market_making.py` (v1), `market_making_v2.py` (moved to `strategy.py`).

Config still accepts `STRATEGY=market_making` as an alias → `market_making_v2`.

## Suggested next refactors (priority)

See [SPLIT_AUDIT.md](./SPLIT_AUDIT.md) for rationale.

1. **`src/lib/api.ts`** — split DTOs / client / mappers (re-export shim)
2. **`dashboard/control-panels.tsx`** — one panel component per file
3. **`analytics/mm_universe_scanner.py`** — scoring vs REST I/O (if touching universe)
4. **`pairs_trading.py`** — optional `pairs_stats` / `pairs_reference` extract

**Not recommended:** further splits of `engine.py` unless a new bounded workflow appears (same bar as `book_resync_runtime`).

## Tests

- Backend: `cd backend && pytest -q`
- Frontend: `npm run build`
