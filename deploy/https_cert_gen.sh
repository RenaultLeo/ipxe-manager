#!/usr/bin/env bash
# ============================================================
# Certificat TLS auto-signé pour Nginx + TRUST= iPXE
#
# Usage :
#   sudo bash deploy/https_cert_gen.sh <IP_OU_DNS_POUR_SAN> [/srv/ipxe/certs/ipxe-manager]
#
# Facultatif : exporter IPXE_TLS_EXTRA_SAN avant l’appel pour d’autres entrées SAN
# (IPs ou DNS, séparateurs virgule), utile si le menu HTTPS est utilisé depuis plusieurs IPs :
#
#   export IPXE_TLS_EXTRA_SAN="192.168.2.6"
#   sudo bash deploy/https_cert_gen.sh 192.168.2.8
#
# Écrit server.crt et server.key dans le répertoire cible (réutilisable avec make TRUST=…).
# ============================================================
set -euo pipefail

_trim_ws() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

_is_ip_literal() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ "$1" =~ ^[0-9a-fA-F:]+:[0-9a-fA-F:]*[0-9a-fA-F]+$ ]]
}

SAN_PRIMARY="${1:?Usage: https_cert_gen.sh <ip-or-dns-for-SAN> [output-dir]}"

SAN_PARTS=( "DNS:localhost" "DNS:ipxe-manager" )
if _is_ip_literal "${SAN_PRIMARY}"; then
  SAN_PARTS+=( "IP:${SAN_PRIMARY}" )
else
  SAN_PARTS+=( "DNS:${SAN_PRIMARY}" )
fi

if [[ -n "${IPXE_TLS_EXTRA_SAN:-}" ]]; then
  IFS=',' read -ra EXTRA_RAW <<<"${IPXE_TLS_EXTRA_SAN}"
  for chunk in "${EXTRA_RAW[@]}"; do
    x="$(_trim_ws "$chunk")"
    [[ -z "$x" ]] && continue
    if _is_ip_literal "${x}"; then
      SAN_PARTS+=( "IP:${x}" )
    else
      SAN_PARTS+=( "DNS:${x}" )
    fi
  done
fi

SAN_LINE=$(IFS=','; echo "${SAN_PARTS[*]}")

OUT_DIR="${2:-/srv/ipxe/certs/ipxe-manager}"

mkdir -p "$OUT_DIR"
KEY="$OUT_DIR/server.key"
CRT="$OUT_DIR/server.crt"
CSR="$OUT_DIR/server.csr"
EXT="$OUT_DIR/openssl-ext.cnf"

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
