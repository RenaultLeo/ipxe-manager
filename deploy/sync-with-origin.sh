#!/usr/bin/env bash
set -euo pipefail

# Sync local repo to remote branch exactly.
# Usage:
#   sudo bash deploy/sync-with-origin.sh
#   sudo bash deploy/sync-with-origin.sh main

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
TARGET_BRANCH="${1:-}"

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/sync-with-origin.sh [branch]" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "Git repo not found: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

if [[ -z "$TARGET_BRANCH" ]]; then
  TARGET_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi

echo "==> Sync repo with origin/$TARGET_BRANCH"
git fetch --all --prune
git reset --hard "origin/$TARGET_BRANCH"
git clean -fd

echo "==> Python deps"
"$VENV/bin/pip" install -q --upgrade -r "$APP_DIR/requirements.txt"

echo "==> DB seed/migrations"
sudo -u ipxe "$VENV/bin/python" "$APP_DIR/deploy/seed_db.py" || true

echo "==> Restart services"
systemctl restart ipxe-manager ipxe-celery tftpd-hpa

echo "[OK] Server now matches origin/$TARGET_BRANCH"
