#!/usr/bin/env bash
# Désactive HTTPS — revient à nginx HTTP seul (rollback rapide).
# Usage : sudo bash deploy/disable-https.sh [IP_ou_FQDN_pour_SERVER_BASE_URL]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
HOST="${1:-$(hostname -I | awk '{print $1}')}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Nginx HTTP…"
cp "$SCRIPT_DIR/nginx.conf" /etc/nginx/sites-available/ipxe-manager
nginx -t
systemctl reload nginx

BASE_URL="http://${HOST}"
if [ -f "$APP_DIR/.env" ]; then
  sed -i "s|^SERVER_BASE_URL=.*|SERVER_BASE_URL=${BASE_URL}|" "$APP_DIR/.env" 2>/dev/null || true
fi

if [ -x "$VENV/bin/python" ]; then
  cd "$APP_DIR"
  "$VENV/bin/python" - <<PY
from app.database import SessionLocal
from app.config import persist_server_base_url
db = SessionLocal()
try:
    persist_server_base_url(db, "${BASE_URL}")
finally:
    db.close()
PY
fi

systemctl restart ipxe-manager ipxe-celery

echo "OK — HTTP restauré (${BASE_URL}). Recompilez le firmware si vous aviez activé HTTPS."
