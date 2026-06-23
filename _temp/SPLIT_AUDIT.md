# Split audit — clarity, readability, maintainability

Audit of large modules: whether splitting improves the codebase (not line-count targets).

## Criteria

| Criterion | Split when… |
|-----------|-------------|
| **Cohesion** | One named responsibility per module |
| **Coupling** | Little need for parent private state (`engine._x`) |
| **Navigation** | Same 300-line region edited repeatedly and hard to find |
| **Tests** | Unit tests without constructing the full parent |
| **Deps** | Keep heavy imports off hot paths |

## Verdict summary

| Priority | File | Lines | Split? | Action |
|----------|------|------:|--------|--------|
| — | `book_resync_runtime.py`, `mm_universe_runtime.py`, `common/config/`, `dashboard/*` | — | Done | Keep; do not re-chop |
| — | `src/lib/algoStreamState.ts` + `useAlgoStream.ts` | — | Done | Pure reducers vs WS hook |
| **High** | `src/lib/api.ts` | ~783 | **Yes** | DTOs / `api` client / mappers |
| **Medium** | `dashboard/control-panels.tsx` | ~592 | **Yes** | One panel per file |
| **Medium** | `analytics/mm_universe_scanner.py` | ~673 | **Consider** | `scoring.py` vs `scan_io.py` |
| **Medium** | `engine/strategies/pairs_trading.py` | ~991 | **Consider** | `_DeviationStats` + reference math |
| **Low** | `market_making/core.py` | ~980 | **Optional** | Only if MM core is edited weekly |
| **Low** | `src/routes/index.tsx` | ~650 | **Optional** | `useConsoleControls` if route grows |
| **Low** | `SettingsDialog.tsx` | ~572 | **Optional** | Tab components when form grows |
| **No** | `engine/core/engine.py` | ~2700 | **No** | Stable orchestration hub |
| **No** | `quote_executor.py`, `vwap_executor.py` | ~580 | **No** | Single-class executors |
| **No** | `ui/sidebar.tsx`, `chart.tsx` | 600+ | **No** | UI kit, not domain logic |

## `engine/core/engine.py` — do not split further

Single `Engine` class wiring gateway, market data, risk, OMS, fills, clock, strategy dispatch (~122 methods).

| Region | Approx. | Notes |
|--------|---------|--------|
| Init + accessors | 146–823 | Belongs with hub |
| Start / pause / resume / stop | 824–1145 | Lifecycle |
| Flatten / emergency | 1146–1699 | Tight OMS + gateway coupling |
| Market handlers | 1700–1843 | Book resync already extracted |
| Fills / account | 1844–2112 | Event glue |
| Clock + evaluate + dispatch | 2113–2681 | Core engine job |
| Status / health | 2682+ | Publishing |

**Instead of more files:** section comments in `engine.py`; extract only bounded workflows (like `book_resync_runtime`, `mm_universe_runtime`).

## `engine/strategies/pairs_trading.py` — optional

Layers: `_DeviationStats`, calibration loaders, strategy (`on_tick`, `_evaluate`, sizing).

**Reasonable extract:** `pairs_stats.py`, `pairs_reference.py` (pure math). Keep stateful evaluate/sizing in `pairs_trading.py`.

## `market_making/core.py` — optional

Package already has `strategy`, `calibrated`, `symbol_params`, `universe`. `core.py` is mostly pure functions; micro-split (`pricing`, `halts`, `exit_quotes`) only if MM is a hot edit zone.

## `analytics/mm_universe_scanner.py` — consider

Seams: scoring/thresholds, async REST I/O, CLI/report writers.

## `engine/execution/*` — no

One executor class per file; splitting adds indirection without clearer boundaries.

## Frontend

### `useAlgoStream` — done

- `src/lib/algoStreamState.ts` — `AlgoStream` type, constants, `applyEvent`, `applyTradingState`, `applyBackendOffline`
- `src/hooks/useAlgoStream.ts` — WebSocket, polling, React state only

### `src/lib/api.ts` — next high-value split

1. `api-types.ts` — DTOs, `WsEvent`
2. `api-client.ts` — `request`, `api` object
3. `api-mappers.ts` — `toPosition`, `toTrade`, backtest helpers

Re-export from `api.ts` shim for stable imports.

### `dashboard/control-panels.tsx` — easy win

Split into `strategy-picker.tsx`, `risk-panel.tsx`, `breakers-panel.tsx`, `control-limits-panel.tsx`; keep barrel in `index.ts`.

### `src/routes/index.tsx` — fine for now

Extract `useConsoleControls` / KPI scope only if the route exceeds ~800 lines.

## Recommended order

1. ~~`useAlgoStream` reducers~~ (done)
2. `api.ts` → types / client / mappers
3. `control-panels.tsx` → one file per panel
4. `mm_universe_scanner` scoring vs I/O (if working on universe)
5. `pairs_trading` stats/reference (if working on pairs)
6. **Leave `engine.py` alone** except in-file sections

## Tests

- Backend: `cd backend && pytest -q`
- Frontend: `npm run build`
