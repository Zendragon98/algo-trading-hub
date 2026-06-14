# Submission Handoff Checklist

Temporary checklist for teammates reviewing this branch before the final
submission package is frozen.

- Submit the repository URL: `https://github.com/Zendragon98/algo-trading-hub.git`.
- Keep secrets and local runtime evidence out of Git: `backend/.env`,
  `backend/data/`, `node_modules/`, `dist/`, and virtual environments are
  ignored.
- Put Binance Demo/Testnet keys only in `backend/.env` when the reviewer wants
  to start the engine against Binance. API-only startup and the smoke backtest
  do not need keys.
- Use [`docs/REPORT_ALIGNMENT.md`](../docs/REPORT_ALIGNMENT.md) as the map from
  report sections to repository evidence.
- Treat the no-key smoke backtest as setup validation only. Final report
  performance claims still need a selected strategy, evaluation window,
  reproducible dataset, and archived results.
