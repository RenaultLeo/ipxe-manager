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

# Vérifier que l'utilisateur ipxe peut lancer le renouvellement TLS sans mot de passe
echo ""
echo "Test sudo renouvellement TLS (utilisateur ipxe) :"
set +e
_sudo_tls_out=$(sudo -u ipxe sudo -n "$RENEW_DST" 2>&1)
_sudo_tls_rc=$?
set -e
if echo "$_sudo_tls_out" | grep -qiE 'password|not allowed|sudoers'; then
  echo "  [!!] Échec — l'UI « Renouveler le certificat » demandera un mot de passe."
  echo "      Relancez : sudo bash $SCRIPT_DIR/install-service-sudo.sh"
  echo "      Détail : $_sudo_tls_out"
elif echo "$_sudo_tls_out" | grep -qiE 'IP/FQDN requis|IP requis|requis'; then
  echo "  [OK] ipxe peut exécuter $RENEW_DST (NOPASSWD)"
else
  echo "  [OK] ipxe-renew-tls-cert invoqué (code $_sudo_tls_rc)"
fi

echo ""
echo "Commandes utiles :"
echo "  Renouveler le certificat (root) : sudo $RENEW_DST <IP_ou_FQDN>"
echo "  Test (comme l'UI)             : sudo -u ipxe sudo -n $RENEW_DST <IP_ou_FQDN>"
echo "  (sans l'extension .sh — binaire : $RENEW_DST)"
echo ""
echo "Test relance services (en tant qu'ipxe) :"
echo "  sudo -u ipxe sudo -n $CTL_DST status ipxe-manager"
echo "  (ne pas lancer restart en prod si vous ne voulez pas redémarrer tout de suite)"
