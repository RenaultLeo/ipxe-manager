#!/usr/bin/env bash
# Pré-vol config + suppression firmware HTTP-only + recompile HTTPS.
# Usage : sudo bash deploy/fix-https-firmware.sh 192.168.1.54
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 IP_SERVEUR" >&2
  exit 1
fi

HOST="${1:-$(hostname -I | awk '{print $1}')}"
APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
TFTP="${TFTP_ROOT:-/srv/ipxe/tftpboot}"
MENU_URL="https://${HOST}/menus/menu.ipxe"

echo "==> IP serveur : $HOST"
echo "==> Menu       : $MENU_URL"

if [ ! -f "$VENV/bin/python" ]; then
  echo "Virtualenv absent : $VENV" >&2
  exit 1
fi

echo "==> Pré-vol fichiers (patch + vérif, sans make)…"
sudo -u "${APP_USER:-ipxe}" env HOME=/srv/ipxe \
  "$VENV/bin/python" "$APP_DIR/deploy/preflight-ipxe-firmware.py" \
  --menu-url "$MENU_URL"

echo "==> Suppression firmware générique (HTTP only) dans TFTP"
rm -f "$TFTP/undionly.kpxe" "$TFTP/ipxe.efi" "$TFTP/snponly.efi"

echo "==> Recompilation firmware + menus (contrôles avant/après make intégrés)…"
bash "$APP_DIR/deploy/bootstrap-https-firmware.sh" "$HOST"

echo "==> Vérification finale TFTP"
if strings "$TFTP/undionly.kpxe" | grep -qF '/menus/menu.ipxe'; then
  echo "OK : URL menu embarquée dans undionly.kpxe"
  strings "$TFTP/undionly.kpxe" | grep -E 'https://[^[:space:]]+/menus/menu\.ipxe' | head -1 || true
else
  echo "KO : undionly.kpxe sans embed — consultez les logs de compilation" >&2
  exit 1
fi
