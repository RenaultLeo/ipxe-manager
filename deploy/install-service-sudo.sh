#!/usr/bin/env bash
# Installe ipxe-service-ctl + sudoers (relance services depuis la page Supervision).
# Usage sur le serveur : sudo bash deploy/install-service-sudo.sh
set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Exécutez ce script en root : sudo bash deploy/install-service-sudo.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CTL_SRC="${SCRIPT_DIR}/ipxe-service-ctl.sh"
CTL_DST="/usr/local/sbin/ipxe-service-ctl"
RENEW_SRC="${SCRIPT_DIR}/ipxe-renew-tls-cert.sh"
RENEW_DST="/usr/local/sbin/ipxe-renew-tls-cert"

if [[ ! -f "$CTL_SRC" ]]; then
  echo "Fichier introuvable : $CTL_SRC" >&2
  exit 1
fi

install -m 755 "$CTL_SRC" "$CTL_DST"
echo "  Installé : $CTL_DST"

if [[ -f "$RENEW_SRC" ]]; then
  install -m 755 "$RENEW_SRC" "$RENEW_DST"
  echo "  Installé : $RENEW_DST"
fi

cat > /etc/sudoers.d/ipxe-manager <<'EOF'
# iPXE Manager — extraction ISO (7z), montages, relance services, renouvellement TLS
Defaults:ipxe !requiretty
ipxe ALL=(ALL) NOPASSWD: /usr/bin/7z, /usr/bin/7za, /bin/mount, /bin/umount, /usr/local/sbin/ipxe-service-ctl, /usr/local/sbin/ipxe-renew-tls-cert
EOF
chmod 440 /etc/sudoers.d/ipxe-manager

if visudo -cf /etc/sudoers.d/ipxe-manager; then
  echo "  Sudoers OK : /etc/sudoers.d/ipxe-manager"
else
  echo "  ERREUR : sudoers invalide" >&2
  exit 1
fi

echo ""
echo "Test (en tant qu'ipxe) :"
echo "  sudo -u ipxe sudo -n /usr/local/sbin/ipxe-service-ctl restart ipxe-manager"
echo "  (ne pas lancer en prod si vous ne voulez pas redémarrer tout de suite)"
