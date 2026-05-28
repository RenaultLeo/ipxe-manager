#!/usr/bin/env bash
set -euo pipefail

START_SERVER=0
SMOKE_SERVER=0

for arg in "$@"; do
  case "$arg" in
    --start-server) START_SERVER=1 ;;
    --smoke-server) SMOKE_SERVER=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./deploy/run-checkup.sh [--smoke-server] [--start-server]

  --smoke-server  Start app, probe /login, then stop.
  --start-server  Start app in foreground at end (Ctrl+C to stop).
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

cd "$(dirname "$0")/.."

step() {
  echo
  echo "==> $1"
}

ok() { echo "[OK] $1"; }
fail() { echo "[FAIL] $1"; }

ALL_OK=1

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python not found (python3/python)." >&2
  exit 1
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) is required. Install it first." >&2
  exit 1
fi

echo "iPXE Manager - Full checkup"
echo "Root: $(pwd)"
echo "Python: $PYTHON_BIN"

step "Python compile check"
if "$PYTHON_BIN" -m compileall app; then
  ok "python compileall"
else
  fail "python compileall"
  ALL_OK=0
fi

step "Regression pattern check (must be empty)"
NEGATIVE_PATTERNS=(
  "winpe_install_added"
  "_esxi_kernel_basename_from_boot_cfg"
)
for p in "${NEGATIVE_PATTERNS[@]}"; do
  if out="$(rg -n --hidden --glob '!**/.git/**' "$p" app || true)" && [[ -z "$out" ]]; then
    ok "rg '$p' app"
  else
    fail "rg '$p' app"
    [[ -n "${out:-}" ]] && echo "$out"
    ALL_OK=0
  fi
done

# Old i18n key removed on purpose. Must not reappear.
if out="$(rg -n '"sett\.tls_renew_confirm"\s*:' app/i18n.py || true)" && [[ -z "$out" ]]; then
  ok "removed key sett.tls_renew_confirm"
else
  fail "removed key sett.tls_renew_confirm"
  [[ -n "${out:-}" ]] && echo "$out"
  ALL_OK=0
fi

step "Expected key check (must exist)"
POSITIVE_PATTERNS=(
  "iso\\.esxi_active_config_bad_type"
  "iso\\.esxi_need_extract"
  "iso\\.esxi_efi_only_notice"
  "iso\\.detail\\.more"
  "iso\\.detail\\.less"
  "iso\\.detail\\.actions"
)
for p in "${POSITIVE_PATTERNS[@]}"; do
  if rg -n "$p" app >/dev/null; then
    ok "rg '$p' app"
  else
    fail "rg '$p' app"
    ALL_OK=0
  fi
done

step "Duplicate i18n key check (targeted)"
fr_count="$(rg -n '"iso\\.winpe_language_packs_empty"\\s*:' app/i18n.py | wc -l | tr -d ' ')"
if [[ "$fr_count" == "2" ]]; then
  ok "iso.winpe_language_packs_empty present once per locale (2 total)"
else
  fail "iso.winpe_language_packs_empty duplicate/missing (count=$fr_count)"
  ALL_OK=0
fi

if [[ "$SMOKE_SERVER" -eq 1 ]]; then
  step "Server smoke test (/login)"
  TMP_LOG="$(mktemp)"
  set +e
  "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8099 >"$TMP_LOG" 2>&1 &
  UVICORN_PID=$!
  set -e
  trap 'kill "$UVICORN_PID" >/dev/null 2>&1 || true; rm -f "$TMP_LOG"' EXIT

  ready=0
  for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:8099/login" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done

  if [[ "$ready" -eq 1 ]]; then
    ok "uvicorn smoke test"
  else
    fail "uvicorn smoke test"
    echo "--- uvicorn log ---"
    sed -n '1,160p' "$TMP_LOG"
    ALL_OK=0
  fi

  kill "$UVICORN_PID" >/dev/null 2>&1 || true
  wait "$UVICORN_PID" 2>/dev/null || true
  rm -f "$TMP_LOG"
  trap - EXIT
fi

echo
if [[ "$ALL_OK" -eq 1 ]]; then
  echo "Checkup complete: OK"
else
  echo "Checkup complete: FAILED"
fi

if [[ "$START_SERVER" -eq 1 ]]; then
  step "Starting app server (foreground)"
  echo "Stop with Ctrl+C"
  exec "$PYTHON_BIN" -m uvicorn app.main:app --reload
fi

[[ "$ALL_OK" -eq 1 ]] || exit 2
