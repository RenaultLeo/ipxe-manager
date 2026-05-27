#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Nettoyage WinPE (BDD + fichiers résiduels)
#
# Quand les ISO / dossiers boot WinPE ont été supprimés à la main
# sans passer par l'UI « Supprimer ».
#
# Ne touche PAS aux masters globaux (boot/masters/) sauf CLEAN_MASTERS=1.
#
# Usage :
#   sudo bash /srv/ipxe/app/deploy/clean-winpe-orphans.sh
#   sudo DRY_RUN=1 bash ...              # simulation
#   sudo CLEAN_MASTERS=1 bash ...        # supprime aussi boot/masters/
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
DB_PATH="${DB_PATH:-/srv/ipxe/app/ipxe.db}"
HTTP_ROOT="${HTTP_ROOT:-/srv/ipxe/http}"
ISO_ROOT="${ISO_ROOT:-/srv/ipxe/isos}"
CONFIGS_ROOT="${CONFIGS_ROOT:-/srv/ipxe/configs}"
VENV_PY="${VENV_PY:-/srv/ipxe/venv/bin/python}"
APP_USER="${APP_USER:-ipxe}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ipxe/backups}"
DRY_RUN="${DRY_RUN:-0}"
CLEAN_MASTERS="${CLEAN_MASTERS:-0}"
CLEAN_ORPHAN_BOOT="${CLEAN_ORPHAN_BOOT:-1}"

STAMP="$(date +%Y%m%d-%H%M%S)"
PATHS_FILE="$(mktemp)"
trap 'rm -f "$PATHS_FILE"' EXIT

run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

rm_path() {
  local p="$1"
  if [ -z "$p" ] || [ "$p" = "/" ]; then
    return 0
  fi
  if [ -e "$p" ]; then
    run rm -rf "$p"
    echo "  supprimé : $p"
  fi
}

echo "==> [1/8] Vérifications"
if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0" >&2
  exit 1
fi
if [ ! -f "$DB_PATH" ]; then
  echo "ERREUR: base SQLite introuvable : $DB_PATH" >&2
  exit 1
fi
if [ ! -d "$APP_DIR" ]; then
  echo "ERREUR: APP_DIR introuvable : $APP_DIR" >&2
  exit 1
fi
cd "$APP_DIR"

echo "==> [2/8] Arrêt des services"
if [ "$DRY_RUN" != "1" ]; then
  systemctl stop ipxe-manager ipxe-celery
fi

echo "==> [3/8] Sauvegarde BDD"
run mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/ipxe.db.winpe-clean.$STAMP.bak"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] cp $DB_PATH $BACKUP_FILE"
else
  cp -a "$DB_PATH" "$BACKUP_FILE"
  echo "  Backup : $BACKUP_FILE"
fi

echo "==> [4/8] Suppression des versions WinPE en BDD"
sudo -u "$APP_USER" "$VENV_PY" - <<PY
import json
import sys
from pathlib import Path

sys.path.insert(0, r"$APP_DIR")
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.database import SessionLocal
from app.models.models import IsoVersion, OsType
from app.services.slugify import slugify

paths_file = Path(r"$PATHS_FILE")
dry = r"$DRY_RUN" == "1"

db = SessionLocal()
try:
    versions = (
        db.query(IsoVersion)
        .options(
            joinedload(IsoVersion.os_type),
            joinedload(IsoVersion.boot_entry),
        )
        .join(OsType)
        .filter(
            or_(
                IsoVersion.windows_mode.ilike("winpe"),
                OsType.slug.ilike("winpe"),
            )
        )
        .all()
    )
    print(f"  Versions WinPE en BDD : {len(versions)}")
    todo = []
    for v in versions:
        os_slug = (v.os_type.slug or "windows").strip()
        seg = None
        be = v.boot_entry
        if be and be.boot_wim_path:
            parts = be.boot_wim_path.replace("\\\\", "/").strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "boot":
                seg = parts[2]
        if not seg:
            seg = slugify(v.version_label) if v.version_label else str(v.id)
        todo.append(
            {
                "id": v.id,
                "label": v.version_label,
                "os_slug": os_slug,
                "segment": seg,
            }
        )
        print(f"    - id={v.id} {v.version_label!r} (boot/{os_slug}/{seg}/)")

    paths_file.write_text(json.dumps(todo, ensure_ascii=False) + "\n", encoding="utf-8")

    if dry:
        print("  [dry-run] suppression BDD ignorée")
    elif versions:
        for v in list(versions):
            v.active_winpe_install_id = None
            v.active_autoconfig_id = None
            v.winpe_startnet_patched_at = None
            db.delete(v)
        db.commit()
        print("  BDD : versions WinPE supprimées (boot_entries, autoconfigs, winpe_installs en cascade)")
    else:
        print("  BDD : rien à supprimer — poursuite nettoyage fichiers résiduels")
finally:
    db.close()
PY

echo "==> [5/8] Fichiers liés aux versions WinPE supprimées"
if grep -q '"id"' "$PATHS_FILE" 2>/dev/null; then
  while IFS= read -r p; do
    [ -n "$p" ] && rm_path "$p"
  done < <(
    sudo -u "$APP_USER" "$VENV_PY" - <<PY
import json
from pathlib import Path

todo = json.loads(Path(r"$PATHS_FILE").read_text(encoding="utf-8"))
http = Path(r"$HTTP_ROOT")
iso_root = Path(r"$ISO_ROOT")
cfg_root = Path(r"$CONFIGS_ROOT")
for row in todo:
    os_slug = row["os_slug"]
    seg = row["segment"]
    vid = row["id"]
    for base, rel in (
        (http / "boot", f"{os_slug}/{seg}"),
        (http / "boot", f"winpe/{seg}"),
        (http / "boot", f"windows/{seg}"),
        (iso_root, f"{os_slug}/{vid}"),
        (iso_root, f"winpe/{vid}"),
        (iso_root, f"windows/{vid}"),
        (cfg_root, f"{os_slug}/{seg}"),
        (cfg_root, f"{os_slug}/{vid}"),
        (cfg_root, f"winpe/{seg}"),
        (cfg_root, f"winpe/{vid}"),
    ):
        print(str(base / rel))
PY
  )
else
  echo "  Aucun chemin BDD — skip"
fi

echo "==> [6/8] Arborescences WinPE legacy + menus obsolètes"
rm_path "$HTTP_ROOT/boot/winpe"
for stale in winpe.ipxe winpe_autres.ipxe; do
  rm_path "$HTTP_ROOT/menus/$stale"
done

if [ "$CLEAN_ORPHAN_BOOT" = "1" ]; then
  echo "  Dossiers boot/windows/* orphelins (WinPE sans ISO en BDD)..."
  while IFS= read -r p; do
    [ -n "$p" ] && rm_path "$p"
  done < <(
    sudo -u "$APP_USER" "$VENV_PY" - <<PY
import sqlite3
from pathlib import Path

db = Path(r"$DB_PATH")
boot_win = Path(r"$HTTP_ROOT") / "boot" / "windows"
if not boot_win.is_dir():
    raise SystemExit(0)
con = sqlite3.connect(db)
known = set()
for row in con.execute(
    "SELECT boot_wim_path FROM boot_entries WHERE coalesce(boot_wim_path,'') != ''"
):
    parts = row[0].replace("\\\\", "/").strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "boot" and parts[1].lower() == "windows":
        known.add(parts[2].lower())
con.close()
for d in sorted(boot_win.iterdir()):
    if not d.is_dir():
        continue
    looks_winpe = (d / "scripts" / "deploy.ps1").is_file() or any(d.rglob("boot.wim"))
    if looks_winpe and d.name.lower() not in known:
        print(d)
PY
  )
fi

if [ "$CLEAN_MASTERS" = "1" ]; then
  echo "  CLEAN_MASTERS=1 → suppression boot/masters/"
  rm_path "$HTTP_ROOT/boot/masters"
else
  echo "  boot/masters/ conservé (CLEAN_MASTERS=1 pour supprimer)"
fi

echo "==> [7/8] init_db + FK orphelines + menus"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] init_db / FK / regenerate_all ignorés"
else
  sudo -u "$APP_USER" "$VENV_PY" -c "from app.database import init_db; init_db()"
  sudo -u "$APP_USER" "$VENV_PY" - <<PY
import sqlite3
db_path = r"$DB_PATH"
con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute("""
UPDATE iso_versions SET active_winpe_install_id = NULL
WHERE active_winpe_install_id IS NOT NULL
  AND active_winpe_install_id NOT IN (SELECT id FROM winpe_installs)
""")
for table in ("boot_entries", "autoconfigs", "winpe_installs"):
    cur.execute(f"DELETE FROM {table} WHERE iso_version_id NOT IN (SELECT id FROM iso_versions)")
cur.execute("""
UPDATE uploads SET iso_version_id = NULL
WHERE iso_version_id IS NOT NULL
  AND iso_version_id NOT IN (SELECT id FROM iso_versions)
""")
con.commit()
violations = cur.execute("PRAGMA foreign_key_check").fetchall()
print(f"  FK violations restantes : {len(violations)}")
con.close()
PY
  if sudo -u "$APP_USER" "$VENV_PY" - <<'PY'
import sys
from app.database import SessionLocal
from app.services.menu_generator import regenerate_all

db = SessionLocal()
try:
    regenerate_all(db)
finally:
    db.close()
print("menus regenerated")
PY
  then
    :
  else
    echo "  ! regenerate_all a echoue — relancer depuis l'UI : Menus iPXE -> Regenerer" >&2
  fi
fi

echo "==> [8/8] Redémarrage"
if [ "$DRY_RUN" != "1" ]; then
  systemctl start ipxe-manager ipxe-celery
fi

cat <<EOF

==> Nettoyage terminé${DRY_RUN:+ (simulation)}.

Backup BDD : $BACKUP_FILE

Tu peux ré-ajouter une ISO WinPE proprement :
  1. ISO Windows → mode WinPE → extraire
  2. Ajouter les masters install.wim
  3. « Mettre à jour scripts WinPE et boot.wim »

EOF
