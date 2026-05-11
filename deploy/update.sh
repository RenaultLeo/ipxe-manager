#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Script de mise à jour
# Usage : sudo bash /srv/ipxe/app/deploy/update.sh
# ============================================================
set -euo pipefail

APP_DIR="/srv/ipxe/app"
VENV="/srv/ipxe/venv"

echo "==> Récupération des dernières modifications…"
git -C "$APP_DIR" pull origin main

echo "==> Mise à jour des dépendances Python…"
"$VENV/bin/pip" install -q --upgrade -r "$APP_DIR/requirements.txt"

echo "==> Redémarrage des services…"
systemctl restart ipxe-manager celery-worker

echo ""
echo "Mise à jour terminée."
systemctl is-active ipxe-manager  && echo "  v ipxe-manager OK" || echo "  x ipxe-manager FAILED"
systemctl is-active celery-worker && echo "  v celery-worker OK" || echo "  x celery-worker FAILED"
