# QF635 Report Alignment

This document maps the QF635 project report sections to the repository evidence
that already exists. It is intended as a reviewer and report-writing guide, not
as the final report itself.

## How to Use This Document

- Use the **repo evidence** column to find implementation details and diagrams.
- Use the **report work remaining** column to decide what still needs narrative,
  figures, results, or interpretation in the submitted report.
- Treat strategy results as pending until final backtests and live or paper runs
  are selected for the report.

## Alignment Matrix

| Report section | Current repo evidence | Report work remaining |
|---|---|---|
| 1. Executive Summary | Root `README.md` gives the product overview, local run path, dashboard purpose, and safety posture. | Write the final summary after the selected strategy, evaluation period, and headline performance are known. |
| 2. Motivation and Market Opportunity | `README.md` explains the Binance USDT-M Futures venue, paper/live modes, and infrastructure scope. `backend/engine/strategies/pairs_trading.py` documents the USDT/USDC implied basis motivation. | Add market rationale: why this venue, why the selected instruments, and what inefficiency or microstructure feature the strategy targets. |
| 3. Market Microstructure Analysis | `backend/engine/market_data/` implements order book, trade tape, feature store, data quality, spread, imbalance, and tape-pressure features. `backend/docs/market-data-and-strategies.md` maps these modules to course concepts. | Convert implementation features into analysis: spread/liquidity observations, fill-risk considerations, and how these shape the chosen strategy. |
| 4. Strategy Research and Signal Development | Strategy modules live in `backend/engine/strategies/`: pairs, SMA, blended signals, flow momentum, and market making. `backend/docs/market-data-and-strategies.md` summarizes their signal logic and analytics support. | Narrow the report to the final chosen strategy or strategy set. Explain signal hypotheses, parameters, and why alternatives were not the main focus. |
| 5. Model Selection and Justification | Strategies are interpretable rule-based models. Config aliases and strategy ids are centralized in `backend/common/config/aliases.py`. | State explicitly that the project uses rule-based microstructure strategies rather than machine learning unless later work adds a trained model. Justify interpretability, latency, and operational control. |
| 6. Strategy Implementation Logic | `backend/engine/core/engine.py` evaluates active strategies, groups pair legs, handles multi-strategy netting, and routes signals. Strategy-specific evidence is summarized in `backend/docs/market-data-and-strategies.md`. | Include pseudocode or diagrams for the selected strategy only. Avoid describing every implemented strategy as if all are part of final performance claims. |
| 7. Trading System Architecture | `README.md`, `backend/README.md`, `backend/docs/backend-architecture.md`, `docs/ARCHITECTURE.md`, and `backend/docs/*.mmd` describe the React console, FastAPI API, single Engine process, EventBus, gateway, execution, and persistence layers. | Use the architecture diagrams as report figures. Explain the design choice: one live engine writer, API control plane, event stream plus REST state hydration. |
| 8. Risk Management Framework | `backend/docs/risk-execution-and-portfolio.md`, `backend/engine/risk/`, `backend/engine/portfolio/`, `backend/engine/position/`, `backend/common/breaker_registry.py`, and `docs/OPERATIONS.md` cover pre-trade checks, circuit breakers, exposure caps, position sync, flattening, and readiness. | Summarize risk controls by layer: pre-trade, in-trade, portfolio, operator controls, and operational readiness. Include key limits used in final tests. |
| 9. Backtesting Methodology | `backend/analytics/backtest/`, `/api/backtest/*`, `src/routes/backtesting.tsx`, and `backend/docs/runtime-reference.md` implement offline 1m kline replay and dataset selection. | Run and document final backtests on an adequate dataset. The current no-key sample is only a smoke test and should not be presented as performance evidence. |
| 10. Performance Analysis | `backend/engine/execution/execution_metrics.py`, run archives, `/api/reports/latest`, strategy PnL reporting, dashboard KPIs, and backtest result objects provide performance measurement infrastructure. `backend/docs/risk-execution-and-portfolio.md` and `backend/docs/runtime-reference.md` identify the evidence paths. | Select final results and report return, drawdown, trades, win rate, execution quality, and any benchmark comparison. |
| 11. Strategy Optimisation | Calibration utilities exist for symbol selection, spread calibration, universe scanning, and pair analysis under `backend/analytics/`. Settings expose many strategy and risk parameters. | Describe only optimisation that was actually performed. Keep unused calibration utilities as future work if they are not part of the final strategy. |
| 12. Robustness Testing and Validation | `backend/tests/` covers engine boot, config aliases, gateways, risk, order state, backtesting, portfolio sync, strategy logic, execution, and market data guards. | Add robustness evidence for the selected strategy: out-of-sample windows, sensitivity tests, transaction cost assumptions, and failure-mode checks. |
| 13. Production Deployment Considerations | `docs/OPERATIONS.md`, `docs/SECURITY.md`, `docs/COMPLIANCE_AND_GOVERNANCE.md`, `backend/docs/runtime-reference.md`, `deploy/gcp/`, `deploy/vercel/`, `.github/workflows/deploy-gcp-backend.yml`, and env examples cover local, cloud, secrets, health, readiness, and hardening. | State this is production-like infrastructure, not a certified trading system. Explain secrets, API token, network restrictions, monitoring, and paper/live safeguards. |
| 14. Limitations and Future Enhancements | Security and compliance docs disclose unauthenticated read paths, client-bundled frontend token limitations, single-process constraints, and governance assumptions. | Add strategy-specific limitations: sample size, market regime dependence, Binance testnet realism, slippage assumptions, and future research steps. |
| 15. Conclusion | README and operations docs establish the platform capabilities. | Conclude after final performance and limitations are known. Tie the final claim to both strategy evidence and infrastructure readiness. |

## Architecture Evidence to Reuse

| Topic | Best source |
|---|---|
| System context | `backend/docs/architecture-system.mmd` |
| Boot and shutdown lifecycle | `backend/docs/architecture-lifecycle.mmd` |
| Per-tick trading path | `backend/docs/architecture-tick.mmd` |
| Strategy modes and netting | `backend/docs/architecture-strategies.mmd` |
| Parent-order execution | `backend/docs/architecture-execution.mmd` |
| Position and portfolio truth | `backend/docs/architecture-data-sync.mmd` |
| Operator controls | `backend/docs/architecture-control.mmd` |
| Circuit breakers | `backend/docs/architecture-breakers.mmd` |
| Frontend data plane | `backend/docs/architecture-frontend.mmd` |

## Backend Evidence Map

| Course concept | Backend evidence |
|---|---|
| Trading system architecture | `backend/docs/backend-architecture.md` |
| Market microstructure and LOB features | `backend/docs/market-data-and-strategies.md` |
| Signal development and strategy implementation | `backend/docs/market-data-and-strategies.md` |
| Execution algorithms and slippage | `backend/docs/risk-execution-and-portfolio.md` |
| Risk, portfolio, and position management | `backend/docs/risk-execution-and-portfolio.md` |
| Runtime reproducibility and validation | `backend/docs/runtime-reference.md` |

## Important Boundaries

- The repository is currently strongest on infrastructure: engine lifecycle,
  gateway abstraction, risk controls, execution, persistence, dashboard, and
  local/cloud operability.
- Final performance claims still require a chosen dataset, chosen strategy, and
  reproducible backtest or paper-trading evidence.
- The checked-in offline backtest sample is useful for smoke testing local
  setup, but it is too small for report conclusions.
- Canonical strategy ids should be used in the report. Short aliases such as
  `pairs`, `blend`, and `sma` are operator conveniences.
