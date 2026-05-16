# Operations runbook

This runbook describes how to operate **Algo Trading Hub** in a production-like environment: observability, failure modes, and safe procedures. It assumes the stack described in the root [`README.md`](../README.md) (FastAPI backend + React console + Binance USDT-M Futures gateway).

---

## 1. Deployment topology

### 1.1 Process model (authoritative)

A **single Python process** (`backend/main.py`) runs:

- The trading **Engine** (asyncio).
- **Uvicorn** serving FastAPI on the same event loop.

Implications:

- **Vertical scaling** only for that process (CPU, network, kernel file descriptors).
- **No horizontal replica** of the live engine state without external redesign (single-writer to the venue per API key is typical for this pattern).
- A process crash clears **in-memory** circuit-breaker state; persisted audit lives via [`Run archive`](../backend/README.md#run-archive).

### 1.2 Frontend vs backend

| Mode | Console origin | API |
|------|----------------|-----|
| **Development** | Vite on `:5173` | Proxied to `:8000` for `/api` and `/ws` (see `vite.config.ts`) |
| **Production build** | TanStack Start + Cloudflare Workers (`wrangler.jsonc`) | Must be set explicitly via **`VITE_API_BASE`** (see `src/lib/api.ts`) |

Operators must ensure the browser can reach **`wss:`** when the page is served over **`https:`** (mixed-content rules).

---

## 2. Observability

### 2.1 Health endpoints

| Endpoint | Semantics | Typical use |
|----------|-----------|-------------|
| `GET /health` | Process liveness: `{ "status": "ok" }` | Load-balancer / k8s **liveness** |
| `GET /ready` | Trading readiness gate | **Readiness** only when engine is safe to route traffic (see below) |

**`GET /ready`** (see `backend/api/routes/health.py`) returns:

- `ready`: `true` only if **all** of:
  - `engine.status == running`
  - Last **public** market tick age **&lt; 60 s** (`tick_fresh`)
  - Last **authoritative venue alignment** age **&lt; 120 s** (`user_data_fresh`: user-data WebSocket *or* periodic REST account snapshot; fields `engine.oms.last_venue_truth_ts` / `user_data_age_sec` in `system_health`)
- Diagnostic fields: `engine`, `tick_fresh`, `user_data_fresh`

**Operational note:** During intentional **pause**, the engine is not `running`; `/ready` will be `false`. Use `/health` for “is the API up?” and align orchestration so **readiness** matches your definition of “accepting trading UI sessions.”

### 2.2 System health in the API

`GET /api/state` includes `system_health` (`SystemHealthDTO` in `backend/api/schemas.py`): latency histograms, market-data health, clock skew, tick age, user-data age, reconcile flags, active breakers, notionals, and PnL snapshot fields.

**Golden signals to watch:**

| Signal | Interpretation |
|--------|----------------|
| `user_data_age_sec`, `user_ws_event_age_sec`, `user_data_stale` | **`user_data_age_sec`** — time since last venue-truth sync (WS *or* successful REST reconcile). **`user_ws_event_age_sec`** — silence on user-data WebSocket (can sit high when holding quietly). **`user_data_stale`** trips only with **working** orders and stale WS |
| `tick_age_sec` | Stale public market data — strategies may be vetoed or paused |
| `clock_skew_ms`, `clock_skew_synced` | REST signing vs venue time; `-1021` class failures if unsynced |
| `order_reconcile` | Venue `openOrders` vs OMS drift |
| `active_breakers` | Halt / veto / latched majors |

### 2.3 Logs

- **Stdout / logging**: configured in `backend/common/logging.py`; failures and operator actions must be collectable to your central log platform.
- **Per-run file**: `backend/data/runs/<run-id>/app.log` (rotating, when enabled).
- **JSONL streams**: fills, orders, positions, equity, breakers, optional WAL — see [`Run archive`](../backend/README.md#run-archive).

### 2.4 WebSocket stream

A client **opens a WebSocket** to **`/ws`** (not an HTTP GET). See `backend/api/ws.py`.

**Back-pressure:** subscribers use **bounded queues**; a slow consumer **drops oldest** events. **Authoritative state** is always re-hydrated via `GET /api/state` (the React hook polls on a fixed cadence and on reconnect).

---

## 3. Control plane (operator actions)

REST control under **`/api/control/*`**. When `API_TOKEN` is set, **all** paths under `/api/control` require `Authorization: Bearer <token>` (`backend/api/server.py`).

| Action | Endpoint | Effect (summary) |
|--------|----------|-------------------|
| Start / resume | `POST /api/control/start` | `engine.start()` if stopped, else `resume()` |
| Pause | `POST /api/control/pause` | Stops strategy evaluation; state retained |
| Stop | `POST /api/control/stop` | Optional flatten (`FLATTEN_ON_STOP`); disconnect |
| Flatten | `POST /api/control/flatten` | Pauses, cancels, syncs venue, closes legs — **remains paused** |
| Strategy | `POST /api/control/strategy` | Hot-swap active strategy |
| Risk | `PATCH /api/control/risk` | Updates live `max_risk_pct` |
| Breakers | `GET/POST .../breakers` | Inspect, trip, re-arm |
| Shutdown | `POST /api/control/shutdown` | Process exit when wired in `main.py` |

**Kill vs Halt:** “Kill” shuts down the **process**. “Halt” trips breakers and may flatten — it is the **trading** stop, not necessarily OS-level termination.

---

## 4. Incident response (playbook patterns)

### 4.1 User-data or market data stale

1. Confirm `GET /ready` and `system_health` in `/api/state`.
2. Check venue status and account ListenKey lifecycle (Binance user stream).
3. If stale while positions are open: treat dashboard as **indicative**; venue account/positions are ground truth.
4. If `reconcile_mismatch` or heal events occurred: follow [Position sync](../backend/README.md#position--dashboard-sync) guidance; do not resume aggressive strategies until order + position reconcile are clean.

### 4.2 MAJOR circuit breaker latched

1. Inspect `/api/control/breakers` and `breakers.jsonl` for code + scope.
2. Automatic flatten may have run — confirm flat on venue before re-arm.
3. `POST /api/control/breakers/rearm` only after root cause addressed (see [Failsafes matrix](../backend/README.md#failsafes--circuit-breaker-matrix)).

### 4.3 Process crash mid-session

1. Restart process; review latest `data/runs/<id>/` folder.
2. If `RECOVER_ON_START=true`, WAL replay runs before reconcile — validate OMS/positions against venue before enabling strategies.
3. With default settings, verify orphans: `RECONCILE_CANCEL_ORPHANS` behaviour on startup reconcile.

### 4.4 Exposure cannot be flattened

1. Check venue connectivity, margin, and reduce-only rejects in `app.log`.
2. Use exchange **manual** flatten as last resort; document divergence from engine state.

---

## 5. Capacity and performance

- **REST throttling:** Binance client enforces minimum spacing (`BINANCE_REST_MIN_INTERVAL_MS`); tune under rate-limit pressure.
- **Concurrency:** `MAX_OPEN_PARENTS`, `SUBMIT_RATE_PER_SEC` cap bursts (see `common/config.py`).
- **Universe size :** `SYMBOLS=AUTO` and broad strategies increase WS fan-in and CPU per tick — validate on hardware representative of production.

---

## 6. Backup and retention

| Asset | Location | Recommendation |
|-------|----------|----------------|
| Run archives | `backend/data/runs/` | Replicate to durable object storage; **exclude** from mutable prod disk without backup |
| Config | `backend/common/config.py` + env | Version control for non-secret defaults; secrets in vault |
| WAL / journal | `events.wal.jsonl` in run dir | Required if using `RECOVER_ON_START` |

Define **retention** (e.g. 30/90 days) per your policy. JSONL is suitable for batch analytics (warehouse ingestion).

---

## 7. Suggested production checklist (non-exhaustive)

- [ ] **Network:** Backend only reachable from trusted IPs / mesh / VPN; no public bind on `0.0.0.0` without TLS termination and policy.
- [ ] **Secrets:** `BINANCE_*` and `API_TOKEN` from secret manager; rotate on schedule.
- [ ] **`API_TOKEN`:** Set non-empty in production; enforce strong entropy.
- [ ] **`TRADING_MODE` + endpoints:** `live` **must** use mainnet hosts — engine enforces fail-closed alignment.
- [ ] **Clock:** NTP-synchronised host (Binance `-1021`).
- [ ] **Alerts:** Wire `ALERT_WEBHOOK_URL` (or export logs/metrics to your APM).
- [ ] **Dashboard token:** Understand `VITE_API_TOKEN` is embedded in the **browser bundle** — acceptable only within controlled network per [SECURITY.md](SECURITY.md).

---

## 8. Escalation

Document **internal** escalation (desk → tech → risk) and **exchange** escalation (API support, account freezes) per your organisation. This repository does not provide vendor SLAs.
