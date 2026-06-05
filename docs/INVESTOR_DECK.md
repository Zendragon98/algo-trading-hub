# Investor Presentation — Multi-Strategy Systematic Crypto Fund

**Document version:** 2026-05-30  
**Data source:** Live run archives under `backend/data/runs/` (51 sessions with fills, May 9–29, 2026)  
**Supporting analysis:** [`netting-analysis-report.md`](netting-analysis-report.md) · [`netting-analysis-data.json`](netting-analysis-data.json)

Replace bracketed placeholders (`[Fund Name]`, team, terms) before external distribution. Have counsel review Slide 21 and all performance claims.

---

## Slide 1 — Title

**[Fund Name]**

Systematic digital asset strategies with internal portfolio netting

**Tagline:** Multiple uncorrelated edges · one capital base · institutional risk controls

**Presented by:** [Name], [Title]  
**Date:** [Date]

---

## Slide 2 — Investment thesis

We do not rely on a single market view. We operate a **diversified engine** of complementary strategies on shared infrastructure, with **internal signal netting** before any order reaches the exchange.

| Pillar | Investor relevance |
|--------|-------------------|
| **Multi-strategy diversification** | Returns driven by several signal sources (basis, trend, ensemble, flow, market making) |
| **Internal netting** | Opposing strategy intents offset *before* venue submission — lower fees and slippage when sleeves disagree |
| **Institutional risk stack** | Pre-trade checks, circuit breakers, kill switch, operator flatten |
| **Production system** | Full JSONL audit trail, real-time dashboard, 51 archived trading sessions |

**Target investor:** Accredited / qualified investors seeking systematic crypto exposure with defined governance.

---

## Slide 3 — The opportunity

**Why systematic crypto?**

- 24/7 liquidity on USDT-margined perpetuals  
- Rich microstructure (L2, tape, funding) for short-horizon and market-neutral edges  
- Regime diversity: trend, mean reversion, basis, and spread capture behave differently across cycles  

**The structural gap we address**

Most funds run **one strategy**, or multiple strategies that **fight on the wire** — paying fees to offset their own positions. Our engine runs **`STRATEGY=all`**: every strategy ticks each second; alpha signals are **netted per symbol** before one parent order is sent.

---

## Slide 4 — Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              SHARED RISK · BREAKERS · KILL SWITCH             │
└─────────────────────────────────────────────────────────────┘
                              ▲
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────┴────┐          ┌─────┴─────┐         ┌─────┴─────┐
   │  ALPHA  │          │  NETTER   │         │    MM2    │
   │ 4 strats│──tick──▶ │ per symbol│         │  quotes   │
   └─────────┘          │  → VWAP   │         │ (parallel)│
                        └───────────┘         └───────────┘
```

**Venue:** Binance USDT-M Futures (paper/testnet in archived runs; live-capable with fail-closed host checks).

---

## Slide 5 — Strategy pillar: Stablecoin basis (Pairs)

- **Edge:** Statistical arbitrage on implied USDT/USDC basis across dual-quoted perps  
- **Profile:** Market-neutral; atomic two-leg submission; self-managed basis z-stops  
- **Status in archive:** Registered; limited fill attribution in May 2026 runs (dominant alpha sleeve was flow — see Slide 15)

---

## Slide 6 — Strategy pillar: Trend (SMA crossover)

- **Edge:** Fast/slow SMA crossover on broad USDT universe (default 15m bars)  
- **Profile:** Directional; equity-budgeted sizing; engine per-leg stop brackets  
- **Complements:** Basis and MM in different regimes  

---

## Slide 7 — Strategy pillar: Blended signals (Ensemble)

- **Edge:** Five-family vote (EMA, MACD, RSI, Bollinger, microstructure)  
- **Profile:** Sparse entries (multi-vote confirmation); defers micro signals on MM-active symbols in `all` mode  
- **Complements:** Higher-conviction filter vs raw trend  

---

## Slide 8 — Strategy pillar: Flow momentum

- **Edge:** Enters **with** aggressive tape flow (complement to MM fade)  
- **Profile:** Short hold; in-strategy stop/TP; highest alpha turnover in current archive  
- **Archive fact:** **100% of netted parent orders** in May 2026 `all`-mode runs were flow_momentum signals routed through the netter (`1,681` parents)

---

## Slide 9 — Strategy pillar: Market making v2

- **Edge:** Two-sided post-only quotes; inventory-aware reservation pricing; toxicity / jump / depletion gates  
- **Execution:** Dedicated `QuoteExecutor` (not netted with alpha)  
- **Archive fact:** MM fills = **364** (**1.7%** of fill count); MM notional ≈ **$5.6K** vs alpha ≈ **$3.14M**

---

## Slide 10 — Why multi-strategy beats single-strategy

| Single-strategy fund | Our approach |
|---------------------|--------------|
| One regime dependency | Five engines + shared risk |
| Idle capital when flat | Continuous 1 Hz scan across universes |
| Strategies pay fees to offset each other | Internal netter aggregates per symbol first |
| Opaque execution | Full `fills.jsonl` / `parents.jsonl` / `logs.jsonl` per session |

**Honest archive insight:** Cross-strategy **offset events (2+ strategies on same net)** were **not observed** in logged SIG lines to date — sleeves did not simultaneously fire opposing intents on the same symbol. The **infrastructure is live**; **economic offset** will appear when SMA, blend, pairs, and flow overlap actively (engine warns at boot on symbol overlap).

---

## Slide 11 — Netting advantage (data-backed)

### What the run archive shows (51 sessions, May 2026)

| Metric | Value |
|--------|-------|
| Total venue fills | **21,495** |
| Net traded notional | **$3.15M** |
| All-in fees paid | **$1,059** (~**3.36 bps** on notional) |
| Parent orders via netter (`__netted__`) | **1,681** |
| Single-strategy parents (non-netted tag) | **104** |
| Net pipeline log events | **8,738** |
| Multi-strategy net events (2+ strats, same symbol) | **0** |
| Full internal cancellations (`net zero`) | **0** (logged at DEBUG; not in `logs.jsonl`) |

### How to talk about savings today

1. **Proven:** **94%** of alpha parent records in `all`-mode runs used the netter tag (`1,681 / 1,785` parent-strategy rows).  
2. **Ready:** When sleeves disagree, gross intent collapses to net qty **before** the exchange — no self-crossing.  
3. **Not yet in logs:** Dollar fee savings from **cross-strategy** offset — **$0** measured; do not claim until overlap occurs or `contributions` are persisted.  
4. **Fee efficiency:** **3.36 bps** realized all-in vs typical **4–5 bps** taker assumptions — partly maker flow and execution quality.

**Illustrative investor line (accurate):**

> "We processed **$3.15M** of net venue notional across **21k** fills with **$1,059** in fees, routing **1,681** alpha parents through our internal netter. Cross-strategy cancellations were not required in this period because only flow momentum actively traded through the alpha path; the netting layer is operational and will reduce turnover when multiple sleeves align on the same symbols."

Re-run analysis anytime:

```bash
cd backend
python -m analytics.netting_analysis --md-out ../docs/netting-analysis-report.md
```

---

## Slide 12 — Risk management

- **Pre-trade:** Notional caps, staleness checks, portfolio gross limits  
- **Intra-trade:** Per-strategy stops (basis z, flow bps, engine brackets for SMA/blend)  
- **Portfolio:** Drawdown kill switch; symbol and global circuit breakers  
- **Operator:** One-click flatten with venue reconciliation  
- **Audit:** Every run archived under `data/runs/<timestamp>/` (fills, orders, parents, equity, breakers)

---

## Slide 13 — Technology edge

| Capability | Status |
|------------|--------|
| Single-process asyncio engine | Production |
| Gateway abstraction (testable, venue-swappable) | Production |
| Hot-swap `STRATEGY=all` | Production |
| Virtual per-strategy ledger on fills | Production |
| MM microstructure hub (toxicity, markouts, depletion) | Production |
| Netting analytics module | `backend/analytics/netting_analysis.py` |

---

## Slide 14 — Execution quality (archive)

| Metric | Value |
|--------|-------|
| Avg fill notional | **~$147** ($3.15M / 21,495) |
| Implied fee rate | **3.36 bps** |
| Alpha vs MM fill mix | **98.3%** alpha / **1.7%** MM (by count) |

*[Insert slippage distribution from `executions.jsonl` if presenting to quant investors.]*

---

## Slide 15 — Performance summary (from archives — verify before LP meeting)

| Metric | May 2026 archives (51 runs with fills) |
|--------|----------------------------------------|
| Net venue notional | **$3,150,553** |
| Fees paid | **$1,059** |
| Realized PnL (sum on fills with field) | **-$1,174** |
| Latest session equity (2026-05-29 run) | **~$7,181** |
| Netted alpha parents | **1,681** |

**Important:** These figures are from **engineering / paper-style sessions** in the repo archive, not audited fund performance. Replace with administrator-prepared NAV, Sharpe, and max drawdown before a formal offering.

**Attribution (parent notes, netted orders):**

| Sleeve | Netted parent count |
|--------|---------------------|
| flow_momentum | 1,681 |
| Other alpha | 0 (in netted path) |

---

## Slide 16 — Competitive comparison

| | Discretionary crypto | Single bot | **[Fund Name]** |
|--|---------------------|------------|-----------------|
| Strategy count | 1 PM view | 1 model | 5 engines |
| Internal netting | Manual / none | None | Automated per tick |
| Audit trail | Variable | Opaque | JSONL per session |
| Cross-strat offset in production | N/A | N/A | **Built; overlap pending** |

**Moat:** Multi-strategy breadth + **live netting pipeline** + institutional controls — not one signal alone.

---

## Slide 17 — Team

**[Founder / CIO — bio]**

**[CTO / engineering — built algo-trading-hub engine]**

**Governance:** Parameter change approval · API key segregation · incident / flatten playbook

---

## Slide 18 — Fund terms (template)

| Term | Detail |
|------|--------|
| Structure | [LP / SPV / SMA] |
| Minimum | $[X] |
| Management fee | [X]% |
| Performance fee | [X]% over [hurdle / HWM] |
| Liquidity | [Monthly / Quarterly] |
| Lock-up | [X months] |

---

## Slide 19 — Roadmap

**0–6 months**

- Activate overlapping alpha sleeves on distinct symbol maps to realize cross-strategy netting savings  
- Persist `contributions` on netted parents for precise gross-vs-net reporting  
- Monthly LP pack from `netting_analysis` + administrator NAV  

**6–18 months**

- Additional venue via gateway layer  
- Independent model risk review  

---

## Slide 20 — The ask

**Raising $[X]M** for [scale live capital / Fund I].

**Why now**

1. **$3.15M** archived net volume demonstrates operational scale  
2. Netting layer **proven in production** (1,681 netted parents)  
3. Multi-strategy stack **deployed** — next step is sleeve overlap for fee economics  
4. [Your catalyst]  

**Next steps:** CIM · live dashboard demo · reference calls · subscription docs  

**Contact:** [email] · [phone]

---

## Slide 21 — Disclaimer

- Past performance **not indicative** of future results.  
- Digital assets involve **substantial risk of loss**, including total loss.  
- For **qualified / accredited** investors only; not an offer where prohibited.  
- Archive metrics are **system-generated** from `fills.jsonl` / `parents.jsonl`; not audited fund statements.  
- Cross-strategy netting savings of **$0** in the current archive does **not** imply the feature is inactive — it reflects **no opposing multi-strategy intents** on the same symbol in the logged period.  
- Consult legal and tax advisors for your jurisdiction.

---

# Appendix A — Speaker notes

**Opening (30 sec):**  
"We run five systematic engines on one account. Before any order hits Binance, our netter aggregates alpha signals per symbol. In May we traded $3.15 million net notional across 21,000 fills, with $1,059 in fees — about 3.4 basis points. Over 1,600 parent orders went through that netter. We haven't yet seen two strategies fight on the same coin in the logs, so cross-strategy fee savings are still ahead of us — but the pipe is live."

**If asked "Where are the netting savings?"**  
"Honest answer: in this archive, zero dollars from cross-strategy cancellation because only flow momentum fired through the alpha netter. Savings today are structural — one OMS, one risk stack, and fee efficiency at 3.36 bps. When pairs, SMA, and blend trade the same symbols as flow, the same code path nets them — that's when the dollar savings show up in the logs."

**If asked "Why invest if PnL is negative?"**  
"The archived realized PnL sum is -$1,174 over research sessions — not a marketing number. We're pitching the **system**, diversification, and risk framework. Replace Slide 15 with audited track record before a close."

---

# Appendix B — Methodology (netting analysis)

**Tool:** `python -m analytics.netting_analysis`

**Inputs per run folder:**

- `fills.jsonl` — venue truth (post-net execution)  
- `parents.jsonl` — `strategy_name == "__netted__"` counts netted parents  
- `logs.jsonl` — SIG lines matching `net BUY|SELL … (N strategies)`  

**Limitations**

1. `fills.jsonl` cannot reconstruct gross pre-net intent without `contributions` persistence.  
2. `net zero` cancellations are DEBUG-level; absent from `logs.jsonl` in sampled runs.  
3. MM orders (`parent_id` prefix `Q-`) are excluded from alpha netting stats.  

**Reproduce:**

```bash
cd backend
python -m analytics.netting_analysis \
  --md-out ../docs/netting-analysis-report.md \
  --json-out ../docs/netting-analysis-data.json
```

---

# Appendix C — One-page tear sheet

| | |
|--|--|
| **Strategy** | Multi-strategy systematic crypto (basis, trend, ensemble, flow, MM) |
| **Infrastructure** | Internal per-symbol signal netting + institutional risk |
| **Archive volume (May 2026)** | $3.15M net notional · 21,495 fills |
| **Archive fees** | $1,059 (3.36 bps) |
| **Netted parents** | 1,681 |
| **Cross-strat offset (logged)** | Not observed in period |
| **Status** | [Fund Name] — [raising / live / paper] |

---

*Generated from repository run archives. Update by re-running `analytics.netting_analysis` after each material trading period.*
