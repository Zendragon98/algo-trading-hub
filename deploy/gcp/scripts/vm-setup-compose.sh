#!/bin/bash
set -euo pipefail
INSTALL=/opt/algo-trading-hub/deploy/gcp
IMAGE=us-central1-docker.pkg.dev/perfect-entry-497811-v1/algo-trading/backend:latest

mkdir -p "$INSTALL"
cp /tmp/docker-compose.yml /tmp/env.gcp.example "$INSTALL/"
cp "$INSTALL/env.gcp.example" "$INSTALL/.env"

python3 <<'PY'
import re
from pathlib import Path

install = Path("/opt/algo-trading-hub/deploy/gcp")
env_path = install / ".env"
text = env_path.read_text()

overrides = {
    "IMAGE": "us-central1-docker.pkg.dev/perfect-entry-497811-v1/algo-trading/backend:latest",
    "DATA_DIR": "/var/lib/algo-trading/data",
    "CORS_ORIGINS": "https://placeholder.vercel.app",
    "ENGINE_AUTOSTART": "false",
}

backend = Path("/tmp/backend.env")
if backend.exists():
    raw = backend.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key in {
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "BINANCE_TESTNET",
            "TRADING_MODE",
            "API_TOKEN",
            "ENGINE_AUTOSTART",
            "CORS_ORIGINS",
        }:
            overrides[key] = val

if not overrides.get("API_TOKEN") or overrides["API_TOKEN"].startswith("generate"):
    import secrets
    overrides["API_TOKEN"] = secrets.token_hex(32)

def set_kv(content: str, key: str, value: str) -> str:
    pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pat.search(content):
        return pat.sub(line, content)
    return content.rstrip() + "\n" + line + "\n"

for k, v in overrides.items():
    text = set_kv(text, k, v)

env_path.write_text(text)
PY

chmod 600 "$INSTALL/.env"
rm -f /tmp/backend.env

gcloud auth configure-docker us-central1-docker.pkg.dev --quiet 2>/dev/null || true
cd "$INSTALL"
docker compose pull
docker compose up -d
sleep 10
curl -s http://127.0.0.1:8000/health
echo ""
grep '^API_TOKEN=' "$INSTALL/.env" | sed 's/=.*/=***REDACTED***/'
