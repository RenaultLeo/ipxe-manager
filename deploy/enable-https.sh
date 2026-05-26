#!/usr/bin/env bash
# Active HTTPS (cert auto-signé + Nginx + SERVER_BASE_URL + firmware iPXE).
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
BASE_URL="https://${HOST}"

echo "==> [1/3] Certificats TLS (OpenSSL)…"
SSL_DIR="${SSL_DIR:-/srv/ipxe/ssl}"
if [ -f "$SSL_DIR/server.crt" ] && command -v openssl >/dev/null 2>&1; then
  if ! openssl x509 -in "$SSL_DIR/server.crt" -noout -text 2>/dev/null | grep -qF "$HOST"; then
    echo "    Certificat serveur sans SAN pour $HOST — renouvellement."
    bash "$SCRIPT_DIR/ipxe-renew-tls-cert.sh" "$HOST"
  else
    bash "$SCRIPT_DIR/generate-tls-cert.sh" "$HOST"
  fi
else
  bash "$SCRIPT_DIR/generate-tls-cert.sh" "$HOST"
fi
chmod 644 "$SSL_DIR/ca.crt" "$SSL_DIR/server.crt" 2>/dev/null || true
chown "${APP_USER:-ipxe}:${APP_USER:-ipxe}" "$SSL_DIR/ca.crt" 2>/dev/null || true

if [ ! -f /srv/ipxe/ssl/server.crt ]; then
  echo "Échec : /srv/ipxe/ssl/server.crt absent." >&2
  exit 1
fi

echo "==> Test TLS 1.2 (doit afficher Verify return code: 0)…"
if ! openssl s_client -connect "${HOST}:443" -servername "$HOST" \
  -CAfile "$SSL_DIR/ca.crt" -tls1_2 </dev/null 2>&1 | grep -qE 'Verify return code: 0|Verification: OK'; then
  echo "ATTENTION : TLS KO depuis ce serveur — corriger avant le boot PXE." >&2
fi

echo "==> [2/3] Nginx HTTPS…"
cp "$SCRIPT_DIR/nginx-https.conf" /etc/nginx/sites-available/ipxe-manager
ln -sf /etc/nginx/sites-available/ipxe-manager /etc/nginx/sites-enabled/ipxe-manager
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

if [ -f "$APP_DIR/.env" ]; then
  if grep -q '^SERVER_BASE_URL=' "$APP_DIR/.env"; then
    sed -i "s|^SERVER_BASE_URL=.*|SERVER_BASE_URL=${BASE_URL}|" "$APP_DIR/.env"
  else
    echo "SERVER_BASE_URL=${BASE_URL}" >> "$APP_DIR/.env"
  fi
fi

if [ -x "$VENV/bin/python" ]; then
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

systemctl restart ipxe-manager ipxe-celery

echo "==> [3/3] Firmware iPXE HTTPS + menus…"
bash "$SCRIPT_DIR/bootstrap-https-firmware.sh" "$HOST"

echo ""
echo "======================================================"
echo "  HTTPS activé"
echo "======================================================"
echo "  Interface : ${BASE_URL}/"
echo "  Menus     : ${BASE_URL}/menus/menu.ipxe"
echo "  Rollback  : sudo bash $SCRIPT_DIR/disable-https.sh $HOST"
echo ""
