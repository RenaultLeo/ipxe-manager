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

if ! command -v proxmox-auto-install-assistant >/dev/null 2>&1; then
  if [ -f "$APP_DIR/deploy/install-proxmox-autoinstall-assistant.sh" ]; then
    echo "==> proxmox-auto-install-assistant absent — installation…"
    bash "$APP_DIR/deploy/install-proxmox-autoinstall-assistant.sh" \
      || echo "  ! Échec — injection Proxmox indisponible (voir deploy/install-proxmox-autoinstall-assistant.sh)."
  fi
fi

if command -v node >/dev/null 2>&1; then
  echo "==> Fichiers i18n (liste DE/ES/IT/PT)…"
  (cd "$APP_DIR" && node tools/extract_en_list.mjs && node tools/build_locale_lists.mjs) \
    || echo "  ! Rebuild i18n échoué (fichiers du dépôt conservés)."
fi

echo "==> Migrations base de données…"
cd "$APP_DIR"
"$VENV/bin/python" deploy/seed_db.py

echo "==> Redémarrage des services applicatifs…"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa

echo "==> Nginx — alignement sur deploy/nginx.conf…"
if [ -f "$APP_DIR/deploy/nginx.conf" ]; then
  cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/ipxe-manager
  if nginx -t 2>/dev/null; then
    systemctl reload nginx && echo "  Nginx rechargé."
  else
    echo "  ! nginx -t a échoué — corrige la config puis: sudo nginx -t && sudo systemctl reload nginx" >&2
  fi
else
  echo "  ! deploy/nginx.conf absent dans le dépôt."
fi

echo ""
echo "Mise à jour terminée."
systemctl is-active ipxe-manager  && echo "  [OK] ipxe-manager"  || echo "  [!!] ipxe-manager FAILED"
systemctl is-active ipxe-celery   && echo "  [OK] ipxe-celery"   || echo "  [!!] ipxe-celery FAILED"
