#!/usr/bin/env bash
# Active HTTPS (cert auto-signé + Nginx + SERVER_BASE_URL).
# Usage : sudo bash deploy/enable-https.sh [IP_ou_FQDN]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 [IP_ou_FQDN]" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
HOST="${1:-$(hostname -I | awk '{print $1}')}"

if [ -z "$HOST" ]; then
  echo "IP/FQDN requis." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> [1/4] Certificats TLS (OpenSSL)…"
bash "$SCRIPT_DIR/generate-tls-cert.sh" "$HOST"

if [ ! -f /srv/ipxe/ssl/server.crt ]; then
  echo "Échec : /srv/ipxe/ssl/server.crt absent." >&2
  exit 1
fi

echo "==> [2/4] Nginx HTTPS…"
cp "$SCRIPT_DIR/nginx-https.conf" /etc/nginx/sites-available/ipxe-manager
ln -sf /etc/nginx/sites-available/ipxe-manager /etc/nginx/sites-enabled/ipxe-manager
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> [3/4] SERVER_BASE_URL → https://${HOST}…"
BASE_URL="https://${HOST}"
if [ -f "$APP_DIR/.env" ]; then
  if grep -q '^SERVER_BASE_URL=' "$APP_DIR/.env"; then
    sed -i "s|^SERVER_BASE_URL=.*|SERVER_BASE_URL=${BASE_URL}|" "$APP_DIR/.env"
  else
    echo "SERVER_BASE_URL=${BASE_URL}" >> "$APP_DIR/.env"
  fi
fi

if [ -x "$VENV/bin/python" ] && [ -f "$APP_DIR/deploy/seed_db.py" ]; then
  cd "$APP_DIR"
  "$VENV/bin/python" - <<PY
from app.database import SessionLocal
from app.config import persist_server_base_url
db = SessionLocal()
try:
    persist_server_base_url(db, "${BASE_URL}")
    print("  BDD + .env : SERVER_BASE_URL=${BASE_URL}")
finally:
    db.close()
PY
fi

echo "==> [4/4] Redémarrage services…"
systemctl restart ipxe-manager ipxe-celery

echo ""
echo "======================================================"
echo "  HTTPS activé"
echo "======================================================"
echo "  Interface : ${BASE_URL}/"
echo "  Menus     : ${BASE_URL}/menus/menu.ipxe"
echo ""
echo "  IMPORTANT — firmware iPXE :"
echo "    1. Ouvrir ${BASE_URL}/firmware"
echo "    2. Lancer « Compiler » (patch DOWNLOAD_PROTO_HTTPS + CERT/TRUST=ca.crt)"
echo "    3. Régénérer les menus iPXE (ISOs ou page Menus)"
echo ""
echo "  Rollback HTTP : sudo bash $SCRIPT_DIR/disable-https.sh"
echo ""
