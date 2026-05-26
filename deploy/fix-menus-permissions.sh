#!/usr/bin/env bash
# Corrige propriétaire/droits de /srv/ipxe/http/menus (menus créés en root par erreur).
# Usage : sudo bash deploy/fix-menus-permissions.sh
set -euo pipefail

APP_USER="${APP_USER:-ipxe}"
DATA_DIR="${DATA_DIR:-/srv/ipxe}"
MENUS="$DATA_DIR/http/menus"

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0" >&2
  exit 1
fi

mkdir -p "$MENUS"
chown -R "$APP_USER:$APP_USER" "$MENUS"
chmod 2775 "$MENUS" 2>/dev/null || chmod 775 "$MENUS"
find "$MENUS" -type d -exec chmod 775 {} \;
find "$MENUS" -type f -exec chmod 664 {} \;
chmod -R o+rX "$MENUS"

echo "OK — $MENUS appartient à $APP_USER (lecture Nginx : other+rX)."
echo "Puis : Menus iPXE → Régénérer tous les menus (ou relancer une extraction)."
