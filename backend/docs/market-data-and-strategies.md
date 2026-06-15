# Market Data, Strategies, and Analytics

This document maps the strategy and analytics parts of the backend to the
market microstructure concepts around limit order books, signals,
backtesting, and strategy design.

## Diagram Anchors

| Diagram | Use |
|---|---|
| [`architecture-tick.mmd`](architecture-tick.mmd) | Market callbacks, feature snapshots, strategy tick |
| [`architecture-strategies.mmd`](architecture-strategies.mmd) | Single strategy, `all` mode, signal netting, market-making quote path |

## Market Data (`engine/market_data/`)

| Module | Role |
|---|---|
| `orderbook.py` | L2 book state and spread/imbalance calculations |
| `trade_tape.py` | Rolling trade tape and aggressor-side ratios |
| `feature_store.py` | Read-through feature snapshots used by strategies |
| `data_quality.py` | Sequence gaps, crossed books, resync triggers |
| `mid_tracker.py` | Mid-price tracking for PnL and features |
| `microstructure_hub.py` | Market-making microstructure features |
| `book_depletion.py` | Effective depth and depletion signals |
| `toxicity.py` | Toxicity scoring for MM risk |
| `markout_tracker.py` | Post-fill markout observations |
| `own_quote_book.py` | Resting MM quote state |
| `symbol_calibration.py` | Per-symbol calibration artefacts |

Concept connection:

- LOB mechanics are represented by `OrderBook` and depth snapshots.
- Order-flow and aggressor pressure are represented by `TradeTape`.
- Trading signals consume normalized `Features` rather than raw exchange events.

## Analytics (`analytics/`)

`analytics/` is off-engine analysis and job code. It supports calibration,
backtests, reports, and strategy research without blocking the live trading
loop.

| Module | Role |
|---|---|
| `backtest/` | Offline kline replay engine and metrics |
| `data_loader.py` | Kline download into `data/klines/` |
| `kline_store.py` | Kline library and run-session datasets |
| `pair_analyzer.py` | Pair spread analysis and calibration output |
| `orderbook_analyzer.py` | Tape/order-book distribution analysis |
| `l2_loader.py`, `l2_store.py` | L2 snapshot capture for spread calibration |
| `spread_calibrator.py` | Suggested half-spreads from observed L2 |
| `mm_spread_pipeline.py` | L2 capture plus calibration pipeline |
| `mm_universe_scanner.py` | MM universe ranking |
| `strategy_pnl_report.py` | Per-strategy PnL attribution from run archives |
| `daily_report.py` | Latest run summary for `/api/reports/latest` |
| `jobs.py`, `worker_main.py` | Async analytics job records and worker |

Common commands:

```powershell
python -m analytics.data_loader --symbols BTCUSDT,ETHUSDT --interval 1m --days 30
python -m analytics.pair_analyzer --base BTC --interval 1m --window 60
python -m analytics.orderbook_analyzer --symbol BTCUSDT --window-sec 300
python -m analytics.mm_spread_pipeline --from-mm-symbols --minutes 15 --interval-sec 1
python -m analytics.strategy_pnl_report --runs-dir data/runs
```

## Strategy Framework (`engine/strategies/`)

Every strategy implements `StrategyBase` and produces either:

- `Signal` objects for alpha strategies routed through risk and execution; or
- `QuoteIntent` objects for market making, refreshed by `QuoteExecutor`.

Canonical strategy ids:

| Strategy | Canonical id | Main path | Execution path |
|---|---|---|---|
| Pairs basis | `pairs_trading_usdt_usdc` | `pairs_trading.py` | Netted VWAP |
| SMA crossover | `sma_crossover` | `sma_crossover.py` | Netted VWAP |
| Blended signals | `blended_signals` | `blended_signals.py` | Netted VWAP |
| Flow momentum | `flow_momentum` | `flow_momentum.py` | Netted VWAP |
| Market making 2.0 | `market_making_v2` | `market_making/strategy.py` | QuoteExecutor |
| All strategies | `all` | `signal_netter.py` plus all registered strategies | Alpha netting plus MM quotes |

Short aliases such as `pairs`, `pairs_trading`, `sma`, `blend`, `flow`, and
`market_making` are normalized in `common/config/aliases.py`.

## Pairs Trading

`pairs_trading.py` uses the fact that Binance Futures Testnet has no direct
USDT/USDC perpetual. It infers the stablecoin basis from coins listed in both
quotes:

```text
basis_i = log(coinUSDC.mid) - log(coinUSDT.mid)
deviation_i = basis_i - reference
z_i = (deviation_i - rolling_mean) / rolling_std
```

Entries fade extreme deviations. Exits and stops are expressed in basis
z-space, so the strategy manages its own risk and bypasses fixed per-leg stop
brackets. Portfolio-level risk controls still apply.

## SMA Crossover

`sma_crossover.py` is a multi-symbol fast/slow simple moving average strategy.
It uses engine-managed per-leg brackets and equity-budgeted sizing.

## Blended Signals

`blended_signals.py` combines EMA trend, MACD momentum, RSI, Bollinger %B, and
microstructure confirmation into a sparse directional signal.

## Flow Momentum

`flow_momentum.py` follows persistent one-sided tape pressure across the
configured universe. It manages its own stop/take-profit/hold-time exits.

## Market Making 2.0

`engine/strategies/market_making/` is the market-making package:

| Module | Role |
|---|---|
| `strategy.py` | `StrategyBase` implementation |
| `core.py` | Quote intent, inventory, tape, gates |
| `avellaneda_stoikov.py` | AS pricing helper |
| `calibrated.py` | Calibration loading |
| `symbol_params.py` | Per-symbol spread gates and params |
| `inventory_cap.py` | Portfolio inventory cap logic |
| `universe.py` | `MM2_SYMBOLS` / AUTO resolution |

Market making posts standing quotes through `QuoteExecutor`, not through the
VWAP wheel. It has separate microstructure risk gates for toxicity, depletion,
jumps, markout, inventory, and spread quality.

## Multi-Strategy Mode

`STRATEGY=all` runs all registered strategies. Alpha strategy signals are netted
per symbol by `signal_netter.py` before shared risk and execution. MM2 quote
intents run in parallel and are not netted with alpha signals.

The engine tracks per-strategy attribution through `position/strategy_ledger.py`
and performance helpers under `engine/performance/`.

## Backtesting Context

Offline backtesting currently supports SMA, blended, and pairs via
`analytics/backtest/strategy_factory.py`. Dataset options include:

- `library`: merged kline library in `backend/data/klines`.
- `run:<id>`: bars captured from a live/paper run.

A local kline sample is useful for smoke testing setup, but `backend/data/` is
gitignored and any local sample is too small for final report performance claims
unless it was deliberately downloaded or captured for the chosen evaluation
period.
