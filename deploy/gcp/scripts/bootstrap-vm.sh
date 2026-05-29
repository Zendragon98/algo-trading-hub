#!/usr/bin/env bash
# Bootstrap a Debian/Ubuntu Compute Engine VM for the trading backend.
# Run as root on a fresh VM:
#   curl -fsSL .../bootstrap-vm.sh | sudo bash -s -- --project YOUR_PROJECT --region us-central1
set -euo pipefail

PROJECT_ID=""
REGION="us-central1"
REPO_NAME="algo-trading"
INSTALL_DIR="/opt/algo-trading-hub"
DATA_DIR="/var/lib/algo-trading/data"

usage() {
  echo "Usage: $0 --project PROJECT_ID [--region REGION] [--install-dir PATH]"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

[[ -n "$PROJECT_ID" ]] || usage

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git nginx

# Docker Engine
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable --now docker

# Artifact Registry auth (pull images)
if command -v gcloud >/dev/null 2>&1; then
  gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true
fi

mkdir -p "$DATA_DIR" "$INSTALL_DIR/deploy/gcp"
chown -R root:root "$DATA_DIR"

# Placeholder compose env if missing
if [[ ! -f "$INSTALL_DIR/deploy/gcp/.env" ]]; then
  cp "$INSTALL_DIR/deploy/gcp/env.gcp.example" "$INSTALL_DIR/deploy/gcp/.env" 2>/dev/null || true
  echo "Edit $INSTALL_DIR/deploy/gcp/.env before starting the engine."
fi

# systemd unit
if [[ -f "$INSTALL_DIR/deploy/gcp/systemd/algo-trading.service" ]]; then
  sed "s|/opt/algo-trading-hub|$INSTALL_DIR|g" \
    "$INSTALL_DIR/deploy/gcp/systemd/algo-trading.service" \
    > /etc/systemd/system/algo-trading.service
  systemctl daemon-reload
  systemctl enable algo-trading.service
fi

# NTP (Binance -1021 clock skew)
apt-get install -y -qq chrony || true
systemctl enable --now chrony 2>/dev/null || systemctl enable --now systemd-timesyncd 2>/dev/null || true

echo ""
echo "Bootstrap complete."
echo "  1. Clone or rsync this repo to $INSTALL_DIR"
echo "  2. Configure $INSTALL_DIR/deploy/gcp/.env (secrets, CORS_ORIGINS, IMAGE)"
echo "  3. Build or push image to ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend"
echo "  4. systemctl start algo-trading"
echo "  5. Optional: TLS via deploy/gcp/nginx/algo-trading.conf + certbot"
