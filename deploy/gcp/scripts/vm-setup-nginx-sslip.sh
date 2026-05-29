#!/bin/bash
# Public HTTPS for the API using sslip.io (no custom domain required).
# Usage on VM: sudo bash vm-setup-nginx-sslip.sh [external-ip-with-dashes.sslip.io]
set -euo pipefail

EXTERNAL_IP=$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip)
HOST="${1:-${EXTERNAL_IP//./-}.sslip.io}"
ENV_FILE=/opt/algo-trading-hub/deploy/gcp/.env
VERCEL_ORIGIN="${VERCEL_ORIGIN:-https://algo-trading-hub.vercel.app}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx

mkdir -p /var/www/certbot

python3 <<PY
import re
from pathlib import Path

path = Path("$ENV_FILE")
text = path.read_text()
origin = "$VERCEL_ORIGIN"
if re.search(r"^CORS_ORIGINS=", text, re.M):
    text = re.sub(r"^CORS_ORIGINS=.*$", f"CORS_ORIGINS={origin}", text, flags=re.M)
else:
    text += f"\nCORS_ORIGINS={origin}\n"
path.write_text(text)
PY

# HTTP-only bootstrap config for certbot
cat > /etc/nginx/sites-available/algo-trading <<EOF
server {
    listen 80;
    server_name ${HOST};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
    }
}
EOF

ln -sf /etc/nginx/sites-available/algo-trading /etc/nginx/sites-enabled/algo-trading
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

certbot --nginx -d "${HOST}" --non-interactive --agree-tos --register-unsafely-without-email --redirect

# Add WebSocket location to the certbot-managed SSL server block
python3 <<'PY'
from pathlib import Path
import re
path = Path("/etc/nginx/sites-enabled/algo-trading")
text = path.read_text()
if "/ws" in text:
    raise SystemExit(0)
ws = '''
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
'''
text = text.replace("location / {", ws + "\n    location / {", 1)
path.write_text(text)
PY

nginx -t
systemctl reload nginx

cd /opt/algo-trading-hub/deploy/gcp
docker compose up -d

echo "API_URL=https://${HOST}"
curl -sf "https://${HOST}/health" && echo ""
