#!/usr/bin/env bash
# Installe proxmox-auto-install-assistant (dépôt Proxmox, pas dans Debian par défaut).
# Requis pour l’injection answer.toml → proxmox-netboot-autoinstall.iso.
# Usage : sudo bash deploy/install-proxmox-autoinstall-assistant.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0" >&2
  exit 1
fi

if command -v proxmox-auto-install-assistant >/dev/null 2>&1; then
  echo "proxmox-auto-install-assistant déjà installé :"
  proxmox-auto-install-assistant --version
  exit 0
fi

. /etc/os-release
CODENAME="${VERSION_CODENAME:-bookworm}"
ARCH="$(dpkg --print-architecture)"

echo "→ Installation proxmox-auto-install-assistant (Debian ${CODENAME})…"

apt-get update -qq
apt-get install -y -qq wget gnupg ca-certificates xorriso

KEYRING="/usr/share/keyrings/proxmox-archive-keyring.gpg"
if [ ! -f "$KEYRING" ]; then
  for url in \
    "https://enterprise.proxmox.com/debian/proxmox-release-${CODENAME}.gpg" \
    "https://download.proxmox.com/debian/proxmox-release-${CODENAME}.gpg"; do
    if wget -qO "$KEYRING" "$url" 2>/dev/null; then
      echo "  Clé Proxmox : $url"
      break
    fi
  done
fi

if [ ! -f "$KEYRING" ]; then
  echo "Impossible de télécharger la clé GPG Proxmox pour ${CODENAME}." >&2
  echo "Ajoutez manuellement le dépôt : https://pve.proxmox.com/wiki/Package_Repositories" >&2
  exit 1
fi

LIST="/etc/apt/sources.list.d/pve-no-subscription.list"
if [ ! -f "$LIST" ]; then
  cat >"$LIST" <<EOF
deb [arch=${ARCH} signed-by=${KEYRING}] http://download.proxmox.com/debian/pve ${CODENAME} pve-no-subscription
EOF
  echo "  Dépôt ajouté : $LIST"
fi

apt-get update -qq
apt-get install -y proxmox-auto-install-assistant

echo "OK : $(proxmox-auto-install-assistant --version)"
