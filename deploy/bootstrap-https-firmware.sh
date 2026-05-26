#!/usr/bin/env bash
# Clone iPXE + compile firmware HTTPS + régénère les menus (post-install / enable-https).
# Usage : sudo bash deploy/bootstrap-https-firmware.sh [IP_ou_FQDN]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 [IP_ou_FQDN]" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
APP_USER="${APP_USER:-ipxe}"
HOST="${1:-$(hostname -I | awk '{print $1}')}"
BASE_URL="https://${HOST}"

if [ -z "$HOST" ]; then
  echo "IP/FQDN requis." >&2
  exit 1
fi

if [ ! -f "$VENV/bin/python" ]; then
  echo "Virtualenv absent : $VENV" >&2
  exit 1
fi

echo "==> Compilation firmware iPXE (HTTPS, TRUST=ca.crt)…"
echo "    Menu : ${BASE_URL}/menus/menu.ipxe"
echo "    (10–25 min selon la machine)"

# Compilation (root ou ipxe) : doit pouvoir lire /srv/ipxe/ssl/ca.crt
if [ "$(id -u)" -eq 0 ] && [ -r /srv/ipxe/ssl/ca.crt ]; then
  chmod 644 /srv/ipxe/ssl/ca.crt /srv/ipxe/ssl/server.crt 2>/dev/null || true
  chown "$APP_USER:$APP_USER" /srv/ipxe/ssl/ca.crt 2>/dev/null || true
fi
sudo -u "$APP_USER" env HOME=/srv/ipxe \
  "$VENV/bin/python" "$APP_DIR/deploy/compile_ipxe_firmware.py" \
  --menu-url "${BASE_URL}/menus/menu.ipxe"

echo "==> Régénération des menus iPXE…"
cd "$APP_DIR"
"$VENV/bin/python" - <<PY
from app.database import SessionLocal
from app.config import persist_server_base_url, sync_settings_server_base_url_from_db
from app.services.menu_generator import regenerate_all

db = SessionLocal()
try:
    persist_server_base_url(db, "${BASE_URL}")
    sync_settings_server_base_url_from_db()
    n = regenerate_all(db)
    print(f"  {n} fichier(s) menu régénéré(s).")
finally:
    db.close()
PY

echo "OK — firmware HTTPS + menus prêts."
