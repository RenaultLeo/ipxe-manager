#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Script de mise à jour
# Usage : sudo bash /srv/ipxe/app/deploy/update.sh
# ============================================================
set -euo pipefail

APP_DIR="/srv/ipxe/app"
VENV="/srv/ipxe/venv"

echo "==> Récupération des dernières modifications…"
git -C "$APP_DIR" pull --ff-only

echo "==> Mise à jour des dépendances Python…"
"$VENV/bin/pip" install -q --upgrade -r "$APP_DIR/requirements.txt"

if command -v node >/dev/null 2>&1; then
  echo "==> Fichiers i18n (liste DE/ES/IT/PT)…"
  (cd "$APP_DIR" && node tools/extract_en_list.mjs && node tools/build_locale_lists.mjs) \
    || echo "  ! Rebuild i18n échoué (fichiers du dépôt conservés)."
fi

echo "==> Migrations base de données…"
cd "$APP_DIR"
"$VENV/bin/python" deploy/seed_db.py

echo "==> Redémarrage des services…"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa
systemctl reload nginx 2>/dev/null || true

echo ""
echo "Mise à jour terminée."
systemctl is-active ipxe-manager  && echo "  [OK] ipxe-manager"  || echo "  [!!] ipxe-manager FAILED"
systemctl is-active ipxe-celery   && echo "  [OK] ipxe-celery"   || echo "  [!!] ipxe-celery FAILED"
