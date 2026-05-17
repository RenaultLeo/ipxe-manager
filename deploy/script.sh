#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Installation en une ligne (à lancer en root ou via sudo bash) :
#
#   curl -fsSL https://raw.githubusercontent.com/RenaultLeo/ipxe-manager/main/deploy/script.sh | sudo bash -s -- 192.168.2.6
#
# Le premier argument facultatif est l’IP publiée aux clients PXE ; sans argument,
# setup.sh prend la première IP locale (hostname -I).
#
# ⚠️ N’utilisez PAS « curl -I » / « -SI » seul pour installer : ces options font
#    une requête HEAD sans corps — il faut télécharger tout le fichier (-fsSL) et
#    le passer à bash comme ci‑dessus.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
REPO_URL="${IPXE_REPO_URL:-https://github.com/RenaultLeo/ipxe-manager.git}"
BRANCH="${IPXE_REPO_BRANCH:-main}"

mkdir -p "$(dirname "$APP_DIR")"

echo "==> Dépôt cible : $APP_DIR"

if [ -d "$APP_DIR/.git" ]; then
  echo "==> Repo déjà présent — mise à jour (pull --ff-only)…"
  git -C "$APP_DIR" pull --ff-only || echo "!!! pull ignoré — poursuite avec la copie locale."
else
  echo "==> Clone $REPO_URL (branche $BRANCH)…"
  rm -rf "$APP_DIR"
  if git clone -b "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"; then
    :
  elif git clone -b master --depth 1 "$REPO_URL" "$APP_DIR"; then
    echo "!!! Branche master utilisée à la place de $BRANCH."
  else
    git clone "$REPO_URL" "$APP_DIR"
  fi
fi

echo "==> Lancement deploy/setup.sh \"$*\" …"
exec bash "$APP_DIR/deploy/setup.sh" "$@"
