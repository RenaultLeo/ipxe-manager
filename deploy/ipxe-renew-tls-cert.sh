#!/usr/bin/env bash
# Renouvelle server.crt / server.key (conserve la CA — iPXE TRUST inchangé).
# Usage : sudo bash deploy/ipxe-renew-tls-cert.sh [IP_ou_FQDN]
# Installé en /usr/local/sbin/ipxe-renew-tls-cert par install-service-sudo.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 [IP_ou_FQDN]" >&2
  exit 1
fi

HOST="${1:-$(hostname -I | awk '{print $1}')}"
SSL_DIR="${SSL_DIR:-/srv/ipxe/ssl}"
DAYS="${TLS_CERT_DAYS:-730}"

if [ -z "$HOST" ]; then
  echo "IP/FQDN requis." >&2
  exit 1
fi

# Aligné sur app/server_url_validation.py — refuse injection shell / OpenSSL.
if [[ "$HOST" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  :
elif [[ "$HOST" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,252}[a-zA-Z0-9])?$ ]]; then
  :
else
  echo "Hôte invalide (IPv4 ou FQDN ASCII attendu)." >&2
  exit 1
fi

if [ ! -f "$SSL_DIR/ca.crt" ] || [ ! -f "$SSL_DIR/ca.key" ]; then
  echo "CA absente dans $SSL_DIR — lancez deploy/generate-tls-cert.sh" >&2
  exit 1
fi

SAN="IP:${HOST}"
if [[ ! "$HOST" =~ ^[0-9.]+$ ]]; then
  SAN="DNS:${HOST}"
else
  SAN="IP:${HOST},DNS:${HOST}"
fi

OPENSSL_CNF="$(mktemp)"
CSR="$(mktemp)"
trap 'rm -f "$OPENSSL_CNF" "$CSR"' EXIT

cat >"$OPENSSL_CNF" <<EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
CN = ${HOST}

[v3_req]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${SAN}

[v3_server]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${SAN}
EOF

echo "→ Renouvellement certificat serveur pour ${HOST} (${DAYS} jours)…"

openssl genrsa -out "$SSL_DIR/server.key" 4096
chmod 600 "$SSL_DIR/server.key"
chown root:root "$SSL_DIR/server.key"

openssl req -new -key "$SSL_DIR/server.key" -out "$CSR" \
  -subj "/CN=${HOST}" \
  -config "$OPENSSL_CNF" -reqexts v3_req

openssl x509 -req -in "$CSR" -CA "$SSL_DIR/ca.crt" -CAkey "$SSL_DIR/ca.key" \
  -CAcreateserial -out "$SSL_DIR/server.crt" -days "$DAYS" -sha256 \
  -extensions v3_server -extfile "$OPENSSL_CNF"

chmod 644 "$SSL_DIR/server.crt"
cat "$SSL_DIR/server.crt" "$SSL_DIR/ca.crt" > "$SSL_DIR/fullchain.pem"
chmod 644 "$SSL_DIR/fullchain.pem"
chown ipxe:ipxe "$SSL_DIR/server.crt" "$SSL_DIR/fullchain.pem" 2>/dev/null || true

if command -v nginx >/dev/null 2>&1; then
  nginx -t
  systemctl reload nginx
  echo "  Nginx rechargé."
fi

echo "OK — server.crt renouvelé jusqu'à +${DAYS} jours (CA inchangée)."
