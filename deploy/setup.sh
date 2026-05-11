#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Installation complète
# Debian 12 / Ubuntu 24.04
# Usage : bash setup.sh [IP_DU_SERVEUR]
# ============================================================
set -euo pipefail

SERVER_IP="${1:-$(hostname -I | awk '{print $1}')}"
REPO_URL="https://github.com/mrlele35/ipxe-manager.git"
APP_DIR="/srv/ipxe/app"
DATA_DIR="/srv/ipxe"
VENV="/srv/ipxe/venv"
APP_USER="ipxe"
LOG_DIR="/var/log/ipxe-manager"

echo "======================================================"
echo "  iPXE Manager — Installation"
echo "  IP : $SERVER_IP"
echo "======================================================"

# ── 1. Paquets système ────────────────────────────────────
apt-get update -qq
apt-get install -y -qq \
    git nginx tftpd-hpa redis-server \
    python3 python3-venv python3-pip \
    p7zip-full wimtools genisoimage xorriso \
    curl wget unzip rsync ca-certificates

# ── 2. Utilisateur système ────────────────────────────────
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
    echo "  Utilisateur $APP_USER créé."
fi

# ── 3. Arborescence des données ───────────────────────────
mkdir -p \
    "$DATA_DIR/tftpboot" \
    "$DATA_DIR/http/menus" \
    "$DATA_DIR/http/boot" \
    "$DATA_DIR/http/configs" \
    "$DATA_DIR/isos" \
    "$LOG_DIR"

# ── 4. Clone du repo dans /srv/ipxe/app ──────────────────
if [ -d "$APP_DIR/.git" ]; then
    echo "  Repo déjà présent — git pull…"
    git -C "$APP_DIR" pull origin main
else
    echo "  Clonage du repo…"
    rm -rf "$APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
fi

# ── 5. Environnement Python ───────────────────────────────
echo "  Création du virtualenv…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip wheel
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "  Dépendances Python installées."

# ── 6. Fichier .env ───────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/.env" <<EOF
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
    echo "  .env créé (mot de passe : admin)"
else
    echo "  .env déjà présent — conservé."
fi

# ── 7. Initialisation de la base de données ──────────────
echo "  Initialisation de la base de données…"
cd "$APP_DIR"
"$VENV/bin/python" -c "
from app.database import init_db
from app.models.models import OsType
from app.database import SessionLocal
import sys, os
sys.path.insert(0, '.')
os.chdir('$APP_DIR')
init_db()
db = SessionLocal()
defaults = [
    ('windows', 'Windows',    'bi-windows', 'windows'),
    ('ubuntu',  'Ubuntu',     'bi-ubuntu',  'linux'),
    ('debian',  'Debian',     'bi-hdd',     'linux'),
    ('centos',  'CentOS',     'bi-hdd',     'linux'),
    ('rocky',   'Rocky Linux','bi-hdd',     'linux'),
    ('proxmox', 'Proxmox VE', 'bi-server',  'linux'),
]
for slug, label, icon, boot_type in defaults:
    if not db.query(OsType).filter(OsType.slug==slug).first():
        db.add(OsType(slug=slug, label=label, icon=icon, boot_type=boot_type))
db.commit()
db.close()
print('  Base initialisée.')
"

# ── 8. TFTP — firmwares iPXE ──────────────────────────────
echo "  Téléchargement des firmwares iPXE…"
wget -q -O "$DATA_DIR/tftpboot/ipxe.efi"       https://boot.ipxe.org/ipxe.efi       || echo "  ! ipxe.efi : échec (réseau ?)"
wget -q -O "$DATA_DIR/tftpboot/undionly.kpxe"  https://boot.ipxe.org/undionly.kpxe  || echo "  ! undionly.kpxe : échec"
wget -q -O "$DATA_DIR/http/wimboot"             https://github.com/ipxe/wimboot/releases/latest/download/wimboot || echo "  ! wimboot : échec"

# Fichier de chainload TFTP → HTTP
cat > "$DATA_DIR/tftpboot/boot.ipxe" <<EOF
#!ipxe
dhcp
chain http://$SERVER_IP/menus/menu.ipxe || shell
EOF

# ── 9. Configuration tftpd-hpa ────────────────────────────
cat > /etc/default/tftpd-hpa <<EOF
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="$DATA_DIR/tftpboot"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure --create --blocksize 1468"
EOF

# ── 10. Configuration Nginx ───────────────────────────────
cat > /etc/nginx/sites-available/ipxe-manager <<'NGINX'
server {
    listen 80;
    server_name _;

    access_log /var/log/nginx/ipxe-manager.access.log;
    error_log  /var/log/nginx/ipxe-manager.error.log;

    sendfile on;
    tcp_nopush on;
    client_max_body_size 60G;

    location /menus/ {
        alias /srv/ipxe/http/menus/;
        add_header Content-Type text/plain;
        expires -1;
    }
    location /boot/ {
        alias /srv/ipxe/http/boot/;
        sendfile on;
        expires 1d;
    }
    location /configs/ {
        alias /srv/ipxe/http/configs/;
        add_header Content-Type text/plain;
        expires -1;
    }
    location /wimboot {
        alias /srv/ipxe/http/wimboot;
        add_header Content-Type application/octet-stream;
    }
    location /static/ {
        alias /srv/ipxe/app/static/;
        expires 7d;
    }
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_read_timeout  3600;
        proxy_send_timeout  3600;
        proxy_request_buffering off;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/ipxe-manager /etc/nginx/sites-enabled/ipxe-manager
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── 11. Services systemd ──────────────────────────────────
cat > /etc/systemd/system/ipxe-manager.service <<EOF
[Unit]
Description=iPXE Manager — FastAPI
After=network.target redis.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/celery-worker.service <<EOF
[Unit]
Description=iPXE Manager — Celery worker
After=network.target redis.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# ── 12. Permissions ───────────────────────────────────────
chown -R "$APP_USER:$APP_USER" "$DATA_DIR" "$LOG_DIR"
chmod 640 "$APP_DIR/.env"

# ── 13. Démarrage des services ────────────────────────────
systemctl daemon-reload
systemctl enable --now redis-server
systemctl enable --now tftpd-hpa
systemctl enable --now nginx
systemctl enable --now ipxe-manager
systemctl enable --now celery-worker

# ── 14. Résumé ────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Installation terminée !"
echo "======================================================"
echo ""
echo "  Interface web : http://$SERVER_IP/"
echo "  Login         : admin  (changer dans Paramètres !)"
echo "  Menu iPXE     : http://$SERVER_IP/menus/menu.ipxe"
echo ""
echo "  Mise à jour future :"
echo "    cd $APP_DIR && git pull && systemctl restart ipxe-manager"
echo ""
systemctl is-active --quiet ipxe-manager  && echo "  [OK] ipxe-manager"  || echo "  [!!] ipxe-manager FAILED"
systemctl is-active --quiet celery-worker && echo "  [OK] celery-worker" || echo "  [!!] celery-worker FAILED"
systemctl is-active --quiet nginx         && echo "  [OK] nginx"         || echo "  [!!] nginx FAILED"
systemctl is-active --quiet tftpd-hpa     && echo "  [OK] tftpd-hpa"     || echo "  [!!] tftpd-hpa FAILED"
systemctl is-active --quiet redis-server  && echo "  [OK] redis"         || echo "  [!!] redis FAILED"
echo ""
