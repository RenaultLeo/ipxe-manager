#!/usr/bin/env bash
# NFS minimal pour Ubuntu netboot (extractions sous http/boot/ubuntu).
# Pas de git pull / pip / seed_db — uniquement le serveur NFS.
#
# Usage : sudo bash deploy/nfs-setup.sh
# Vars optionnelles : DATA_DIR (défaut /srv/ipxe)
set -euo pipefail

DATA_DIR="${DATA_DIR:-/srv/ipxe}"
EXPORT_PATH="$DATA_DIR/http/boot/ubuntu"

if [[ $EUID -ne 0 ]]; then
  echo "Lancer en root/sudo." >&2
  exit 1
fi

echo "[nfs] Installation nfs-kernel-server…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nfs-kernel-server

echo "[nfs] Répertoire export : $EXPORT_PATH"
mkdir -p "$EXPORT_PATH"

echo "[nfs] /etc/exports.d/ipxe-manager-ubuntu.exports"
install -d -m 755 /etc/exports.d
cat > /etc/exports.d/ipxe-manager-ubuntu.exports <<EOF
# nfsroot côté client typique : IP:$DATA_DIR/http/boot/ubuntu/<slug>
# « No such file » → le dossier <slug> est absent sur le disque ou ne correspond pas au slug du menu.
$EXPORT_PATH *(ro,sync,no_subtree_check,insecure,no_root_squash)
EOF

exportfs -ra

if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^nfs-server\.service'; then
  systemctl enable --now nfs-server
elif systemctl list-unit-files --type=service 2>/dev/null | grep -q '^nfs-kernel-server\.service'; then
  systemctl enable --now nfs-kernel-server
else
  echo "Unité nfs-server / nfs-kernel-server introuvable." >&2
  exit 1
fi

chmod -R o+rX "$DATA_DIR/http/boot" 2>/dev/null || true

echo ""
echo "OK — showmount -e localhost"
echo ""
