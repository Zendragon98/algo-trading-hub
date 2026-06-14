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
- Added a no-key offline backtest command using the local kline library.
- Clarified that the no-key backtest is a smoke test, not a performance result,
  because local samples under `backend/data/` are setup evidence unless they are
  deliberately generated and documented for evaluation.
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

### Phase 2: Report Alignment and Architecture Evidence

**Why this phase exists:** compared with `main`, the repository had strong
infrastructure documentation but no course-facing map from the QF635 report
guidelines to the implemented code, diagrams, tests, and remaining report work.

**Files changed:**

- `docs/REPORT_ALIGNMENT.md`
- `docs/README.md`
- `README.md`
- `backend/docs/architecture-strategies.mmd`
- `backend/docs/architecture-control.mmd`
- `backend/common/breaker_registry.py`
- `backend/tests/test_breaker_strategy_scope.py`
- `src/lib/algoStreamState.ts`
- `BRANCH_CHANGES.md`
- `docs/STRUCTURE.md`

**What changed compared with `main`:**

- Added `docs/REPORT_ALIGNMENT.md`, a section-by-section map from the QF635
  report guidelines to repository evidence and remaining report work.
- Added the report-alignment document to the documentation register.
- Reorganized the documentation register into primary reading, operations and
  governance, and supporting/generated material.
- Corrected the root README strategy table to use the canonical pairs strategy
  id `pairs_trading_usdt_usdc`.
- Clarified that short names such as `pairs`, `pairs_trading`, `sma`, and
  `blend` are accepted aliases, while the README table shows canonical engine
  ids.
- Reduced the root README's opening interruption by replacing the long document
  table with direct links to the documentation register and QF635 alignment
  guide.
- Cleaned `docs/STRUCTURE.md` formatting and reframed it as a quick contributor
  code map instead of a mixed structure/refactor note.
- Updated the strategy architecture diagram so market making quote intents flow
  through `QuoteExecutor` and `OrderManager`, separate from the alpha strategy
  VWAP execution path.
- Corrected root README, the editable control-plane diagram, and a frontend
  offline message so E-Stop maps to `POST /api/control/kill` while process
  shutdown remains the separate `POST /api/control/shutdown` path.
- Fixed stale breaker scoping for `group_unwind_failed` so fallback strategy
  filtering checks the canonical pairs strategy id.
- Added a targeted breaker-scope test for the canonical pairs strategy id.

**Why these changes matter:**

- A professor or teammate can now trace the report outline directly to files in
  the repository without guessing which implementation supports which section.
- The documentation now better separates implemented infrastructure evidence
  from still-pending strategy evaluation and report narrative.
- Strategy naming is consistent between docs, frontend settings, API state, and
  backend strategy classes.
- The architecture diagram now reflects the actual difference between alpha
  signal execution and market-making quote execution.

**Runtime impact:** one low-risk code path changed: unattributed
`group_unwind_failed` breaker fallback scoping now uses the canonical pairs
strategy id. No strategy signal logic, gateway logic, or execution sizing was
changed.

### Phase 3: Backend Documentation Structure

**Why this phase exists:** the backend source tree was already organized around
infrastructure domains, but `backend/README.md` tried to explain every domain
in one long document. For a graded submission, the backend needs to be easier
to navigate without moving working source files.

**Files changed:**

- `backend/README.md`
- `backend/docs/backend-architecture.md`
- `backend/docs/market-data-and-strategies.md`
- `backend/docs/risk-execution-and-portfolio.md`
- `backend/docs/runtime-reference.md`
- `docs/README.md`
- `docs/OPERATIONS.md`
- `docs/REPORT_ALIGNMENT.md`
- `README.md`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Reframed `backend/README.md` as the backend front door: purpose, quick start,
  folder map, engine subsystem map, common commands, and reading path.
- Added focused backend docs that mirror the existing backend folders instead
  of reorganizing code:
  - architecture and runtime wiring,
  - market data, strategies, and analytics,
  - risk, execution, orders, portfolio, and performance,
  - runtime reference, API surface, run archive, tests, and troubleshooting.
- Updated the repository documentation register to include the backend deep
  dives.
- Updated the report-alignment document so QF635 sections point to the new
  backend evidence docs.
- Redirected root README and operations runbook links from old backend README
  anchors to the new focused backend documents.

**Why these changes matter:**

- A reviewer can now understand the backend by following the same boundaries as
  the codebase: `api`, `engine`, `gateways`, `common`, `analytics`, and tests.
- The detailed infrastructure evidence is still available, but no longer packed
  into one oversized README.
- The docs now use the architecture diagrams as anchors for the written
  explanation.

**Runtime impact:** documentation-only. No source files, imports, scripts, or
runtime behavior were reorganized.

### Phase 4: Submission Readiness Validation

**Why this phase exists:** after improving setup and documentation, the branch
needed an evidence-based readiness pass to confirm that a local reviewer can
build the dashboard and run the backend tests without avoidable failures.

**Files changed:**

- `backend/tests/test_backtest_runner.py`
- `backend/tests/test_market_making_v2.py`
- `backend/tests/test_mm_universe_scanner.py`
- `backend/tests/test_multi_strategy.py`
- `backend/tests/test_pairs_trading.py`
- `.prettierrc`
- `.prettierignore`
- `eslint.config.js`
- `src/lib/algoStreamState.ts`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Replaced a Unix-only `/tmp` test path with pytest's `tmp_path` fixture so the
  market-capture test works on Windows.
- Isolated a pairs bar-aggregation test from persisted local warmup state by
  giving it a temporary persistence directory.
- Updated market-making tests to match the current implementation contracts:
  - fee-floor minimum edge is round-trip fee plus spread buffer,
  - MM quote evaluation in `STRATEGY=all` only runs while the engine is
    `RUNNING`,
  - the two-sided MM skew test disables inside-touch pegging so it tests the
    skew gate rather than quote placement constraints.
- Made frontend lint usable in a local Windows review environment by:
  - excluding local/generated folders such as `.claude/` and `backend/.venv/`,
  - separating Prettier formatting from ESLint quality checks,
  - allowing Prettier to preserve local line endings,
  - fixing one `prefer-const` lint error in `src/lib/algoStreamState.ts`.

**Why these changes matter:**

- The full backend pytest suite now passes locally on Windows.
- The tests no longer depend on machine-specific paths or stale runtime state.
- The test expectations better describe the current trading-engine behaviour a
  reviewer will exercise.
- `npm run lint` now reports warnings instead of failing on local worktrees,
  virtualenv files, or formatting churn.

**Validation performed:**

- `python -m pytest -q` from `backend/`: 510 passed.
- `python -m ruff check` on the changed backend tests: passed.
- `npm.cmd run lint` from repo root: 0 errors, 13 warnings.
- `npm.cmd run build` from repo root: passed.
- `git diff --check`: passed.

**Runtime impact:** test and tooling only, plus one frontend `let` to `const`
cleanup with no behaviour change. No backend engine, strategy, gateway,
execution, or dashboard runtime logic changed.

### Phase 5: Submission-Grade Repository Experience

**Why this phase exists:** the repository was runnable and validated, but a
first-time course reviewer still needed a clearer path through the top-level
README and documentation register. This phase keeps the focus on infrastructure
readability rather than strategy performance or report writing.

**Files changed:**

- `README.md`
- `docs/README.md`
- `docs/STRUCTURE.md`
- `docs/REPORT_ALIGNMENT.md`
- `backend/docs/market-data-and-strategies.md`
- `backend/docs/runtime-reference.md`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Added a compact validation checklist to the root README covering backend
  tests, frontend lint/build, health/readiness checks, and the no-key smoke
  backtest.
- Reduced duplication in the root README's "Learn more" table and grouped
  backend references by reviewer intent: runtime, risk/execution, and
  market-data/strategy logic.
- Reframed the documentation register around a course-review reading path, then
  separated backend deep dives, operations/governance, supporting material, and
  optional/non-core material.
- Reworded `docs/STRUCTURE.md` as a source-tree map first, with refactor notes
  clearly marked as future maintainability context.
- Clarified that offline backtest smoke tests use local `backend/data/klines`
  data, and that `backend/data/` is gitignored rather than shipped as committed
  evidence.
- Switched the documented no-key smoke test to `sma`/`BTCUSDT` so it validates
  the offline backtest path without touching pairs-strategy warmup state.

**Why these changes matter:**

- A reviewer can now answer "what should I run first?" from the README without
  reading the full architecture section.
- Optional documents such as investor-deck and generated netting material no
  longer compete with the core QF635 review path.
- The repo still presents the same infrastructure, but with clearer entry
  points for setup, validation, backend internals, architecture, and operations.

**Runtime impact:** documentation-only. No code, tests, scripts, or runtime
configuration changed.

### Phase 6: Fresh Reviewer Run-Through

**Why this phase exists:** after improving the documentation, the next risk was
whether the documented setup path actually behaves like a fresh reviewer would
expect. This phase exercised the README validation commands and the one-terminal
launcher path.

**Files changed:**

- `run-local.ps1`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Fixed the Windows launcher's Python dependency probe. The previous multiline
  `python -c` probe could be mangled by Windows PowerShell native argument
  passing, causing `.\run-local.ps1 -NoInstall` to report missing Python
  dependencies even when the repo `.venv` had them installed.
- Added process-level `Path`/`PATH` normalization before starting backend and
  frontend child processes. This avoids a Windows `Start-Process` failure when
  the parent environment contains duplicate path keys.

**Validation performed:**

- `cd backend; python -m pytest -q` passed: 510 tests.
- `npm.cmd run lint` passed with warnings only.
- `npm.cmd run build` passed outside the Codex sandbox.
- The documented `sma` no-key offline backtest passed on the local kline
  library.
- `.\run-local.ps1 -NoInstall` now passes dependency detection and reaches
  service startup in the Codex shell. Vite dev startup still requires normal
  filesystem access outside the Codex sandbox, matching the earlier build
  behaviour.

**Runtime impact:** launcher robustness only. No backend engine, strategy,
gateway, execution, dashboard, or configuration behavior changed.

### Phase 7: Submission Readiness Pass

**Why this phase exists:** after validating the local setup path, the remaining
question was whether the repository is clean and easy to submit as a graded
artifact. This phase checks the Git-facing package rather than local generated
state.

**Files changed:**

- `README.md`
- `BRANCH_CHANGES.md`

**What changed compared with `main`:**

- Added a top-level submission handoff checklist covering the repository URL,
  ignored local artifacts, Binance key placement, report-evidence map, and the
  boundary between setup smoke tests and report-grade performance claims.
- Confirmed that local `.env`, virtual environment, downloaded kline data, run
  archives, `node_modules/`, and `dist/` are ignored rather than part of the
  Git submission.
- Confirmed no tracked sensitive files or generated runtime artifacts are
  present in the submission surface.

**Why these changes matter:**

- A reviewer can distinguish what to clone, what to configure locally, and what
  should not be committed.
- The report handoff is explicit: infrastructure evidence is strong, but final
  strategy performance still needs a deliberate dataset and archived results.

**Runtime impact:** documentation-only. No code, scripts, dependencies,
runtime configuration, or dashboard behavior changed.
