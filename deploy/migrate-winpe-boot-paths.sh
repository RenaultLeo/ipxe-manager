#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Migration chemins WinPE legacy (boot/winpe → boot/windows)
#
# À lancer si tu as mis à jour sans supprimer les ISO WinPE extraites
# sous l'ancien slug OS « winpe ».
#
# Usage :
#   sudo bash /srv/ipxe/app/deploy/migrate-winpe-boot-paths.sh
#   sudo DRY_RUN=1 bash ...   # simulation sans écriture
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
DB_PATH="${DB_PATH:-/srv/ipxe/app/ipxe.db}"
BOOT_ROOT="${BOOT_ROOT:-/srv/ipxe/http/boot}"
VENV_PY="${VENV_PY:-/srv/ipxe/venv/bin/python}"
APP_USER="${APP_USER:-ipxe}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ipxe/backups}"
DRY_RUN="${DRY_RUN:-0}"

WINPE_OLD="$BOOT_ROOT/winpe"
WINDOWS_NEW="$BOOT_ROOT/windows"
STAMP="$(date +%Y%m%d-%H%M%S)"

run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

echo "==> [1/7] Vérifications"
if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0" >&2
  exit 1
fi
if [ ! -f "$DB_PATH" ]; then
  echo "ERREUR: base SQLite introuvable : $DB_PATH" >&2
  exit 1
fi
if [ ! -x "$VENV_PY" ]; then
  echo "ERREUR: venv Python introuvable : $VENV_PY" >&2
  exit 1
fi

if [ ! -d "$WINPE_OLD" ] || [ -z "$(ls -A "$WINPE_OLD" 2>/dev/null || true)" ]; then
  echo "Rien à migrer : $WINPE_OLD absent ou vide."
  echo "Si les scripts WinPE manquent quand même, régénérez-les depuis l'UI."
  exit 0
fi

echo "==> [2/7] Arrêt des services"
if [ "$DRY_RUN" != "1" ]; then
  systemctl stop ipxe-manager ipxe-celery
fi

echo "==> [3/7] Sauvegarde BDD"
run mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/ipxe.db.winpe-migrate.$STAMP.bak"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] cp $DB_PATH $BACKUP_FILE"
else
  cp -a "$DB_PATH" "$BACKUP_FILE"
  echo "  Backup : $BACKUP_FILE"
fi

echo "==> [4/7] Déplacement boot/winpe/* → boot/windows/*"
run mkdir -p "$WINDOWS_NEW"
run chown "$APP_USER:$APP_USER" "$WINDOWS_NEW"

shopt -s nullglob
for src in "$WINPE_OLD"/*; do
  name="$(basename "$src")"
  dest="$WINDOWS_NEW/$name"
  if [ -d "$src" ]; then
    if [ -e "$dest" ]; then
      echo "  Fusion $src → $dest"
      if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] rsync -a $src/ $dest/"
        echo "  [dry-run] rm -rf $src"
      else
        rsync -a "$src/" "$dest/"
        rm -rf "$src"
        chown -R "$APP_USER:$APP_USER" "$dest"
      fi
    else
      echo "  Déplace $src → $dest"
      if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] mv $src $dest"
      else
        mv "$src" "$dest"
        chown -R "$APP_USER:$APP_USER" "$dest"
      fi
    fi
  elif [ -f "$src" ]; then
    echo "  Fichier racine : $name → $WINDOWS_NEW/"
    if [ "$DRY_RUN" = "1" ]; then
      echo "  [dry-run] mv $src $dest"
    else
      mv "$src" "$dest"
      chown "$APP_USER:$APP_USER" "$dest"
    fi
  fi
done

if [ "$DRY_RUN" != "1" ] && [ -d "$WINPE_OLD" ] && [ -z "$(ls -A "$WINPE_OLD" 2>/dev/null || true)" ]; then
  rmdir "$WINPE_OLD"
  echo "  Supprimé dossier vide : $WINPE_OLD"
fi

echo "==> [5/7] Mise à jour chemins boot/winpe/ → boot/windows/ en BDD"
SQL="
UPDATE boot_entries SET
  kernel_path=replace(replace(kernel_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  initrd_path=replace(replace(initrd_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  boot_wim_path=replace(replace(boot_wim_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  bcd_path=replace(replace(bcd_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  boot_sdi_path=replace(replace(boot_sdi_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  bootmgr_path=replace(replace(bootmgr_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  efi_path=replace(replace(efi_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/'),
  custom_ipxe_path=replace(replace(custom_ipxe_path,'boot/winpe/','boot/windows/'),'boot\\winpe\\','boot/windows/')
WHERE coalesce(boot_wim_path,'') LIKE '%winpe%'
   OR coalesce(kernel_path,'') LIKE '%winpe%'
   OR coalesce(bcd_path,'') LIKE '%winpe%';
"

if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] sqlite3 UPDATE boot_entries ..."
  sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM boot_entries WHERE coalesce(boot_wim_path,'') LIKE '%winpe%';" \
    | xargs -I{} echo "  Lignes boot_entries concernées : {}"
else
  BEFORE="$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM boot_entries WHERE coalesce(boot_wim_path,'') LIKE '%winpe%';")"
  sqlite3 "$DB_PATH" "$SQL"
  AFTER="$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM boot_entries WHERE coalesce(boot_wim_path,'') LIKE '%winpe%';")"
  echo "  boot_entries migrées : $BEFORE → reste $AFTER avec « winpe » dans boot_wim_path"
fi

echo "==> [6/7] init_db + régénération menus"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] init_db + regenerate_all"
else
  sudo -u "$APP_USER" "$VENV_PY" -c "from app.database import init_db; init_db()"
  sudo -u "$APP_USER" "$VENV_PY" - <<'PY'
from app.database import SessionLocal
from app.services.menu_generator import regenerate_all

db = SessionLocal()
try:
    regenerate_all(db)
finally:
    db.close()
print("menus regenerated")
PY
fi

echo "==> [7/7] Redémarrage + contrôle WinPE"
if [ "$DRY_RUN" != "1" ]; then
  systemctl start ipxe-manager ipxe-celery
  if [ -f "$APP_DIR/scripts/check_winpe_boot.py" ]; then
    sudo -u "$APP_USER" "$VENV_PY" "$APP_DIR/scripts/check_winpe_boot.py" || true
  fi
fi

cat <<EOF

==> Migration terminée${DRY_RUN:+ (simulation)}.

Étapes UI :
  1. Fiche ISO WinPE → « Mettre à jour scripts WinPE et boot.wim »
  2. Tester un boot PXE WinPE

Backup BDD : $BACKUP_FILE
EOF
