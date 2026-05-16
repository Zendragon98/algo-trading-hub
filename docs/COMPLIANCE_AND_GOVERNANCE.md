# Compliance, governance, and risk disclosure

This document supports **risk management**, **audit trail mapping**, and **organisational governance** for deployments of Algo Trading Hub. It does **not** constitute legal, regulatory, or investment advice.

---

## 1. Scope and limitations

### 1.1 What this software is

- An **engineering reference / operator console** for algorithmic trading against **Binance USDT-M Futures** (extendable via `GatewayInterface`).
- A **single-process** asyncio application combining strategy execution, order management scaffolding, and a web API (see [`OPERATIONS.md`](OPERATIONS.md)).

### 1.2 What this software is not

- **Not** a certified **best-execution** or **order management system** in the regulatory sense of your jurisdiction.
- **Not** validated for **SEC / CFTC / FCA / MAS / MiFID II** requirements unless **you** complete gap analysis, testing, and sign-off.
- **Not** a guarantee of profitability, liquidity, or venue availability.
- **Not** providing **investment advice**.

**Your organisation** bears sole responsibility for fit-for-purpose determination, model risk, operational risk, and regulatory filings.

---

## 2. Records management (audit trail)

### 2.1 Primary artefacts

| Artefact | Location | Use in audit |
|----------|----------|--------------|
| Run manifest | `data/runs/<id>/manifest.json` | Session identity, started-at |
| Event JSONL | `fills.jsonl`, `orders.jsonl`, `positions.jsonl`, etc. | Reconstruct trading activity |
| Breakers | `breakers.jsonl` | Safety trips and operator halts |
| WAL | `events.wal.jsonl` (optional) | Full bus replay when enabled |
| Application log | `app.log` | Human-readable diagnostics |

Ensure **WORM** or **immutable backup** policies if your policy requires non-repudiation.

### 2.2 Gaps to disclose internally

- **In-memory** breaker state is lost on process restart (documented in backend README).
- **WebSocket** consumers may miss events (UI mitigates via polling — document your “source of truth” policy).
- **Clock skew** can affect signing and staleness checks — NTP discipline is mandatory.

---

## 3. Model risk and strategy disclosure

Strategies ship in-tree for demonstration and extension:

- **Pairs trading** — statistical basis on implied stablecoin leg; **parameter sensitivity** (z thresholds, volume weights) must be validated.
- **SMA crossover** — trend-following; **whipsaw** and regime risk.
- **Market making** — inventory and adverse-selection risk; optional fade vs follow modes.

**Governance expectation:** define **approval** for parameter changes (`common/config.py` vs env), backtesting independence, and **kill-switch** authority (who may `POST /control/breakers/trip`).

---

## 4. Change management (suggested RACI)

| Activity | Responsible | Accountable | Consulted | Informed |
|----------|-------------|-------------|-----------|----------|
| Strategy parameter release | Quant dev | Head of desk | Risk | Compliance |
| Production deploy | Platform | CTO / IT | Security | Trading |
| API key rotation | Security | CISO | Ops | Trading |
| Incident / flatten | Ops lead | Risk manager | Legal (if material) | Management |

Adapt to your org chart; this table is **illustrative**.

---

## 5. Data classification (template)

Classify the following under your enterprise scheme:

| Data | Typical classification |
|------|-------------------------|
| API keys / secrets | **Confidential** / restricted |
| Positions / PnL / logs | **Confidential** |
| Public market data in logs | Internal / low (still commercially sensitive in aggregate) |

Encrypt **at rest** for run archives if policy requires (filesystem encryption or object storage with SSE).

---

## 6. Regulatory mapping (non-exhaustive prompts)

Use these as **conversation starters** with compliance — not as checkboxes implying conformance:

- **Market abuse / manipulation:** surveillance remains your obligation; this code does not replace surveillance systems.
- **Best execution:** record **arrival price vs VWAP** (`ExecutionTracker`) supports TCA narratives; policies differ by regulator.
- **Client assets / custody:** not applicable to direct exchange API trading in typical crypto perpetual setups — confirm with counsel.
- **Recordkeeping:** map JSONL + logs to your **books and records** rule equivalents.

---

## 7. Third parties

- **Binance** (or successor venue): terms of use, API rules, jurisdictional availability — **your** compliance team must approve.
- **Cloudflare Workers** (if using built frontend): data processing and subprocessors per Cloudflare DPA.
- **Supabase / other MCP tools** in developer environments: **out of scope** for core trading path unless explicitly integrated.

---

## 8. Sign-off template (internal)

```
System: Algo Trading Hub  Repository commit: ________________
Environment: [ ] paper testnet  [ ] production mainnet
Risk review date: ________  Approver: ________
Known limitations acknowledged: [ ] single-process  [ ] WS unauthenticated
                                 [ ] client-bundled API token
Independent security review: [ ] not required  [ ] completed (ref: _____)
```

---

## 9. Disclaimer

Software is provided **as-is** for engineering purposes. Trading digital asset derivatives involves **substantial risk of loss**. Past simulated performance does not guarantee future results.
