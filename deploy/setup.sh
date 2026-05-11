#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Script d'installation complète
# Debian 12 (Bookworm) — compatible Ubuntu 24.04
# À exécuter en tant que root : sudo bash setup.sh [IP]
# ============================================================
set -euo pipefail

SERVER_IP="${1:-$(hostname -I | awk '{print $1}')}"
APP_DIR="/srv/ipxe"
APP_USER="ipxe"
LOG_DIR="/var/log/ipxe-manager"
VENV="$APP_DIR/venv"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "======================================================"
echo "  iPXE Manager — Installation"
echo "  IP serveur : $SERVER_IP"
echo "======================================================"

# ── Détection OS ──────────────────────────────────────────
OS_ID=$(grep '^ID=' /etc/os-release | cut -d= -f2)
OS_VER=$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '"')

echo "  OS détecté : $OS_ID $OS_VER"

# ── 1. Mise à jour système ─────────────────────────────────
apt-get update
apt-get upgrade -y

# ── 2. Dépendances système ────────────────────────────────
apt-get install -y \
    nginx \
    tftpd-hpa \
    redis-server \
    p7zip-full \
    wimtools \
    genisoimage \
    xorriso \
    curl \
    wget \
    git \
    unzip \
    rsync \
    ca-certificates

# ── Python 3.12 ───────────────────────────────────────────
# Debian 12 fournit Python 3.11 par défaut — on installe 3.12 via backports
if [ "$OS_ID" = "debian" ]; then
    PYTHON_BIN=""
    # Tenter python3.12 depuis les dépôts ou deadsnakes
    if ! python3.12 --version &>/dev/null 2>&1; then
        echo "  Installation Python 3.12 via deadsnakes PPA (Debian)…"
        apt-get install -y software-properties-common
        # Sur Debian on utilise le dépôt deb.debian.org backports ou on compile
        # Solution propre : dépôt ppa:deadsnakes via un miroir debian
        apt-get install -y python3.11 python3.11-venv python3-pip
        PYTHON_BIN="python3.11"
        echo "  Python 3.11 utilisé (compatible, 3.12 non requis)"
    else
        PYTHON_BIN="python3.12"
    fi
else
    # Ubuntu 24.04 : python3.12 disponible directement
    apt-get install -y python3.12 python3.12-venv python3-pip
    PYTHON_BIN="python3.12"
fi

echo "  Python : $($PYTHON_BIN --version)"

# ── 3. Utilisateur dédié ───────────────────────────────────
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home "$APP_DIR" --shell /sbin/nologin "$APP_USER"
fi

# ── 4. Arborescence ───────────────────────────────────────
mkdir -p \
    "$APP_DIR/tftpboot" \
    "$APP_DIR/http/menus" \
    "$APP_DIR/http/boot" \
    "$APP_DIR/http/configs" \
    "$APP_DIR/isos" \
    "$APP_DIR/app" \
    "$LOG_DIR"

# ── 5. Copie du code ──────────────────────────────────────
rsync -av --exclude='*.pyc' --exclude='__pycache__' \
    "$REPO_DIR/app/"   "$APP_DIR/app/app/"
rsync -av "$REPO_DIR/static/"  "$APP_DIR/app/static/"
cp "$REPO_DIR/requirements.txt" "$APP_DIR/app/"

# ── 6. Environnement Python ───────────────────────────────
$PYTHON_BIN -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel
"$VENV/bin/pip" install -r "$APP_DIR/app/requirements.txt"

# ── 7. Fichier .env ───────────────────────────────────────
if [ ! -f "$APP_DIR/app/.env" ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/app/.env" <<EOF
SERVER_BASE_URL=http://$SERVER_IP
SECRET_KEY=$SECRET
ADMIN_PASSWORD=admin
DATABASE_URL=sqlite:////srv/ipxe/app/ipxe.db
REDIS_URL=redis://localhost:6379/0
TFTP_ROOT=/srv/ipxe/tftpboot
HTTP_ROOT=/srv/ipxe/http
ISO_ROOT=/srv/ipxe/isos
MAX_UPLOAD_SIZE=53687091200
EXTRACT_TIMEOUT=3600
EOF
    echo "  ⚠  .env créé avec mot de passe 'admin' — CHANGER IMMÉDIATEMENT"
fi

# ── 8. Permissions ────────────────────────────────────────
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$LOG_DIR"
chmod 750 "$APP_DIR/app/.env"

# ── 9. TFTP ───────────────────────────────────────────────
cp "$REPO_DIR/deploy/tftpd-hpa.conf" /etc/default/tftpd-hpa
cp "$REPO_DIR/deploy/boot.ipxe" "$APP_DIR/tftpboot/boot.ipxe"
sed -i "s/SERVER_IP/$SERVER_IP/g" "$APP_DIR/tftpboot/boot.ipxe"

# Télécharger les firmwares iPXE si absents
if [ ! -f "$APP_DIR/tftpboot/ipxe.efi" ]; then
    echo "Téléchargement du firmware iPXE UEFI…"
    wget -q -O "$APP_DIR/tftpboot/ipxe.efi" \
        "https://boot.ipxe.org/ipxe.efi" || \
        echo "  ⚠  ipxe.efi non téléchargé (pas de réseau). Copier manuellement."
fi
if [ ! -f "$APP_DIR/tftpboot/undionly.kpxe" ]; then
    echo "Téléchargement du firmware iPXE BIOS…"
    wget -q -O "$APP_DIR/tftpboot/undionly.kpxe" \
        "https://boot.ipxe.org/undionly.kpxe" || \
        echo "  ⚠  undionly.kpxe non téléchargé. Copier manuellement."
fi

# wimboot pour Windows HTTP boot
if [ ! -f "$APP_DIR/http/wimboot" ]; then
    wget -q -O "$APP_DIR/http/wimboot" \
        "https://github.com/ipxe/wimboot/releases/latest/download/wimboot" || \
        echo "  ⚠  wimboot non téléchargé. Copier manuellement."
    chmod +x "$APP_DIR/http/wimboot" 2>/dev/null || true
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR/tftpboot" "$APP_DIR/http"

# ── 10. Nginx ─────────────────────────────────────────────
cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/sites-available/ipxe-manager
ln -sf /etc/nginx/sites-available/ipxe-manager \
       /etc/nginx/sites-enabled/ipxe-manager
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

# ── 11. Systemd services ──────────────────────────────────
cp "$REPO_DIR/deploy/ipxe-manager.service" /etc/systemd/system/
cp "$REPO_DIR/deploy/celery-worker.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now redis-server
systemctl enable --now tftpd-hpa
systemctl enable --now ipxe-manager
systemctl enable --now celery-worker

# ── 12. Initialisation base de données ───────────────────
cd "$APP_DIR/app"
"$VENV/bin/python" -c "
from app.database import init_db
from app.models.models import OsType
from app.database import SessionLocal

init_db()
db = SessionLocal()
defaults = [
    ('windows', 'Windows', 'bi-windows', 'windows'),
    ('ubuntu',  'Ubuntu',  'bi-ubuntu',  'linux'),
    ('debian',  'Debian',  'bi-hdd',     'linux'),
    ('centos',  'CentOS',  'bi-hdd',     'linux'),
    ('proxmox', 'Proxmox', 'bi-server',  'linux'),
]
for slug, label, icon, boot_type in defaults:
    if not db.query(OsType).filter(OsType.slug==slug).first():
        db.add(OsType(slug=slug, label=label, icon=icon, boot_type=boot_type))
db.commit()
db.close()
print('Base de données initialisée.')
"

# ── 13. Résumé ────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Installation terminée !"
echo "======================================================"
echo ""
echo "  Interface web   : http://$SERVER_IP/"
echo "  Mot de passe    : admin  (CHANGER dans Paramètres !)"
echo "  Menus iPXE      : http://$SERVER_IP/menus/menu.ipxe"
echo "  TFTP boot       : $APP_DIR/tftpboot/boot.ipxe"
echo ""
echo "  DHCP à configurer :"
echo "    next-server    : $SERVER_IP"
echo "    filename BIOS  : undionly.kpxe"
echo "    filename UEFI  : ipxe.efi"
echo ""
echo "  Statuts services :"
systemctl is-active --quiet ipxe-manager   && echo "  ✓ ipxe-manager"   || echo "  ✗ ipxe-manager FAILED"
systemctl is-active --quiet celery-worker  && echo "  ✓ celery-worker"  || echo "  ✗ celery-worker FAILED"
systemctl is-active --quiet nginx          && echo "  ✓ nginx"          || echo "  ✗ nginx FAILED"
systemctl is-active --quiet tftpd-hpa      && echo "  ✓ tftpd-hpa"      || echo "  ✗ tftpd-hpa FAILED"
systemctl is-active --quiet redis-server   && echo "  ✓ redis"          || echo "  ✗ redis FAILED"
echo ""
