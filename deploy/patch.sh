#!/usr/bin/env bash
# ============================================================
# patch.sh — Anciens correctifs cumulés (SQLite + Samba ponctuel).
#
# ⚠ Déploiement neuf ou à jour : utiliser uniquement deploy/setup.sh puis
#    deploy/update.sh ; init_db dans l’app gère déjà les colonnes manquantes.
# Usage (si besoin) : sudo bash deploy/patch.sh
# ============================================================
set -euo pipefail

echo "==> (patch.sh) ATTENTION — script historique ; préférez deploy/update.sh pour les mises à jour."

APP_DIR="/srv/ipxe/app"
VENV="/srv/ipxe/venv"
DB="/srv/ipxe/app/ipxe.db"

echo "==> git pull…"
cd "$APP_DIR"
git pull --ff-only || git pull --ff-only || git pull

# ── Patch 1 : Samba ───────────────────────────────────────
echo "==> Patch 1 : Samba"
apt-get install -y sqlite3 samba samba-common-bin

cat > /etc/samba/smb.conf <<'EOF'
[global]
   workgroup = WORKGROUP
   server string = iPXE Boot Server
   security = user
   map to guest = bad user
   server min protocol = SMB2
   server max protocol = SMB3
   ntlm auth = yes

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

# ── Patch 3 : colonnes Windows ───────────────────────────
echo "==> Patch 3 : colonnes Windows (bootmgr_path, boot_sdi_path)"
sqlite3 "$DB" "ALTER TABLE boot_entries ADD COLUMN bootmgr_path VARCHAR(512);" 2>/dev/null \
    && echo "  bootmgr_path ajoutée" || echo "  bootmgr_path déjà présente"
sqlite3 "$DB" "ALTER TABLE boot_entries ADD COLUMN boot_sdi_path VARCHAR(512);" 2>/dev/null \
    && echo "  boot_sdi_path ajoutée" || echo "  boot_sdi_path déjà présente"

# ── Patch 4 : colonne bcd_path si absente ────────────────
echo "==> Patch 4 : colonne bcd_path"
sqlite3 "$DB" "ALTER TABLE boot_entries ADD COLUMN bcd_path VARCHAR(512);" 2>/dev/null \
    && echo "  bcd_path ajoutée" || echo "  bcd_path déjà présente"

# ── Redémarrage ───────────────────────────────────────────
echo "==> Redémarrage des services…"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa

echo ""
echo "Tous les patchs appliqués."
