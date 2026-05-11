#!/usr/bin/env bash
# ============================================================
# patch.sh — Correctifs cumulatifs iPXE Manager
# Usage : bash deploy/patch.sh
# ============================================================
set -euo pipefail

APP_DIR="/srv/ipxe/app"
VENV="/srv/ipxe/venv"
DB="/srv/ipxe/app/ipxe.db"

echo "==> git pull…"
cd "$APP_DIR"
git pull

# ── Patch 1 : Samba ───────────────────────────────────────
echo "==> Patch 1 : Samba"
apt-get install -y sqlite3 samba samba-common-bin

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
echo "  Samba OK"

# ── Patch 2 : AlmaLinux + WinPE en BDD ───────────────────
echo "==> Patch 2 : AlmaLinux + WinPE"
"$VENV/bin/python" "$APP_DIR/deploy/seed_db.py"
echo "  Seed OK"

# ── Patch 3 : colonne bootmgr_path ───────────────────────
echo "==> Patch 3 : colonne bootmgr_path"
sqlite3 "$DB" "ALTER TABLE boot_entries ADD COLUMN bootmgr_path VARCHAR(512);" 2>/dev/null \
    && echo "  Colonne ajoutée" \
    || echo "  Déjà présente"

# ── Redémarrage ───────────────────────────────────────────
echo "==> Redémarrage des services…"
systemctl restart ipxe-manager celery-worker

echo ""
echo "Tous les patchs appliqués."
