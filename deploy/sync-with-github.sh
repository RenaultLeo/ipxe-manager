#!/usr/bin/env bash
# Synchronise strictement le dossier app avec le dépôt GitHub distant.
# Objectif: matcher exactement la branche distante (code + suppressions),
# puis redémarrer les services applicatifs.
#
# Usage:
#   sudo bash deploy/sync-with-github.sh
#   sudo BRANCH=main bash deploy/sync-with-github.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
BRANCH="${BRANCH:-}"
REMOTE="${REMOTE:-origin}"
APP_USER="${APP_USER:-ipxe}"
KEEP_ENV="${KEEP_ENV:-1}"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/sync-with-github.sh" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Not a git repo: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

if [[ -z "$BRANCH" ]]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi

echo "==> Syncing $APP_DIR with $REMOTE/$BRANCH"
echo "==> Backup marker: $STAMP"

if [[ "$KEEP_ENV" == "1" && -f "$APP_DIR/.env" ]]; then
  cp -a "$APP_DIR/.env" "/tmp/ipxe.env.$STAMP.bak"
  echo "Saved .env -> /tmp/ipxe.env.$STAMP.bak"
fi

echo "==> Fetch latest"
git fetch --prune "$REMOTE"

echo "==> Hard reset to $REMOTE/$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

echo "==> Remove untracked files/dirs"
git clean -fd

if [[ "$KEEP_ENV" == "1" && -f "/tmp/ipxe.env.$STAMP.bak" ]]; then
  cp -a "/tmp/ipxe.env.$STAMP.bak" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env" 2>/dev/null || true
  chmod 640 "$APP_DIR/.env" 2>/dev/null || true
  echo "Restored .env"
fi

echo "==> Python deps"
/srv/ipxe/venv/bin/pip install -q -r "$APP_DIR/requirements.txt"

echo "==> DB seed/migrations (idempotent)"
sudo -u "$APP_USER" /srv/ipxe/venv/bin/python "$APP_DIR/deploy/seed_db.py" || true

echo "==> Restart services"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa

echo ""
echo "Done. App now matches $REMOTE/$BRANCH (plus local .env restore if enabled)."
