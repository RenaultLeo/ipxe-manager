#!/usr/bin/env bash
# Corrige general.h (HTTPS) + supprime firmware générique boot.ipxe.org + recompile.
# Usage : sudo bash deploy/fix-https-firmware.sh 192.168.1.54
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 IP_SERVEUR" >&2
  exit 1
fi

HOST="${1:-$(hostname -I | awk '{print $1}')}"
APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
TFTP="${TFTP_ROOT:-/srv/ipxe/tftpboot}"
GENERAL="${BUILD_DIR:-/srv/ipxe/build}/ipxe-src/src/config/general.h"

echo "==> IP serveur : $HOST"
echo "==> Patch $GENERAL"

if [ ! -f "$GENERAL" ]; then
  echo "Sources iPXE absentes : $GENERAL" >&2
  echo "Lancez d'abord une compilation Firmware ou setup.sh." >&2
  exit 1
fi

"$VENV/bin/python" - "$GENERAL" <<'PY'
from pathlib import Path
import re
import sys

general = Path(sys.argv[1])
g = general.read_text(encoding="utf-8", errors="replace")
g, n_undef = re.subn(
    r"^[ \t]*#undef[ \t]+DOWNLOAD_PROTO_HTTPS[^\n]*\n",
    "",
    g,
    flags=re.MULTILINE,
)
g, _ = re.subn(
    r"^[ \t]*//[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS[^\n]*\n",
    "#define DOWNLOAD_PROTO_HTTPS\t\t/* Secure Hypertext Transfer Protocol */\n",
    g,
    count=1,
    flags=re.MULTILINE,
)
if not re.search(r"^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTPS\b", g, flags=re.MULTILINE):
    g2, n = re.subn(
        r"(^[ \t]*#define[ \t]+DOWNLOAD_PROTO_HTTP\b[^\n]*\n)",
        r"\1#define DOWNLOAD_PROTO_HTTPS\t\t/* Secure Hypertext Transfer Protocol */\n",
        g,
        count=1,
        flags=re.MULTILINE,
    )
    if not n:
        sys.exit("Impossible d'ajouter #define DOWNLOAD_PROTO_HTTPS")
    g = g2
general.write_text(g, encoding="utf-8")
print(f"OK — #undef supprimés: {n_undef}, fichier patché.")
PY

LOCAL_DIR="$(dirname "$GENERAL")/local"
mkdir -p "$LOCAL_DIR"
cat >"$LOCAL_DIR/general.h" <<'EOF'
/* iPXE Manager — HTTPS pour undionly.kpxe (après #undef PLATFORM_pcbios) */
#define DOWNLOAD_PROTO_HTTPS
EOF
echo "==> $LOCAL_DIR/general.h écrit"

echo "==> Suppression firmware générique (HTTP only) dans TFTP"
rm -f "$TFTP/undionly.kpxe" "$TFTP/ipxe.efi" "$TFTP/snponly.efi"

echo "==> Recompilation firmware + menus"
bash "$APP_DIR/deploy/bootstrap-https-firmware.sh" "$HOST"

echo "==> Vérification strings"
if strings "$TFTP/undionly.kpxe" | grep -qF '/menus/menu.ipxe'; then
  echo "OK : URL menu embarquée dans undionly.kpxe"
  strings "$TFTP/undionly.kpxe" | grep -E 'https://[^[:space:]]+/menus/menu\.ipxe' | head -1 || true
elif strings "$TFTP/undionly.kpxe" | grep -qi 'openssl\|tls'; then
  echo "OK : TLS/OpenSSL présent (vérifiez l'URL menu au boot)"
else
  echo "KO : undionly.kpxe sans embed HTTPS — voir logs compilation" >&2
  exit 1
fi
