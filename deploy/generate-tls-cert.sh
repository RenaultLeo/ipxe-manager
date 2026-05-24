#!/usr/bin/env bash
# Génère une CA + certificat serveur auto-signés pour iPXE Manager (OpenSSL).
# Usage : sudo bash deploy/generate-tls-cert.sh [IP_ou_FQDN]
# Sortie : /srv/ipxe/ssl/{ca.crt, ca.key, server.crt, server.key}
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez en root : sudo bash $0 [IP_ou_FQDN]" >&2
  exit 1
fi

HOST="${1:-$(hostname -I | awk '{print $1}')}"
SSL_DIR="${SSL_DIR:-/srv/ipxe/ssl}"
DAYS="${TLS_CERT_DAYS:-3650}"
CN="${TLS_CERT_CN:-iPXE Manager}"

if [ -z "$HOST" ]; then
  echo "Impossible de déterminer l'IP — passez-la en argument." >&2
  exit 1
fi

mkdir -p "$SSL_DIR"
chmod 755 "$SSL_DIR"

# Ne pas écraser sans confirmation explicite
if [ -f "$SSL_DIR/server.crt" ] && [ "${TLS_FORCE_REGEN:-0}" != "1" ]; then
  echo "Certificats déjà présents dans $SSL_DIR (TLS_FORCE_REGEN=1 pour régénérer)."
  exit 0
fi

echo "→ Génération TLS pour hôte : $HOST ($SSL_DIR)"

# Subject Alternative Name : IP + éventuellement DNS
SAN="IP:${HOST}"
if [[ ! "$HOST" =~ ^[0-9.]+$ ]]; then
  SAN="DNS:${HOST}"
else
  SAN="IP:${HOST},DNS:${HOST}"
fi

OPENSSL_CNF="$(mktemp)"
trap 'rm -f "$OPENSSL_CNF"' EXIT
cat >"$OPENSSL_CNF" <<EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_ca
req_extensions = v3_req

[dn]
CN = ${CN} CA

[v3_ca]
basicConstraints = critical, CA:true
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash

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

# ── CA (racine de confiance iPXE : TRUST=ca.crt) ─────────────────────────────
if [ ! -f "$SSL_DIR/ca.key" ] || [ "${TLS_FORCE_REGEN:-0}" = "1" ]; then
  openssl genrsa -out "$SSL_DIR/ca.key" 4096
  chmod 600 "$SSL_DIR/ca.key"
  openssl req -x509 -new -nodes -key "$SSL_DIR/ca.key" -sha256 -days "$DAYS" \
    -out "$SSL_DIR/ca.crt" -subj "/CN=${CN} CA"
fi
chmod 644 "$SSL_DIR/ca.crt"

# ── Certificat serveur (Nginx) ───────────────────────────────────────────────
openssl genrsa -out "$SSL_DIR/server.key" 4096
chmod 600 "$SSL_DIR/server.key"

CSR="$(mktemp)"
trap 'rm -f "$OPENSSL_CNF" "$CSR"' EXIT
openssl req -new -key "$SSL_DIR/server.key" -out "$CSR" \
  -subj "/CN=${HOST}" \
  -config "$OPENSSL_CNF" -reqexts v3_req

openssl x509 -req -in "$CSR" -CA "$SSL_DIR/ca.crt" -CAkey "$SSL_DIR/ca.key" \
  -CAcreateserial -out "$SSL_DIR/server.crt" -days "$DAYS" -sha256 \
  -extensions v3_server -extfile "$OPENSSL_CNF"

chmod 644 "$SSL_DIR/server.crt"

# Chaîne pour clients qui veulent fullchain (optionnel)
cat "$SSL_DIR/server.crt" "$SSL_DIR/ca.crt" > "$SSL_DIR/fullchain.pem"
chmod 644 "$SSL_DIR/fullchain.pem"

chmod 644 "$SSL_DIR/ca.crt" "$SSL_DIR/server.crt" "$SSL_DIR/fullchain.pem"
chmod 600 "$SSL_DIR/ca.key" "$SSL_DIR/server.key"
chown root:root "$SSL_DIR/ca.key" "$SSL_DIR/server.key"
chown ipxe:ipxe "$SSL_DIR/ca.crt" "$SSL_DIR/server.crt" "$SSL_DIR/fullchain.pem" 2>/dev/null || true

echo ""
echo "OK — certificats créés :"
echo "  CA (TRUST iPXE)  : $SSL_DIR/ca.crt"
echo "  Serveur (Nginx)  : $SSL_DIR/server.crt + $SSL_DIR/server.key"
echo "  Validité         : ${DAYS} jours"
echo ""
echo "Prochaines étapes :"
echo "  sudo bash /srv/ipxe/app/deploy/enable-https.sh ${HOST}"
echo "  Puis recompiler le firmware iPXE depuis l’interface (/firmware)."
