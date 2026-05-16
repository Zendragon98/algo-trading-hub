# Backend engineering guide

Conventions for Python changes under `backend/`. For architecture and ops, see [`README.md`](README.md), repo [`docs/`](../docs/), and [`docs/OPERATIONS.md`](../docs/OPERATIONS.md).

---

## Dependencies and layout

- **Import root:** `backend/` (see `pyproject.toml` `pythonpath`). Use `from common...`, `from engine...`, not relative hacks.
- **Imports:** Keep imports at the **top of the file** (project rule). Avoid inline imports except where required for circularity — prefer restructuring.
- **Layers:** `common/` ← `gateways/` + `engine/` ← `api/` + `analytics/`. Cross-cutting messaging: **`EventBus` only**.

## Quality gates

```bash
cd backend
pip install -r requirements.txt
pytest -q
ruff check .
```

- **Ruff:** `line-length = 100`, Python 3.11+ (`pyproject.toml`).
- **Tests:** Async tests with `pytest-asyncio`. Mocks and fakes **only** in `tests/` — never in production paths.

## Trading and safety

- **`TRADING_MODE=live`** must align with **live** venue hosts; the gateway fails closed on sandbox mismatch.
- **Reduce-only** exits bypass entry breakers by design — preserve this when changing risk code.
- **Venue truth:** position and wallet reconciliation beats UI/WebSocket convenience.

## API / schemas

- **`backend/api/schemas.py`** shapes must stay aligned with **`src/components/algo/types.ts`**.
- New public fields: update serializers, tests, and the dashboard types in the same change when possible.

## Documentation

- Large behavioural changes: update **`backend/README.md`** and, if operator-facing, **`docs/OPERATIONS.md`** or **`docs/SECURITY.md`**.

## File size

Keep modules focused (~250 LOC guideline). Split before adding parallel abstractions.
