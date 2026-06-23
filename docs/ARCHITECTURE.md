# Architecture — canonical references

This file is the **signpost** for system design material. Detailed narrative and embedded diagrams live in the root [`README.md`](../README.md); engine-specific deep dives are in [`../backend/README.md`](../backend/README.md).

---

## Editable diagram sources

All Mermaid sources live in [`../backend/docs/`](../backend/docs/). Treat them as **version-controlled** living architecture: update when behaviour changes.

| Diagram | File |
|---------|------|
| System context | `architecture-system.mmd` |
| Boot / shutdown | `architecture-lifecycle.mmd` |
| Trading path | `architecture-tick.mmd` |
| Event bus | `architecture-events.mmd` |
| Gateway seam | `architecture-gateway.mmd` |
| Position truth | `architecture-data-sync.mmd` |
| Operator controls | `architecture-control.mmd` |
| Frontend data plane | `architecture-frontend.mmd` |
| Parent execution | `architecture-execution.mmd` |
| Strategies & netting | `architecture-strategies.mmd` |
| Circuit breakers | `architecture-breakers.mmd` |
| End-to-end compact | `architecture.mmd` |

**Render:** paste into [mermaid.live](https://mermaid.live) or use a Mermaid-capable doc pipeline (GitLab, Confluence Mermaid, internal docs site).

---

## Component map (summary)

| Component | Path | Responsibility |
|-----------|------|----------------|
| Process entry | `backend/main.py` | EventBus, Engine, uvicorn, signal handling |
| HTTP / WS API | `backend/api/` | FastAPI routes, schemas, WebSocket pump |
| Engine core | `backend/engine/core/engine.py` | Clock, callbacks, reconcile orchestration |
| Market data | `backend/engine/market_data/` | Book, tape, features, quality |
| Strategies | `backend/engine/strategies/` | Signal generation |
| Risk | `backend/engine/risk/` | Pre-trade, breakers, monitors |
| Execution | `backend/engine/execution/`, `orders/` | Router, VWAP wheel, OMS |
| Gateway | `backend/gateways/` | Venue adapters: Binance active, IBKR scaffold |
| Persistence | `backend/engine/persistence/` | JSONL run recorder, WAL |

---

## Architectural constraints (non-functional)

- **Single-writer Engine** per process — see [`OPERATIONS.md`](OPERATIONS.md) for scaling implications.
- **Bounded EventBus queues** — see WebSocket back-pressure notes in [`OPERATIONS.md`](OPERATIONS.md).
- **Venue as source of truth** for positions after reconcile — see [`COMPLIANCE_AND_GOVERNANCE.md`](COMPLIANCE_AND_GOVERNANCE.md) records section.

---

## Security-relevant boundaries

See [`SECURITY.md`](SECURITY.md) for authentication coverage of `/api/control` vs read paths and `/ws`.
