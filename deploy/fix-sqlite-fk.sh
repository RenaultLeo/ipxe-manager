#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
DB_PATH="${DB_PATH:-/srv/ipxe/app/ipxe.db}"
VENV_PY="${VENV_PY:-/srv/ipxe/venv/bin/python}"
APP_USER="${APP_USER:-ipxe}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ipxe/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "==> [1/8] Verifications prealables"
if [[ ! -d "$APP_DIR" ]]; then
  echo "ERREUR: APP_DIR introuvable: $APP_DIR" >&2
  exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
  echo "ERREUR: DB SQLite introuvable: $DB_PATH" >&2
  exit 1
fi
if [[ ! -x "$VENV_PY" ]]; then
  echo "ERREUR: Python venv introuvable/executable: $VENV_PY" >&2
  exit 1
fi

echo "==> [2/8] Stop services"
sudo systemctl stop ipxe-manager ipxe-celery

echo "==> [3/8] Backup DB"
sudo mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/ipxe.db.$STAMP.bak"
sudo cp -a "$DB_PATH" "$BACKUP_FILE"
echo "Backup: $BACKUP_FILE"

echo "==> [4/8] Reparer foreign keys orphelines"
sudo -u "$APP_USER" "$VENV_PY" - <<PY
import sqlite3
import sys

db_path = r"$DB_PATH"
con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute("PRAGMA foreign_keys=ON")

before = cur.execute("PRAGMA foreign_key_check").fetchall()
print("Violations avant:", len(before))
for row in before:
    print(" ", row)

# iso_versions -> nullable refs
cur.execute("""
UPDATE iso_versions
SET owner_user_id = NULL
WHERE owner_user_id IS NOT NULL
  AND owner_user_id NOT IN (SELECT id FROM users)
""")
cur.execute("""
UPDATE iso_versions
SET active_autoconfig_id = NULL
WHERE active_autoconfig_id IS NOT NULL
  AND active_autoconfig_id NOT IN (SELECT id FROM autoconfigs)
""")
cur.execute("""
UPDATE iso_versions
SET active_winpe_install_id = NULL
WHERE active_winpe_install_id IS NOT NULL
  AND active_winpe_install_id NOT IN (SELECT id FROM winpe_installs)
""")

# iso_versions -> os_types (non-null): fallback windows
row = cur.execute("SELECT id FROM os_types WHERE slug='windows' LIMIT 1").fetchone()
if row:
    windows_id = int(row[0])
    cur.execute("""
    UPDATE iso_versions
    SET os_type_id = ?
    WHERE os_type_id NOT IN (SELECT id FROM os_types)
    """, (windows_id,))
else:
    print("WARN: os_type windows absent; remap os_type_id ignore")

# child tables -> orphan cleanup
for table in ("boot_entries", "autoconfigs", "winpe_installs"):
    cur.execute(f"""
    DELETE FROM {table}
    WHERE iso_version_id NOT IN (SELECT id FROM iso_versions)
    """)

# uploads -> nullable refs
cur.execute("""
UPDATE uploads
SET owner_user_id = NULL
WHERE owner_user_id IS NOT NULL
  AND owner_user_id NOT IN (SELECT id FROM users)
""")
cur.execute("""
UPDATE uploads
SET iso_version_id = NULL
WHERE iso_version_id IS NOT NULL
  AND iso_version_id NOT IN (SELECT id FROM iso_versions)
""")

con.commit()
after = cur.execute("PRAGMA foreign_key_check").fetchall()
print("Violations apres:", len(after))
for row in after:
    print(" ", row)
con.close()

if after:
    sys.exit(2)
PY

echo "==> [5/8] Migrations DB"
cd "$APP_DIR"
sudo -u "$APP_USER" "$VENV_PY" -c "from app.database import init_db; init_db()"

echo "==> [6/8] Seed OS types"
sudo -u "$APP_USER" "$VENV_PY" deploy/seed_db.py

echo "==> [7/8] Redemarrer services"
sudo systemctl start ipxe-manager ipxe-celery

echo "==> [8/8] Verification finale foreign_key_check"
sudo -u "$APP_USER" "$VENV_PY" - <<PY
import sqlite3
db_path = r"$DB_PATH"
con = sqlite3.connect(db_path)
rows = con.execute("PRAGMA foreign_key_check").fetchall()
print("Violations finales:", len(rows))
for row in rows:
    print(" ", row)
con.close()
PY

echo "==> Termine."
