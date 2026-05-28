#!/usr/bin/env bash
set -euo pipefail

# Clean serveur "safe" (pas destructif sur .env/ipxe.db)
# - purge caches Python
# - supprime artefacts build locaux
# - réaligne permissions app
# - reseed DB (idempotent)
# - redémarre services
#
# Usage:
#   sudo bash deploy/clean-server-state.sh

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
APP_USER="${APP_USER:-ipxe}"
VENV="${VENV:-/srv/ipxe/venv}"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/clean-server-state.sh" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Missing APP_DIR: $APP_DIR" >&2
  exit 1
fi

echo "==> Cleaning Python cache files"
find "$APP_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$APP_DIR" -type f -name "*.pyc" -delete

echo "==> Cleaning local build artifacts"
rm -rf \
  "$APP_DIR/.pytest_cache" \
  "$APP_DIR/.mypy_cache" \
  "$APP_DIR/build" \
  "$APP_DIR/dist" 2>/dev/null || true

echo "==> Reset ownership on app dir"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" 2>/dev/null || true

echo "==> DB seed/migrations (idempotent)"
if [[ -x "$VENV/bin/python" ]]; then
  sudo -u "$APP_USER" "$VENV/bin/python" "$APP_DIR/deploy/seed_db.py" || true
else
  echo "  [WARN] $VENV/bin/python not found; skip seed_db.py"
fi

echo "==> Restart services"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa

echo "[OK] clean-server-state done"
