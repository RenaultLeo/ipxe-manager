#!/usr/bin/env bash
# ============================================================
# Certificat TLS auto-signé pour Nginx + TRUST= iPXE
# Usage :
#   sudo bash deploy/https_cert_gen.sh <IP_OU_DNS_POUR_SAN> [/srv/ipxe/certs/ipxe-manager]
#
# Écrit server.crt et server.key dans le répertoire cible (réutilisable avec make TRUST=…).
# ============================================================
set -euo pipefail

SAN_PRIMARY="${1:?Usage: https_cert_gen.sh <ip-or-dns-for-SAN> [output-dir]}"
OUT_DIR="${2:-/srv/ipxe/certs/ipxe-manager}"

mkdir -p "$OUT_DIR"
KEY="$OUT_DIR/server.key"
CRT="$OUT_DIR/server.crt"
CSR="$OUT_DIR/server.csr"
EXT="$OUT_DIR/openssl-ext.cnf"

# SAN : localhost + valeur admin ; distinguer IP vs nom DNS pour openssl
if [[ "$SAN_PRIMARY" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ "$SAN_PRIMARY" =~ ^[0-9a-fA-F:]+:[0-9a-fA-F:]*[0-9a-fA-F]+$ ]]; then
  SAN_LINE="DNS:localhost,DNS:ipxe-manager,IP:${SAN_PRIMARY}"
else
  SAN_LINE="DNS:localhost,DNS:ipxe-manager,DNS:${SAN_PRIMARY}"
fi

cat > "$EXT" <<EOF
subjectAltName=${SAN_LINE}
EOF

umask 077
openssl req -newkey rsa:4096 -nodes \
  -keyout "$KEY" -out "$CSR" \
  -subj "/CN=ipxe-manager/O=iPXE Manager"

openssl x509 -req -days 825 -sha256 \
  -in "$CSR" -signkey "$KEY" -out "$CRT" \
  -extfile "$EXT"

rm -f "$CSR" "$EXT"
chmod 600 "$KEY"
chmod 644 "$CRT"
echo "TLS : $CRT et $KEY générés ($SAN_LINE)."
