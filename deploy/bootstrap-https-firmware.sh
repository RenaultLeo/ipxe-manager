#!/usr/bin/env bash
# Clone iPXE + compile firmware HTTPS + régénère les menus (post-install / enable-https).
# Usage : sudo bash deploy/bootstrap-https-firmware.sh [IP_ou_FQDN]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 [IP_ou_FQDN]" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
DATA_DIR="${DATA_DIR:-/srv/ipxe}"
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

sudo -u "$APP_USER" env HOME=/srv/ipxe \
  "$VENV/bin/python" "$APP_DIR/deploy/compile_ipxe_firmware.py" \
  --menu-url "${BASE_URL}/menus/menu.ipxe"

echo "==> Régénération des menus iPXE (utilisateur ${APP_USER})…"
cd "$APP_DIR"
sudo -u "$APP_USER" env HOME=/srv/ipxe \
  "$VENV/bin/python" - <<PY
from app.database import SessionLocal
from app.config import persist_server_base_url, sync_settings_server_base_url_from_db, settings
from app.services.filesystem_perms import prepare_menus_dir
from app.services.menu_generator import regenerate_all

db = SessionLocal()
try:
    persist_server_base_url(db, "${BASE_URL}")
    sync_settings_server_base_url_from_db()
    prepare_menus_dir(settings.menus_dir)
    n = regenerate_all(db)
    print(f"  {len(n)} fichier(s) menu régénéré(s).")
finally:
    db.close()
PY

chown -R "$APP_USER:$APP_USER" "$DATA_DIR/http/menus" 2>/dev/null || true
chmod -R o+rX "$DATA_DIR/http/menus" 2>/dev/null || true

echo "OK — firmware HTTPS + menus prêts."
