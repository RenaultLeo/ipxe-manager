#!/usr/bin/env bash
# ============================================================
# Certificat TLS auto-signé pour Nginx + embarquement iPXE (make CERT=)
#
# Usage :
#   sudo bash deploy/https_cert_gen.sh [AUTO|<ip>|<hostname-dns>] [/chemin/sortie]
#
# Sans argument (équivalent à AUTO), détection automatique pour le SAN :
#   - CN = FQDN (hostname + suffixe DHCP / DNS résolver) lorsque c’est déductible ;
#   - Sinon CN = ipxe-manager (comportement historique).
#   - IPv4 primaire (« route ») ajoutée en IP: dans le SAN.
#
# Sinon (mode manuel) : une entrée IP: ou DNS: principale (comme les anciennes versions).
#
# Variables optionnelles :
#   export IPXE_TLS_EXTRA_SAN="10.0.0.12,autre.dns.local"
#
# Dépendances : openssl, hostname, awk, ip (iproute2) ; optionnels : resolvectl, nmcli.
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

# shellcheck disable=SC2018
_detect_hostname_short() {
  local h
  h=$(hostname -s 2>/dev/null || hostname 2>/dev/null || printf '')
  h=$(_trim_ws "$h")
  case "${h,,}" in
    '' | '(none)' | localhost) ;;
    *)
      if [[ "$h" == *.* ]]; then h="${h%%.*}"; fi
      printf '%s' "$h"
      ;;
  esac
}

_resolvectl_dns_domain() {
  command -v resolvectl >/dev/null 2>&1 || return 1
  local line dom
  while IFS= read -r line; do
    [[ "$line" =~ DNS[\ ]Domain:|Current[\ ]DNS[\ ]Domain: ]] || continue
    dom="${line#*:}"
    dom=$(_trim_ws "$dom")
    dom="${dom/#\~/}"
    dom="${dom%%[[:space:]]*}"
    dom="${dom%%;*}"
    [[ -z "$dom" || "$dom" == "." ]] && continue
    printf '%s' "$dom"
    return 0
  done < <(resolvectl status 2>/dev/null || true)
  return 1
}

_nmcli_ip4_domain() {
  command -v nmcli >/dev/null 2>&1 || return 1
  local ifc d
  ifc=$(ip -4 route show default 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
  [[ -z "$ifc" ]] && return 1
  d=$(nmcli -g IP4.DOMAIN device show "$ifc" 2>/dev/null | head -1 || true)
  d=$(_trim_ws "${d//$'\n'/ }")
  [[ -z "$d" ]] && return 1
  printf '%s' "$d"
}

_resolv_search_domain() {
  local d=""
  [[ -r /etc/resolv.conf ]] || return 1
  d="$(awk '!/^#/ && /^domain[\t ]/ { print $2; exit }' /etc/resolv.conf)"
  d=$(_trim_ws "$d")
  if [[ -z "$d" || "${d,,}" == localdomain ]]; then
    d="$(awk '!/^#/ && /^search[\t ]/ {
      for (i = 2; i <= NF; i++)
        if (length($i) && $i != "localdomain") { print $i; exit }
    }' /etc/resolv.conf)"
    d=$(_trim_ws "$d")
  fi
  [[ -n "$d" && "${d,,}" != localdomain ]] && printf '%s' "$d"
}

_dhcp_lease_domain_name() {
  local f d
  for f in \
    /var/lib/dhcp/dhclient*.lease* \
    /var/lib/dhcp/*.lease \
    ; do
    for path in $f; do
      [[ -r "$path" ]] || continue
      d="$(grep -Eho 'option domain-name\s+"([^"]+)"' "$path" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true)"
      [[ -z "$d" ]] && continue
      d=$(_trim_ws "$d")
      [[ -n "$d" ]] && printf '%s' "$d" && return 0
    done
  done
  return 1
}

_detect_fqdn() {
  local f short dom
  if command -v hostname >/dev/null 2>&1; then
    f=$(hostname --fqdn 2>/dev/null || true)
    [[ -z "$f" ]] && f=$(hostname -f 2>/dev/null || true)
  fi
  f=$(_trim_ws "$f")
  if [[ -n "$f" ]] && [[ "$f" != localhost ]] && [[ "$f" != localhost.localdomain ]] && [[ "$f" == *.* ]]; then
    printf '%s' "$f"
    return 0
  fi
  short=$(_detect_hostname_short)
  dom=""
  dom=$( _resolvectl_dns_domain || printf '' )
  [[ -z "$dom" ]] && dom=$( _nmcli_ip4_domain || printf '' )
  [[ -z "$dom" ]] && dom=$( _resolv_search_domain || printf '' )
  [[ -z "$dom" ]] && dom=$( _dhcp_lease_domain_name || printf '' )
  dom=$(_trim_ws "$dom")
  if [[ -n "$short" && -n "$dom" ]]; then
    printf '%s' "${short}.${dom}"
    return 0
  fi
  return 1
}

_detect_default_ipv4() {
  local ip
  ip=$(hostname -I 2>/dev/null || true); ip="${ip%% *}"
  ip=$(_trim_ws "$ip")
  if [[ -n "$ip" ]] && [[ ! "$ip" =~ ^127\. ]]; then printf '%s' "$ip"; return 0; fi
  if command -v ip >/dev/null 2>&1; then
    ip=$(ip -4 route get 8.8.8.8 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit } }' || true)
    ip=$(_trim_ws "$ip")
    if [[ -n "$ip" ]] && [[ ! "$ip" =~ ^127\. ]]; then printf '%s' "$ip"; return 0; fi
  fi
  return 1
}

SAN_PRIMARY="${1:-AUTO}"
SAN_PRIMARY_LOWER=$(printf '%s' "$SAN_PRIMARY" | tr '[:upper:]' '[:lower:]')
if [[ "$SAN_PRIMARY_LOWER" == "auto" ]]; then
  SAN_PRIMARY="AUTO"
fi

OUT_DIR="${2:-/srv/ipxe/certs/ipxe-manager}"

SAN_PARTS=( "DNS:localhost" "DNS:ipxe-manager" )
_cn_out="ipxe-manager"
_detect_fq=""
_detect_ip=""
_log_auto=""

if [[ "$SAN_PRIMARY" == "AUTO" ]]; then
  _detect_fq=$( _detect_fqdn || printf '' )
  _detect_ip=$( _detect_default_ipv4 || printf '' )
  [[ -n "$_detect_fq" ]] && SAN_PARTS+=( "DNS:${_detect_fq}" )
  [[ -n "$_detect_ip" ]] && SAN_PARTS+=( "IP:${_detect_ip}" )
  if [[ -n "$_detect_fq" ]]; then
    _cn_out="$_detect_fq"
    _log_auto="AUTO : CN/SAN principaux = ${_detect_fq}"
    [[ -n "$_detect_ip" ]] && _log_auto+=", IPv4=${_detect_ip}"
  else
    _log_auto="AUTO : FQDN non résolu depuis DHCP/resolver → CN=${_cn_out}"
    [[ -n "$_detect_ip" ]] && _log_auto+=", IPv4 dans SAN=${_detect_ip}"
    [[ -z "$_detect_ip" ]] && _log_auto+=", aucune IPv4 détectée (vérifie réseau)"
  fi
else
  if _is_ip_literal "${SAN_PRIMARY}"; then
    SAN_PARTS+=( "IP:${SAN_PRIMARY}" )
    _cn_out="ipxe-manager"
  else
    SAN_PARTS+=( "DNS:${SAN_PRIMARY}" )
    _cn_out="$SAN_PRIMARY"
  fi
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

declare -a SAN_UNIQ
declare -A _seenSAN
for p in "${SAN_PARTS[@]}"; do
  if [[ -z "${_seenSAN[$p]+x}" ]]; then
    _seenSAN[$p]=1
    SAN_UNIQ+=("$p")
  fi
done

SAN_LINE=$(IFS=','; printf '%s' "${SAN_UNIQ[*]}")

# Échapper « / » dans le CN pour openssl -subj
_cn_openssl="${_cn_out//\//\\/}"

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
  -subj "/CN=${_cn_openssl}/O=iPXE Manager"

openssl x509 -req -days 825 -sha256 \
  -in "$CSR" -signkey "$KEY" -out "$CRT" \
  -extfile "$EXT"

rm -f "$CSR" "$EXT"
chmod 600 "$KEY"
chmod 644 "$CRT"

echo "TLS — $CRT + $KEY (CN=$_cn_out)"
echo "SAN : $SAN_LINE"
[[ -n "$_log_auto" ]] && echo "Note : ${_log_auto}"
