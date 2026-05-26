#!/usr/bin/env bash
# Diagnostic Permission denied (0216eb3c) au boot iPXE HTTPS.
# Usage : sudo bash deploy/diagnose-https-boot.sh 192.168.1.54
set -euo pipefail

HOST="${1:-$(hostname -I | awk '{print $1}')}"
SSL_DIR="${SSL_DIR:-/srv/ipxe/ssl}"
TFTP="${TFTP_ROOT:-/srv/ipxe/tftpboot}"
BUILD_SRC="${BUILD_DIR:-/srv/ipxe/build}/ipxe-src/src"

echo "=== Diagnostic HTTPS iPXE pour $HOST ==="
echo ""

fail=0

check() {
  if "$@"; then
    echo "  OK  $*"
  else
    echo "  KO  $*"
    fail=1
  fi
}

echo "[1] Fichiers TLS"
check test -f "$SSL_DIR/ca.crt"
check test -f "$SSL_DIR/server.crt"
check test -f "$SSL_DIR/server.key"

echo ""
echo "[2] server.crt signé par ca.crt"
openssl verify -CAfile "$SSL_DIR/ca.crt" "$SSL_DIR/server.crt"

echo ""
echo "[3] SAN du certificat serveur (doit contenir $HOST)"
openssl x509 -in "$SSL_DIR/server.crt" -noout -ext subjectAltName 2>/dev/null || true

echo ""
echo "[4] TLS 1.2 depuis le serveur (comme iPXE)"
openssl s_client -connect "${HOST}:443" -servername "$HOST" \
  -CAfile "$SSL_DIR/ca.crt" -tls1_2 </dev/null 2>&1 | tail -12

echo ""
echo "[5] Menu HTTPS"
curl -fsS --cacert "$SSL_DIR/ca.crt" --tlsv1.2 \
  "https://${HOST}/menus/menu.ipxe" -o /dev/null && echo "  OK curl menu.ipxe" || {
  echo "  KO curl menu.ipxe"
  fail=1
}

echo ""
echo "[6] Firmware TFTP"
for f in undionly.kpxe snponly.efi; do
  p="$TFTP/$f"
  if [ -f "$p" ]; then
    echo "  $f : $(stat -c%s "$p" 2>/dev/null || stat -f%z "$p") o, $(stat -c%y "$p" 2>/dev/null || stat -f%Sm "$p")"
  else
    echo "  KO $p absent"
    fail=1
  fi
done

echo ""
echo "[7] Dernier build iPXE (TRUST embarqué ?)"
if [ -f "$BUILD_SRC/bin/.trusted.list" ] 2>/dev/null; then
  echo "  trusted.list : $(cat "$BUILD_SRC/bin/.trusted.list" 2>/dev/null || true)"
fi
if [ -f "$BUILD_SRC/ipxe-ca.crt" ]; then
  echo "  ipxe-ca.crt présent dans src (build récent)"
else
  echo "  ipxe-ca.crt absent dans src — recompilez le firmware"
  fail=1
fi

echo ""
echo "[8] Horloge serveur (décalage > 5 min = échec TLS au client PXE)"
date -u
echo "  Sur la VM PXE : dans iPXE tapez « show unixtime:int32 » et comparez à l'heure réelle."

echo ""
if [ "$fail" -ne 0 ]; then
  echo "=== Corriger puis : sudo bash deploy/enable-https.sh $HOST ==="
  exit 1
fi
echo "=== Côté serveur tout semble OK — si Permission denied persiste, recompilez :"
echo "    sudo bash deploy/enable-https.sh $HOST"
echo "    (firmware doit être compilé APRÈS le dernier ca.crt)"
