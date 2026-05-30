"""CORS policy for the dashboard ↔ API split deployment."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.server import create_app
from common.config import Settings
from common.events import EventBus


class _StubEngine:
    settings = Settings()


def _client(*, cors_origins: list[str] | None = None) -> TestClient:
    settings = Settings(cors_origins=cors_origins or ["http://localhost:5173"])
    app = create_app(_StubEngine(), EventBus(), settings)  # type: ignore[arg-type]
    return TestClient(app)


def test_cors_allows_configured_origin() -> None:
    client = _client(cors_origins=["https://algo-trading-hub.vercel.app"])
    res = client.get("/health", headers={"Origin": "https://algo-trading-hub.vercel.app"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "https://algo-trading-hub.vercel.app"


def test_cors_allows_vercel_preview_regex() -> None:
    client = _client()
    origin = "https://algo-trading-hub-git-feature-user.vercel.app"
    res = client.get("/health", headers={"Origin": origin})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == origin


def test_cors_blocks_unknown_origin() -> None:
    client = _client()
    res = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers
