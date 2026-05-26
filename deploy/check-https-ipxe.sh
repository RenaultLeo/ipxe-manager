#!/usr/bin/env bash
# Diagnostic HTTPS + chaîne PXE/iPXE (certificats, Nginx, menus, firmware, embed).
# Usage : sudo bash deploy/check-https-ipxe.sh [IP_ou_FQDN]
#         sudo bash /srv/ipxe/app/deploy/check-https-ipxe.sh
# (ne pas lancer avec « sh » : utiliser « bash »)
if [ -z "${BASH_VERSION:-}" ]; then
  echo "Lancez avec bash : sudo bash $0 $*" >&2
  exit 2
fi
set -eo pipefail

APP_DIR="${APP_DIR:-/srv/ipxe/app}"
VENV="${VENV:-/srv/ipxe/venv}"
DATA_DIR="${DATA_DIR:-/srv/ipxe}"
SSL_DIR="${SSL_DIR:-/srv/ipxe/ssl}"
TFTP_ROOT="${TFTP_ROOT:-/srv/ipxe/tftpboot}"
HTTP_ROOT="${HTTP_ROOT:-/srv/ipxe/http}"
BUILD_DIR="${BUILD_DIR:-/srv/ipxe/build}"
IPXE_SRC="${BUILD_DIR}/ipxe-src"

HOST="${1:-}"
if [ -z "$HOST" ]; then
  HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

FAIL=0
WARN=0
OK=0

red() { printf '\033[0;31m%s\033[0m\n' "$*"; }
grn() { printf '\033[0;32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[0;33m%s\033[0m\n' "$*"; }
hdr() { printf '\n\033[1m══ %s\033[0m\n' "$*"; }

ok() { grn "  ✓ $*"; OK=$((OK + 1)); }
ko() { red "  ✗ $*"; FAIL=$((FAIL + 1)); }
warn() { ylw "  ⚠ $*"; WARN=$((WARN + 1)); }

read_env_url() {
  if [ -f "$APP_DIR/.env" ]; then
    grep -E '^SERVER_BASE_URL=' "$APP_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
  fi
}

ENV_URL="$(read_env_url)"
ENV_URL="${ENV_URL:-}"

DB_URL=""
if [ -x "$VENV/bin/python" ] && [ -d "$APP_DIR/app" ]; then
  DB_URL="$(cd "$APP_DIR" && "$VENV/bin/python" -c 'from app.config import resolve_server_base_url; print(resolve_server_base_url())' 2>/dev/null)" || DB_URL=""
fi

BASE_URL="${ENV_URL:-$DB_URL}"
if [ -z "$BASE_URL" ] && [ -n "$HOST" ]; then
  BASE_URL="https://${HOST}"
  warn "SERVER_BASE_URL absent - on suppose $BASE_URL"
fi

SCHEME=""
BASE_HOST=""
if [ -n "$BASE_URL" ]; then
  SCHEME="${BASE_URL%%://*}"
  rest="${BASE_URL#*://}"
  BASE_HOST="${rest%%/*}"
fi

hdr "iPXE Manager — checkup HTTPS / PXE"
echo "  Date      : $(date -Is 2>/dev/null || date)"
echo "  Hôte test : ${HOST:-?}"
echo "  URL .env  : ${ENV_URL:-n/a}"
echo "  URL BDD   : ${DB_URL:-n/a}"
echo "  IP locale : $(hostname -I 2>/dev/null | tr '\n' ' ')"

# ── 1. TLS ───────────────────────────────────────────────────────────────────
hdr "1. Certificats TLS ($SSL_DIR)"
for f in ca.crt server.crt server.key; do
  if [ -f "$SSL_DIR/$f" ]; then
    ok "$f présent ($(stat -c%s "$SSL_DIR/$f" 2>/dev/null || stat -f%z "$SSL_DIR/$f") octets)"
  else
    ko "$f absent — lancer : sudo bash $APP_DIR/deploy/generate-tls-cert.sh $HOST"
  fi
done
if [ -f "$SSL_DIR/server.crt" ] && command -v openssl >/dev/null 2>&1; then
  exp="$(openssl x509 -enddate -noout -in "$SSL_DIR/server.crt" 2>/dev/null | cut -d= -f2-)"
  subj="$(openssl x509 -subject -noout -in "$SSL_DIR/server.crt" 2>/dev/null)"
  echo "      $subj"
  echo "      expire : $exp"
  if openssl x509 -in "$SSL_DIR/server.crt" -text -noout 2>/dev/null | grep -q "IP Address:${HOST}\|DNS:${HOST}"; then
    ok "SAN contient $HOST"
  else
    warn "SAN du certificat ne mentionne peut‑être pas $HOST (vérifier openssl x509 -text)"
  fi
fi

# ── 2. Nginx ─────────────────────────────────────────────────────────────────
hdr "2. Nginx HTTPS"
if command -v nginx >/dev/null 2>&1; then
  if nginx -t 2>&1 | grep -qi successful; then
    ok "nginx -t OK"
  else
    ko "nginx -t échoue"
    nginx -t 2>&1 | sed 's/^/      /'
  fi
  if ss -tln 2>/dev/null | grep -q ':443 '; then
    ok "écoute TCP :443"
  else
    ko "rien n’écoute sur :443"
  fi
  if [ -L /etc/nginx/sites-enabled/ipxe-manager ] || [ -f /etc/nginx/sites-enabled/ipxe-manager ]; then
    if grep -q ssl_certificate "$APP_DIR/deploy/nginx-https.conf" 2>/dev/null; then
      if grep -q 'listen 443' /etc/nginx/sites-available/ipxe-manager 2>/dev/null; then
        ok "site ipxe-manager avec listen 443"
      else
        warn "sites-available/ipxe-manager sans listen 443 — reprendre nginx-https.conf"
      fi
    fi
  else
    warn "site nginx ipxe-manager non activé"
  fi
else
  ko "nginx non installé"
fi

# ── 3. URL applicative ───────────────────────────────────────────────────────
hdr "3. URL serveur (menus / embed)"
if [ "$SCHEME" = "https" ]; then
  ok "SERVER_BASE_URL en https ($BASE_URL)"
elif [ "$SCHEME" = "http" ]; then
  ko "SERVER_BASE_URL encore en HTTP : $BASE_URL — enable-https.sh ou Paramètres"
else
  ko "SERVER_BASE_URL invalide : ${BASE_URL:-vide}"
fi
if [ -n "$HOST" ] && [ -n "$BASE_HOST" ] && [ "$BASE_HOST" != "$HOST" ]; then
  warn "IP argument ($HOST) ≠ hôte dans URL ($BASE_HOST) — menus/embed peuvent pointer ailleurs"
fi

# ── 4. HTTP(S) sonde ─────────────────────────────────────────────────────────
hdr "4. Sondes HTTP(S) depuis ce serveur"
MENU_URL="${BASE_URL%/}/menus/menu.ipxe"
if command -v curl >/dev/null 2>&1; then
  code="$(curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$MENU_URL" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ]; then
    ok "curl -k $MENU_URL → HTTP 200"
  else
    ko "curl -k $MENU_URL → HTTP $code"
  fi
  code_login="$(curl -k -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "${BASE_URL%/}/login" 2>/dev/null || echo 000)"
  if [ "$code_login" = "200" ]; then
    ok "curl -k ${BASE_URL%/}/login → HTTP 200"
  else
    warn "curl login → HTTP $code_login"
  fi
else
  warn "curl absent — apt install curl"
fi

# ── 5. Fichiers menu / boot ──────────────────────────────────────────────────
hdr "5. Menus sur disque ($HTTP_ROOT/menus)"
MENU_FILE="$HTTP_ROOT/menus/menu.ipxe"
if [ -f "$MENU_FILE" ]; then
  ok "menu.ipxe présent"
  head -8 "$MENU_FILE" | sed 's/^/      /'
  if grep -qE '^https?://' "$MENU_FILE" 2>/dev/null; then
    first_url="$(grep -oE 'https?://[^ )]+' "$MENU_FILE" | head -1)"
    if echo "$first_url" | grep -q '^https://'; then
      ok "menu.ipxe référence HTTPS ($first_url …)"
    else
      ko "menu.ipxe contient encore HTTP : $first_url — Régénérer tous les menus (UI)"
    fi
  fi
  if [ -n "$BASE_HOST" ] && grep -q "$BASE_HOST" "$MENU_FILE"; then
    ok "menu.ipxe mentionne l’hôte $BASE_HOST"
  else
    warn "menu.ipxe ne contient pas $BASE_HOST (ancienne IP ?)"
  fi
  for old in 192.168.2.6 192.168.2.8; do
    if grep -q "$old" "$MENU_FILE" 2>/dev/null; then
      warn "menu.ipxe contient encore l’IP exemple $old"
    fi
  done
else
  ko "menu.ipxe absent — régénérer depuis Menus iPXE"
fi

BOOT_IPXE="$TFTP_ROOT/boot.ipxe"
if [ -f "$BOOT_IPXE" ]; then
  echo "      boot.ipxe :"
  sed -n '1,12p' "$BOOT_IPXE" | sed 's/^/      /'
  if grep -q 'SERVER_IP' "$BOOT_IPXE"; then
    ko "boot.ipxe contient encore SERVER_IP — remplacer par l’IP réelle ou régénérer"
  elif grep -q '^http://' "$BOOT_IPXE"; then
    ko "boot.ipxe chain encore en HTTP — mettre https:// ou passer par firmware embed uniquement"
  elif grep -q '^https://' "$BOOT_IPXE"; then
    ok "boot.ipxe chain en HTTPS"
  else
    warn "boot.ipxe : pas de chain http(s) évident (boot peut être 100 % embed firmware)"
  fi
else
  warn "boot.ipxe absent dans TFTP (normal si DHCP donne directement l’URL du menu)"
fi

# ── 6. embed.ipxe (sources compilation) ─────────────────────────────────────
hdr "6. embed.ipxe (sources iPXE avant make)"
EMBED="$IPXE_SRC/src/embed.ipxe"
if [ -f "$EMBED" ]; then
  ok "embed.ipxe présent"
  sed -n '1,20p' "$EMBED" | sed 's/^/      /'
  embed_chain="$(grep -E '^chain ' "$EMBED" | head -1 || true)"
  if echo "$embed_chain" | grep -q 'https://'; then
    ok "embed chain en HTTPS"
  else
    ko "embed chain pas en HTTPS : $embed_chain"
  fi
  if [ -n "$MENU_URL" ] && grep -qF "$MENU_URL" "$EMBED"; then
    ok "embed pointe vers $MENU_URL"
  else
    warn "embed ≠ URL attendue $MENU_URL — recompiler firmware"
  fi
else
  ko "embed.ipxe absent — jamais compilé ou IPXE_SRC=$IPXE_SRC incorrect"
fi

# ── 7. Patch DOWNLOAD_PROTO_HTTPS ────────────────────────────────────────────
hdr "7. Patch sources iPXE (DOWNLOAD_PROTO_HTTPS)"
GENERAL_H="$IPXE_SRC/src/config/general.h"
if [ -f "$GENERAL_H" ]; then
  if grep -qE '^[[:space:]]*#define[[:space:]]+DOWNLOAD_PROTO_HTTPS' "$GENERAL_H"; then
    ok "DOWNLOAD_PROTO_HTTPS défini dans general.h"
  elif grep -q 'undef DOWNLOAD_PROTO_HTTPS' "$GENERAL_H"; then
    ko "DOWNLOAD_PROTO_HTTPS encore #undef — compilation = « not supported » sur https://"
  else
    warn "general.h : état HTTPS ambigu — ouvrir $GENERAL_H"
  fi
else
  warn "Sources iPXE absentes ($IPXE_SRC) — clone au premier compile firmware"
fi

# ── 8. Binaires TFTP ─────────────────────────────────────────────────────────
hdr "8. Firmware TFTP ($TFTP_ROOT)"
for bin in undionly.kpxe ipxe.efi snponly.efi; do
  p="$TFTP_ROOT/$bin"
  if [ -f "$p" ]; then
    sz="$(stat -c%s "$p" 2>/dev/null || stat -f%z "$p")"
    mt="$(stat -c%y "$p" 2>/dev/null || stat -f%Sm "$p")"
    ok "$bin ($sz o, modif $mt)"
  else
    ko "$bin absent"
  fi
done

# ── 9. Chaîne dans le binaire (strings) ───────────────────────────────────────
hdr "9. URL embarquée dans undionly.kpxe (strings)"
KPXE="$TFTP_ROOT/undionly.kpxe"
STR_TMP=""
if [ -f "$KPXE" ] && command -v strings >/dev/null 2>&1; then
  STR_TMP="$(mktemp /tmp/ipxe-strings.XXXXXX)"
  strings "$KPXE" 2>/dev/null >"$STR_TMP" || true
  has_dl_https=0
  has_https_url=0
  grep -q 'DOWNLOAD_PROTO_HTTPS' "$STR_TMP" 2>/dev/null && has_dl_https=1 || true
  grep -qi 'https://' "$STR_TMP" 2>/dev/null && has_https_url=1 || true
  if [ "$has_dl_https" -eq 1 ]; then
    ok "symbole DOWNLOAD_PROTO_HTTPS visible (build avec HTTPS)"
  elif [ "$has_https_url" -eq 1 ]; then
    ok "chaine https:// trouvee dans le binaire (embed/menu)"
  else
    warn "pas de https:// ni DOWNLOAD_PROTO_HTTPS dans strings - firmware probablement HTTP-only"
    ko "cause typique du message https://.../menu.ipxe not supported"
  fi
  emb_url="$(grep -E 'https?://[^[:space:]]+/menus/menu\.ipxe' "$STR_TMP" 2>/dev/null | head -1)" || emb_url=""
  if [ -n "$emb_url" ]; then
    echo "      URL embed : $emb_url"
    if [ -n "$MENU_URL" ] && [ "$emb_url" = "$MENU_URL" ]; then
      ok "URL embed = menu attendu"
    else
      warn "URL embed different de $MENU_URL - recompiler avec la bonne IP"
    fi
  else
    warn "aucune URL .../menus/menu.ipxe lisible dans le binaire (strings)"
  fi
  for old in 192.168.2.6 192.168.2.8; do
    if grep -q "$old" "$STR_TMP" 2>/dev/null; then
      warn "binaire contient encore IP exemple $old"
    fi
  done
  rm -f "$STR_TMP"
else
  warn "strings ou undionly.kpxe indisponible"
fi

# ── 10. Services ─────────────────────────────────────────────────────────────
hdr "10. Services"
for svc in nginx ipxe-manager ipxe-celery tftpd-hpa redis-server; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    ok "$svc actif"
  else
    warn "$svc inactif ou absent"
  fi
done

# ── 11. Dernier log compilation (si présent) ─────────────────────────────────
hdr "11. Aide compilation"
echo "  Recompiler firmware + menus :"
echo "    sudo bash $APP_DIR/deploy/bootstrap-https-firmware.sh ${HOST}"
echo "  Ou UI : Firmware → Recompiler, puis Menus → Régénérer tous"
echo "  Activer HTTPS complet :"
echo "    sudo bash $APP_DIR/deploy/enable-https.sh ${HOST}"

# ── Bilan ─────────────────────────────────────────────────────────────────────
hdr "Bilan"
echo "  OK : $OK   Échecs : $FAIL   Avertissements : $WARN"
if [ "$FAIL" -gt 0 ]; then
  red "  → Corriger les ✗ avant test PXE (surtout embed, general.h, binaire TFTP, URL https)."
  exit 1
fi
if [ "$WARN" -gt 0 ]; then
  ylw "  → Vérifier les ⚠ ; le PXE peut quand même fonctionner."
  exit 0
fi
grn "  → Tout semble cohérent côté serveur — si PXE échoue encore : BIOS vs UEFI, DHCP, autre TFTP."
exit 0
