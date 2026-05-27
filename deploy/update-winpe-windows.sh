#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/ipxe/app"
VENV_PY="/srv/ipxe/venv/bin/python"
APP_USER="ipxe"

echo "==> [1/6] Aller dans le repo"
cd "$APP_DIR"

echo "==> [2/6] Recuperer le dernier code"
git pull --ff-only

echo "==> [3/6] Stopper les services"
sudo systemctl stop ipxe-manager ipxe-celery

echo "==> [4/6] Migrations DB + seed OS"
sudo -u "$APP_USER" "$VENV_PY" -c "from app.database import init_db; init_db()"
sudo -u "$APP_USER" "$VENV_PY" deploy/seed_db.py

echo "==> [5/6] Redemarrer les services"
sudo systemctl start ipxe-manager ipxe-celery

echo "==> [6/6] Regenerer les menus iPXE"
sudo -u "$APP_USER" "$VENV_PY" - <<'PY'
from app.database import SessionLocal
from app.services.menu_generator import regenerate_all

db = SessionLocal()
try:
    regenerate_all(db)
finally:
    db.close()
print("menus regenerated")
PY

echo "==> Termine."
