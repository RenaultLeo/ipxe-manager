#!/usr/bin/env bash
# Mise à jour WinPE / Windows (modes windows_mode + winpe_mode, scripts PS, masters globaux).
# Pas de migration WinPE : supprimer les ISO WinPE avant si repartir de zero.
#
# Usage : sudo bash /srv/ipxe/app/deploy/update-winpe-windows.sh
set -euo pipefail

APP_DIR="/srv/ipxe/app"
VENV_PY="/srv/ipxe/venv/bin/python"
APP_USER="ipxe"

echo "==> [1/7] Aller dans le repo"
cd "$APP_DIR"

echo "==> [2/7] Recuperer le dernier code"
git pull --ff-only

echo "==> [3/7] Dependances Python"
/srv/ipxe/venv/bin/pip install -q --upgrade -r requirements.txt

echo "==> [4/7] Stopper les services"
sudo systemctl stop ipxe-manager ipxe-celery

echo "==> [5/7] Migrations DB + seed OS"
sudo -u "$APP_USER" "$VENV_PY" -c "from app.database import init_db; init_db()"
sudo -u "$APP_USER" "$VENV_PY" deploy/seed_db.py

echo "==> [6/7] Redemarrer les services"
sudo systemctl start ipxe-manager ipxe-celery

echo "==> [7/7] Regenerer les menus iPXE"
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

cat <<'EOF'

==> Termine.

Ensuite (UI) :
  1. Ajouter l'ISO WinPE (OS Windows, mode WinPE)
  2. Extraire l'ISO
  3. Ajouter les masters install.wim (locaux ou boot/masters/<famille>/<slug>/)
  4. Fiche ISO WinPE -> « Mettre a jour scripts WinPE et boot.wim »

EOF
