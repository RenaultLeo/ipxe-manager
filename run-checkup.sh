#!/usr/bin/env bash
set -euo pipefail

START_SERVER=0
if [[ "${1:-}" == "--start-server" ]]; then
  START_SERVER=1
fi

step() {
  echo
  echo "==> $1"
}

ok() { echo "[OK] $1"; }
fail() { echo "[FAIL] $1"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "iPXE Manager - Linux Checkup"
echo "Root: $ROOT_DIR"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python introuvable (python3/python)." >&2
  exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) manquant. Installe-le puis relance." >&2
  exit 1
fi

ALL_OK=1

step "Compilation Python (compileall app)"
if "$PYTHON_BIN" -m compileall app; then
  ok "python -m compileall app"
else
  fail "python -m compileall app"
  ALL_OK=0
fi

step "Recherche regressions connues (doit etre vide)"
PATTERNS=(
  "winpe_install_added"
  "_esxi_kernel_basename_from_boot_cfg"
  "sett\\.tls_renew_confirm"
  "iso\\.proxmox_inject_need_extract"
  "iso\\.proxmox_active_config_bad_type"
)

for p in "${PATTERNS[@]}"; do
  if out="$(rg -n --hidden --glob '!**/.git/**' "$p" app || true)" && [[ -z "$out" ]]; then
    ok "rg '$p' app"
  else
    fail "rg '$p' app"
    [[ -n "${out:-}" ]] && echo "$out"
    ALL_OK=0
  fi
done

step "Verification des nouvelles cles critiques (doit exister)"
REQUIRED=(
  "iso\\.esxi_active_config_bad_type"
  "iso\\.esxi_need_extract"
  "iso\\.detail\\.more"
  "iso\\.detail\\.less"
  "iso\\.detail\\.actions"
)

for p in "${REQUIRED[@]}"; do
  if rg -n "$p" app >/dev/null; then
    ok "rg '$p' app"
  else
    fail "rg '$p' app"
    ALL_OK=0
  fi
done

echo
if [[ "$ALL_OK" -eq 1 ]]; then
  echo "Checkup termine: OK"
else
  echo "Checkup termine: des points sont en echec"
fi

if [[ "$START_SERVER" -eq 1 ]]; then
  step "Demarrage serveur uvicorn (--reload)"
  echo "Arret: Ctrl+C"
  "$PYTHON_BIN" -m uvicorn app.main:app --reload
fi

[[ "$ALL_OK" -eq 1 ]] || exit 2
