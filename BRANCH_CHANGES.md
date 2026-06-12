# Branch Changes From Main

This file records intentional changes made on the `wei-han` branch relative to
`main`. It exists so reviewers and teammates can distinguish submission-readiness
work from strategy or runtime changes.

## Why Keep This Log

- **Reviewer orientation:** a professor or teammate can see why files changed
  without reverse-engineering the Git diff.
- **Report traceability:** documentation and setup changes can be mapped back to
  the QF635 requirement that the repository be runnable and understandable.
- **PR handoff:** this file becomes a concise source for a pull request summary.
- **Scope control:** infrastructure, documentation, and later strategy changes
  can be tracked separately instead of blurring together.

## Phased Changes

### Phase 1: Local Run Readiness

**Why this phase exists:** compared with `main`, the repository needed a clearer
fresh-clone path so a reviewer could install dependencies, start the dashboard,
understand where secrets belong, and run a no-key validation path without
reverse-engineering the backend.

**Files changed:**

- `README.md`
- `backend/README.md`
- `backend/.env.example`
- `backend/main.py`
- `backend/tests/test_main_boot.py`
- `run-local.ps1`
- `docs/README.md`
- `.env.example`
- `package-lock.json`
- `vite.config.ts`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Made the root README the self-contained front door for course review.
- Reordered the root README so the reader sees overview, prerequisites,
  installation, and local run instructions before deep architecture details.
- Aligned the root README strategy table with implemented strategy ids by
  adding `blended_signals` and removing a duplicate `market_making_v2` row.
- Added an explicit installation guide covering clone, Python 3.11 backend
  setup, dependency install, `backend/.env`, and frontend dependencies.
- Switched frontend install guidance to `npm ci` for lockfile-based installs.
- Clarified safe first-run settings: `TRADING_MODE=paper`,
  `BINANCE_TESTNET=true`, and `ENGINE_AUTOSTART=false`.
- Clarified that Binance Demo/Testnet keys are needed for engine connectivity
  to account/order endpoints, but not for the offline backtest demo.
- Clarified that account equity/balances remain unseeded defaults until the
  stopped engine is started and connects to Binance.
- Added a no-key offline backtest command using checked-in kline data.
- Clarified that the no-key backtest is a smoke test, not a performance result,
  because the checked-in sample currently contains only a few bars.
- Changed `backend/.env.example` Binance credentials to blank values so copied
  env files do not look configured before real Demo/Testnet keys are supplied.
- Clarified that local frontend development does not need a root `.env`; the
  root `.env.example` is only a frontend deployment example for `VITE_*` values.
- Updated setup-facing config references to point at `backend/common/config/`
  and corrected the documented `BINANCE_REST_MIN_INTERVAL_MS` default to `200`.
- Fixed API-only backend startup so `python main.py --no-engine` and the default
  stopped boot do not resolve Binance `AUTO` universes before serving health.
- Synchronized `package-lock.json` so the documented `npm ci` path works.
- Added `recharts` to Vite's explicit dependency optimization list so the
  dashboard dev server prebundles Recharts instead of serving its ESM imports
  against CommonJS `lodash/get` directly.
- Added `run-local.ps1` as a Windows convenience launcher that starts backend
  and frontend as separate local processes from one terminal.
- Updated `run-local.ps1` to auto-detect an active Conda environment, otherwise
  use/create `backend/.venv`, and to install backend/frontend dependencies only
  when dependency checks fail.

**Why these changes matter:**

- The README now works as the repository front door instead of assuming the
  reader already knows how backend, frontend, and Binance credentials fit
  together.
- Environment setup is less ambiguous: local backend secrets live in
  `backend/.env`; the root `.env.example` is only for frontend deployment
  variables.
- API-only startup is safer for review because the backend can serve health and
  dashboard state before any Binance connectivity is attempted.
- Reviewers are less likely to mistake the initial `0` dashboard balance for a
  Binance API failure when `ENGINE_AUTOSTART=false`.
- The one-terminal launcher reduces friction on Windows while keeping backend
  and frontend as separate processes.
- Conda users can run `.\run-local.ps1` directly after activating their Conda
  environment instead of passing Python override flags.
- The dashboard renders after `npm ci` in Vite dev mode instead of falling into
  the route error boundary on Recharts/lodash module interop.

**Runtime impact:** no strategy, gateway, execution, or production trading
behavior was changed. Backend API-only startup now avoids a pre-serve Binance
REST call when the engine is not autostarting. Frontend local-dev behavior
changed only to prebundle Recharts so the dashboard renders correctly after
`npm ci`.

**Verification:**

- No-key offline backtest command passed; it produced a 5-bar, 0-trade smoke
  result under `backend/data/backtest_runs`.
- `python main.py` and `python main.py --no-engine` served `/health` and
  `/ready` without Binance connectivity.
- `python -m pytest tests/test_main_boot.py tests/test_universe_bootstrap.py -q`
  passed.
- `npm ci` passed after synchronizing the lockfile.
- `npm run build` passed after reinstalling dependencies.
- Headless Chrome verified `http://localhost:5173` renders the live dashboard
  after the Vite dependency optimization fix.
- `run-local.ps1 -NoInstall` started both local services; backend `/health`
  responded and Vite reported `http://localhost:5173/` ready.
- `.\run-local.ps1` was run successfully from the user's local terminal after
  adding Conda detection and dependency checks.
- `git diff --check` passed after the edits.
