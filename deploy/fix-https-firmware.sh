#!/usr/bin/env bash
# Supprime firmware HTTP générique + recompile HTTPS (menus inclus).
# Usage : sudo bash deploy/fix-https-firmware.sh 192.168.1.54
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 IP_SERVEUR" >&2
  exit 1
fi

HOST="${1:-$(hostname -I | awk '{print $1}')}"
APP_DIR="${APP_DIR:-/srv/ipxe/app}"
TFTP="${TFTP_ROOT:-/srv/ipxe/tftpboot}"

echo "==> IP serveur : $HOST"
rm -f "$TFTP/undionly.kpxe" "$TFTP/ipxe.efi" "$TFTP/snponly.efi"
bash "$APP_DIR/deploy/bootstrap-https-firmware.sh" "$HOST"
