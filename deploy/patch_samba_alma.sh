#!/usr/bin/env bash
# Patch : Samba + AlmaLinux/WinPE en BDD
set -euo pipefail

apt-get install -y samba samba-common-bin

cat > /etc/samba/smb.conf <<'EOF'
[global]
   workgroup = WORKGROUP
   server string = iPXE Boot Server
   security = user
   map to guest = bad user

[boot]
   comment = iPXE Boot Files
   path = /srv/ipxe/http/boot
   browseable = yes
   read only = yes
   guest ok = yes

[isos]
   comment = ISO Images
   path = /srv/ipxe/isos
   browseable = yes
   read only = yes
   guest ok = yes
EOF

systemctl enable --now smbd nmbd

cd /srv/ipxe/app
git pull
/srv/ipxe/venv/bin/python deploy/seed_db.py
systemctl restart ipxe-manager

echo "Patch appliqué."
