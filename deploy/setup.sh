#!/usr/bin/env bash
# ============================================================
# iPXE Manager — Installation complète
# Debian 12 / Ubuntu 24.04
# Usage :
#   sudo bash setup.sh [IP_DU_SERVEUR]
#   curl -fsSL https://raw.githubusercontent.com/RenaultLeo/ipxe-manager/main/deploy/setup.sh | sudo bash -s -- IP
#   (l’étape [4] clone le dépôt sous APP_DIR avant pip install — le pipe fonctionne ainsi.)
# ============================================================
set -euo pipefail

SERVER_IP="${1:-$(hostname -I | awk '{print $1}')}"
REPO_URL="https://github.com/RenaultLeo/ipxe-manager.git"
APP_DIR="/srv/ipxe/app"
DATA_DIR="/srv/ipxe"
VENV="/srv/ipxe/venv"
APP_USER="ipxe"
LOG_DIR="/var/log/ipxe-manager"

echo "======================================================"
echo "  iPXE Manager — Installation"
echo "  IP détectée : $SERVER_IP"
echo "======================================================"

# ── 1. Paquets système ────────────────────────────────────────────────────────
echo "[1/15] Installation des paquets système…"
apt-get update -qq
apt-get install -y -qq \
    sudo git curl wget unzip rsync ca-certificates \
    iproute2 procps \
    nginx tftpd-hpa redis-server \
    python3 python3-venv python3-pip nodejs \
    p7zip-full wimtools genisoimage xorriso libarchive-tools \
    samba samba-common-bin nfs-kernel-server \
    build-essential gcc binutils make liblzma-dev mtools \
    isolinux || true   # isolinux peut manquer sur certaines archi

# Désactiver le service AD DC de Samba (non utilisé — on veut juste smbd/nmbd)
systemctl disable --now samba-ad-dc 2>/dev/null || true

# ── 2. Utilisateur système ────────────────────────────────────────────────────
echo "[2/15] Création de l'utilisateur $APP_USER…"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
    echo "  Utilisateur $APP_USER créé."
else
    echo "  Utilisateur $APP_USER déjà présent."
fi

# ── 3. Arborescence des données ───────────────────────────────────────────────
echo "[3/15] Création de l'arborescence…"
mkdir -p \
    "$DATA_DIR/tftpboot" \
    "$DATA_DIR/http/menus" \
    "$DATA_DIR/http/boot" \
    "$DATA_DIR/http/boot/ubuntu" \
    "$DATA_DIR/http/configs" \
    "$DATA_DIR/isos" \
    "$DATA_DIR/build" \
    "$LOG_DIR"

# Permissions publiques sur http/ pour que Nginx puisse servir les fichiers
chmod -R 755 "$DATA_DIR/http"

# ── 4. Clone du repo ──────────────────────────────────────────────────────────
echo "[4/15] Récupération du code source…"
if [ -d "$APP_DIR/.git" ]; then
    echo "  Repo déjà présent — git pull (branche courante / upstream)…"
    git -C "$APP_DIR" pull --ff-only || echo "  ! git pull échoué — conserve la version actuelle."
else
    echo "  Clonage de $REPO_URL…"
    mkdir -p "$(dirname "$APP_DIR")"
    rm -rf "$APP_DIR"
    if git clone -b main --depth 1 "$REPO_URL" "$APP_DIR" 2>/dev/null; then
        echo "  Branche « main » clonée."
    elif git clone -b master --depth 1 "$REPO_URL" "$APP_DIR" 2>/dev/null; then
        echo "  Branche « master » clonée."
    else
        git clone "$REPO_URL" "$APP_DIR"
    fi
fi

# Sudoers + script de relance services (page Supervision)
if [ -f "$APP_DIR/deploy/install-service-sudo.sh" ]; then
    bash "$APP_DIR/deploy/install-service-sudo.sh"
else
    echo "  ! deploy/install-service-sudo.sh absent — relance services UI indisponible jusqu'à install manuel."
fi

# ── 5. Environnement Python ───────────────────────────────────────────────────
echo "[5/15] Création du virtualenv Python…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip wheel
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "  Dépendances Python installées."

# Bundles i18n (DE / ES / IT / PT) : alignés sur app/i18n.py + tools/build_locale_lists.mjs
if command -v node >/dev/null 2>&1; then
  echo "  Régénération des fichiers app/locale_values/*.list.json (Node)…"
  (cd "$APP_DIR" && node tools/extract_en_list.mjs && node tools/build_locale_lists.mjs) \
    || echo "  ! Rebuild i18n échoué — utilisation des fichiers déjà présents dans le dépôt."
else
  echo "  ! Node.js indisponible — les listes i18n du clone Git doivent être complètes."
fi

# ── 6. Fichier .env ───────────────────────────────────────────────────────────
echo "[6/15] Configuration de l'environnement (.env)…"
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
ISO_HTTP_ALIAS=isos-ipxe
BUILD_DIR=/srv/ipxe/build
MAX_UPLOAD_SIZE=53687091200
# Marge disque min. (octets) avant upload ISO — défaut 256 Mo dans l’app si absent (voir UPLOAD_MIN_FREE_BYTES).
# UPLOAD_MIN_FREE_BYTES=268435456
EXTRACT_TIMEOUT=3600
# Ubuntu ISO extraite : racine NFS = HTTP_ROOT/boot/ubuntu/<slug-version> (activer après export NFS)
UBUNTU_NFS_ENABLED=false
UBUNTU_NFS_HOST=
UBUNTU_NFS_MOUNT_OPTS=vers=4,tcp
EOF
    echo "  .env créé — mot de passe par défaut : admin (à changer !)"
else
    echo "  .env déjà présent — conservé."
    # S'assurer que BUILD_DIR est présent dans le .env existant
    grep -q "BUILD_DIR" "$APP_DIR/.env" || echo "BUILD_DIR=/srv/ipxe/build" >> "$APP_DIR/.env"
    grep -q "^ISO_HTTP_ALIAS" "$APP_DIR/.env" || echo "ISO_HTTP_ALIAS=isos-ipxe" >> "$APP_DIR/.env"
    grep -q "UPLOAD_MIN_FREE_BYTES" "$APP_DIR/.env" || printf '\n# Upload ISO — marge disque minimale avant acceptation (défaut app : 268435456 = 256 Mo)\n# UPLOAD_MIN_FREE_BYTES=268435456\n' >> "$APP_DIR/.env"
    grep -q "^UBUNTU_NFS_ENABLED" "$APP_DIR/.env" || {
        printf '\nUBUNTU_NFS_ENABLED=false\nUBUNTU_NFS_HOST=\nUBUNTU_NFS_MOUNT_OPTS=vers=4,tcp\n' >> "$APP_DIR/.env"
    }
fi

# ── 7. Base de données ────────────────────────────────────────────────────────
echo "[7/15] Initialisation de la base de données…"
cd "$APP_DIR"
"$VENV/bin/python" deploy/seed_db.py
echo "  Base initialisée avec tous les OS de base."

# ── 8. Firmwares iPXE (génériques — en attendant la compilation custom) ───────
echo "[8/15] Téléchargement des firmwares iPXE génériques…"
# Ces binaires seront remplacés lors de la compilation depuis /firmware dans l'UI
if [ ! -f "$DATA_DIR/tftpboot/undionly.kpxe" ]; then
    wget -q -O "$DATA_DIR/tftpboot/undionly.kpxe" \
        https://boot.ipxe.org/undionly.kpxe \
        && echo "  undionly.kpxe téléchargé." \
        || echo "  ! undionly.kpxe : échec réseau, à télécharger manuellement."
fi
if [ ! -f "$DATA_DIR/tftpboot/ipxe.efi" ]; then
    wget -q -O "$DATA_DIR/tftpboot/ipxe.efi" \
        https://boot.ipxe.org/ipxe.efi \
        && echo "  ipxe.efi téléchargé." \
        || echo "  ! ipxe.efi : échec réseau, à télécharger manuellement."
fi
if [ ! -f "$DATA_DIR/tftpboot/snponly.efi" ]; then
    wget -q -O "$DATA_DIR/tftpboot/snponly.efi" \
        https://boot.ipxe.org/snponly.efi \
        && echo "  snponly.efi téléchargé (recommandé pour VMs)." \
        || echo "  ! snponly.efi : à compiler via l'interface web (/firmware)."
fi

# wimboot pour le boot Windows PE
if [ ! -f "$DATA_DIR/http/wimboot" ]; then
    wget -q -O "$DATA_DIR/http/wimboot" \
        "https://github.com/ipxe/wimboot/releases/latest/download/wimboot" \
        && echo "  wimboot téléchargé." \
        || echo "  ! wimboot : échec réseau."
fi

# ── 9. TFTP — tftpd-hpa ───────────────────────────────────────────────────────
echo "[9/15] Configuration de tftpd-hpa…"
cat > /etc/default/tftpd-hpa <<EOF
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="$DATA_DIR/tftpboot/"
TFTP_ADDRESS="0.0.0.0:69"
TFTP_OPTIONS="--secure --create --blocksize 1468"
EOF
chmod -R 755 "$DATA_DIR/tftpboot"

# ── 10. Nginx ─────────────────────────────────────────────────────────────────
echo "[10/15] Configuration Nginx…"
if [ ! -f "$APP_DIR/deploy/nginx.conf" ]; then
    echo "ERREUR : $APP_DIR/deploy/nginx.conf introuvable." >&2
    exit 1
fi
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/ipxe-manager

ln -sf /etc/nginx/sites-available/ipxe-manager /etc/nginx/sites-enabled/ipxe-manager
rm -f /etc/nginx/sites-enabled/default
nginx -t && echo "  Nginx : config OK."

# ── 11. Services systemd ──────────────────────────────────────────────────────
echo "[11/15] Création des services systemd…"

cat > /etc/systemd/system/ipxe-manager.service <<EOF
[Unit]
Description=iPXE Manager — FastAPI Web App
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/uvicorn.log
StandardError=append:$LOG_DIR/uvicorn.log

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ipxe-celery.service <<EOF
[Unit]
Description=iPXE Manager — Celery Worker (extraction ISO + compilation firmware)
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=exec
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/celery.log
StandardError=append:$LOG_DIR/celery.log

[Install]
WantedBy=multi-user.target
EOF

# Supprimer l'ancienne unité si elle existait sous un autre nom
systemctl disable --now celery-worker 2>/dev/null || true
rm -f /etc/systemd/system/celery-worker.service

# ── 12. Samba (partage SMB pour installation Windows via réseau) ──────────────
echo "[12/15] Configuration Samba…"
cat > /etc/samba/smb.conf <<EOF
[global]
   workgroup = WORKGROUP
   server string = iPXE Boot Server
   security = user
   map to guest = bad user
   log file = /var/log/samba/log.%m
   max log size = 1000
   # Désactiver les fonctionnalités AD DC
   server role = standalone server

[boot]
   comment = iPXE boot tree (WinPE: \\host\\boot\\winpe\\<ver>\\installs\\<slug>\\install.wim)
   path = $DATA_DIR/http/boot
   browseable = yes
   read only = yes
   guest ok = yes
   force user = $APP_USER

[isos]
   comment = ISO Images
   path = $DATA_DIR/isos
   browseable = yes
   read only = yes
   guest ok = yes
EOF

# ── 13. NFS — Ubuntu live / install depuis extractions sous http/boot/ubuntu ─
echo "[13/15] Configuration NFS (Ubuntu netboot depuis $DATA_DIR/http/boot/ubuntu)…"
install -d -m 755 /etc/exports.d
cat > /etc/exports.d/ipxe-manager-ubuntu.exports <<EOF
# ISO Ubuntu extraites : un dossier par version sous ce répertoire (même slug que l’UI).
# Après ajout d’une nouvelle version : même export, mais vérifier que le dossier existe puis exportfs -ra si besoin.
# Client nfsroot = IP:/srv/ipxe/http/boot/ubuntu/<slug> ; si « No such file » → dossier absent ou faux slug.
# Pare-feu : tcp 2049 (+ rpcbind 111 si filtré finement).
$DATA_DIR/http/boot/ubuntu *(ro,sync,no_subtree_check,insecure,no_root_squash)
EOF
exportfs -ra || true
if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^nfs-server\.service'; then
    systemctl enable --now nfs-server
elif systemctl list-unit-files --type=service 2>/dev/null | grep -q '^nfs-kernel-server\.service'; then
    systemctl enable --now nfs-kernel-server
else
    echo "  ! Unité NFS introuvable — démarrez manuellement nfs-server ou nfs-kernel-server après vérification des paquets."
fi

# ── 14. Permissions finales ───────────────────────────────────────────────────
echo "[14/15] Application des permissions…"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR" "$LOG_DIR"
chmod 640 "$APP_DIR/.env"
# http/boot doit être lisible par Nginx (www-data) ET par Samba
chmod -R o+rX "$DATA_DIR/http/boot" 2>/dev/null || true
chmod -R o+rX "$DATA_DIR/http/menus" 2>/dev/null || true
chmod -R o+rX "$DATA_DIR/http/configs" 2>/dev/null || true
chmod -R o+rX "$DATA_DIR/isos" 2>/dev/null || true

# ── 15. Démarrage de tous les services ────────────────────────────────────────
echo "[15/15] Démarrage des services…"
systemctl daemon-reload
systemctl enable --now redis-server
systemctl enable --now tftpd-hpa
systemctl reload-or-restart nginx
systemctl enable --now smbd nmbd
# NFS : déjà activé ci-dessus si présent ; s'assurer qu'il tourne après permissions
systemctl reload-or-restart nfs-server 2>/dev/null || systemctl reload-or-restart nfs-kernel-server 2>/dev/null || true

# Ubuntu autoinstall : mode HTTP par défaut (UBUNTU_NFS_ENABLED=false). NFS reste optionnel.
if [ -f "$APP_DIR/.env" ]; then
    grep -q '^UBUNTU_NFS_ENABLED=' "$APP_DIR/.env" || printf '\nUBUNTU_NFS_ENABLED=false\n' >> "$APP_DIR/.env"
    grep -q '^UBUNTU_RAMDISK_SIZE=' "$APP_DIR/.env" || printf 'UBUNTU_RAMDISK_SIZE=1500000\n' >> "$APP_DIR/.env"
    chmod 640 "$APP_DIR/.env" 2>/dev/null || true
fi
if systemctl is-active --quiet nfs-server 2>/dev/null || systemctl is-active --quiet nfs-kernel-server 2>/dev/null; then
    echo "  NFS Ubuntu export disponible ; menus HTTP autoinstall par défaut (UBUNTU_NFS_ENABLED=true seulement si vous le voulez)."
else
    echo "  NFS non actif — menus Ubuntu en mode HTTP autoinstall (UBUNTU_NFS_ENABLED=false)."
fi

systemctl enable --now ipxe-manager
systemctl enable --now ipxe-celery

# tftpd-hpa peut avoir démarré pendant la mise à jour des fichiers ; redémarrer une fois tout écrit.
systemctl restart tftpd-hpa

# ── Résumé ────────────────────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Installation terminée !"
echo "======================================================"
echo ""
echo "  Interface web  : http://$SERVER_IP/"
echo "  Login          : admin  /  Mot de passe : admin"
echo "  Menu iPXE HTTP : http://$SERVER_IP/menus/menu.ipxe"
echo "  TFTP server    : $SERVER_IP (undionly.kpxe / snponly.efi / ipxe.efi)"
echo "  Samba share    : \\\\$SERVER_IP\\boot"
echo "  NFS (Ubuntu)   : $SERVER_IP:$DATA_DIR/http/boot/ubuntu (optionnel — UBUNTU_NFS_ENABLED=true dans .env)"
echo ""
echo "  IMPORTANT : Changer le mot de passe admin dans Paramètres !"
echo "  FIRMWARE  : Compiler un firmware custom avec embed depuis /firmware"
echo ""
echo "  Mise à jour :"
echo "    cd $APP_DIR && git pull && systemctl restart ipxe-manager ipxe-celery"
echo "    $VENV/bin/python deploy/seed_db.py   # si nouvelles migrations DB"
echo ""

# Statut de chaque service
for svc in ipxe-manager ipxe-celery nginx tftpd-hpa redis-server smbd; do
    if systemctl is-active --quiet "$svc"; then
        echo "  [OK] $svc"
    else
        echo "  [!!] $svc — PROBLÈME (voir: journalctl -u $svc)"
    fi
done
if systemctl is-active --quiet nfs-server 2>/dev/null; then
    echo "  [OK] nfs-server"
elif systemctl is-active --quiet nfs-kernel-server 2>/dev/null; then
    echo "  [OK] nfs-kernel-server"
elif dpkg -l nfs-kernel-server 2>/dev/null | grep -q '^ii'; then
    echo "  [!!] NFS — service inactif (journalctl -u nfs-server ou nfs-kernel-server)"
fi
echo ""
