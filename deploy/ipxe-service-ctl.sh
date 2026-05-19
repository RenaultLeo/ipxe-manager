#!/bin/bash
# Relance contrôlée des services iPXE — appelé via sudo par l'utilisateur « ipxe ».
set -euo pipefail

ACTION="${1:-}"
UNIT="${2:-}"

SYSTEMCTL="${SYSTEMCTL:-/usr/bin/systemctl}"
if [[ ! -x "$SYSTEMCTL" && -x /bin/systemctl ]]; then
  SYSTEMCTL=/bin/systemctl
fi

case "$ACTION" in
  restart)
    case "$UNIT" in
      ipxe-manager|ipxe-celery|tftpd-hpa)
        exec "$SYSTEMCTL" restart "$UNIT"
        ;;
      *)
        echo "Unité non autorisée : $UNIT" >&2
        exit 1
        ;;
    esac
    ;;
  reload-nginx)
    exec "$SYSTEMCTL" reload nginx
    ;;
  *)
    echo "Usage: $0 restart <ipxe-manager|ipxe-celery|tftpd-hpa>" >&2
    echo "       $0 reload-nginx" >&2
    exit 1
    ;;
esac
