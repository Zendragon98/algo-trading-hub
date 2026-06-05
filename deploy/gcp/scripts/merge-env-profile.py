#!/usr/bin/env python3
"""Merge env.gcp.example into deploy/gcp/.env, preserving secrets."""
from __future__ import annotations

from pathlib import Path

INSTALL = Path("/opt/algo-trading-hub/deploy/gcp")
ENV_PATH = INSTALL / ".env"
EXAMPLE = Path("/tmp/env.gcp.example")

PRESERVE = {
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "API_TOKEN",
    "CORS_ORIGINS",
    "IMAGE",
    "DATA_DIR",
    "ENGINE_AUTOSTART",
}


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def main() -> None:
    if not ENV_PATH.is_file():
        raise SystemExit(f"missing {ENV_PATH}")
    if not EXAMPLE.is_file():
        raise SystemExit(f"missing {EXAMPLE}")

    current = parse(ENV_PATH.read_text(encoding="utf-8", errors="replace"))
    example = parse(EXAMPLE.read_text(encoding="utf-8", errors="replace"))

    for key, val in example.items():
        if key in PRESERVE and current.get(key) and not str(current[key]).startswith("generate"):
            continue
        if key in {"BINANCE_API_KEY", "BINANCE_API_SECRET"} and current.get(key):
            continue
        if key == "IMAGE" and "YOUR_PROJECT" in val:
            val = "us-central1-docker.pkg.dev/perfect-entry-497811-v1/algo-trading/backend:latest"
        current[key] = val

    if not current.get("IMAGE"):
        current["IMAGE"] = (
            "us-central1-docker.pkg.dev/perfect-entry-497811-v1/algo-trading/backend:latest"
        )

    ENV_PATH.write_text(
        "\n".join(f"{k}={v}" for k, v in current.items()) + "\n",
        encoding="utf-8",
    )
    print(f"merged {len(current)} keys; STRATEGY={current.get('STRATEGY')}")


if __name__ == "__main__":
    main()
