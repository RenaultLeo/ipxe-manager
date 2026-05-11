# iPXE Manager

Interface web complète pour gérer un serveur iPXE : upload d'ISOs, extraction de fichiers boot, génération automatique de menus `.ipxe`, configurations automatiques (preseed / kickstart / unattend).

## Architecture

```
VM Ubuntu 24.04 (Proxmox)
├── tftpd-hpa          → chainload initial (BIOS/UEFI)
├── Nginx              → reverse proxy + serveur de fichiers boot
├── FastAPI (uvicorn)  → interface web de gestion
├── Celery + Redis     → extraction ISO en arrière-plan
└── SQLite             → base de données
```

## Installation rapide

### 1. Créer la VM sur Proxmox

- **OS** : Ubuntu Server 24.04 LTS
- **CPU** : 4 vCPU
- **RAM** : 8 GB
- **Disque** : 500 GB+ (stockage ISOs)
- **Réseau** : Bridge sur le LAN (IP statique recommandée)

Configurer une IP statique dans `/etc/netplan/00-installer-config.yaml` :

```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: false
      addresses: [192.168.1.100/24]
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
```

```bash
sudo netplan apply
```

### 2. Cloner le projet et lancer l'installation

```bash
git clone https://github.com/vous/ipxe-manager.git /tmp/ipxe-manager
sudo bash /tmp/ipxe-manager/deploy/setup.sh 192.168.1.100
```

Le script installe et configure automatiquement tous les services.

### 3. Configurer le DHCP

Sur votre serveur DHCP (pfSense, Mikrotik, ISC DHCP…) :

| Option | BIOS | UEFI |
|--------|------|------|
| next-server | `192.168.1.100` | `192.168.1.100` |
| filename | `undionly.kpxe` | `ipxe.efi` |

Exemple ISC DHCP :
```
next-server 192.168.1.100;
if exists user-class and option user-class = "iPXE" {
    filename "http://192.168.1.100/menus/menu.ipxe";
} elsif option arch = 00:07 {
    filename "ipxe.efi";
} else {
    filename "undionly.kpxe";
}
```

## Utilisation

### Interface web

Ouvrir `http://192.168.1.100/` — mot de passe par défaut : `admin`

**IMPORTANT** : Changer le mot de passe dans **Paramètres → Mot de passe admin** immédiatement.

### Ajouter une ISO

1. **Paramètres** → Ajouter un type d'OS si nécessaire (Ubuntu, Debian, Windows…)
2. **ISOs** → Uploader une ISO → choisir le type et le label de version
3. Cliquer **Extraire depuis l'ISO** → Celery extrait `vmlinuz`, `initrd` ou `boot.wim` en arrière-plan
4. Les **Menus iPXE** sont régénérés automatiquement

### Upload manuel de fichiers boot

Via **Fichiers Boot** : uploader `vmlinuz`, `initrd`, `boot.wim` individuellement et les associer à une version d'ISO.

### Configurations automatiques

Via **Configs Auto** : créer des fichiers preseed / kickstart / unattend.xml / cloud-init avec l'éditeur intégré (templates disponibles). Ces fichiers sont intégrés dans les menus iPXE des versions Linux avec sous-menu de sélection.

## Structure des répertoires sur le serveur

```
/srv/ipxe/
├── tftpboot/               # Racine TFTP
│   ├── ipxe.efi            # Firmware iPXE UEFI
│   ├── undionly.kpxe       # Firmware iPXE BIOS
│   └── boot.ipxe           # Chainload vers HTTP
├── http/                   # Racine HTTP (servi par Nginx)
│   ├── menus/              # Fichiers .ipxe générés
│   │   ├── menu.ipxe       # Menu central
│   │   ├── ubuntu.ipxe
│   │   ├── windows.ipxe
│   │   └── …
│   ├── boot/               # Fichiers de boot extraits
│   │   ├── ubuntu/22/      # vmlinuz, initrd
│   │   └── windows/5/      # boot.wim
│   └── configs/            # Preseed, kickstart, unattend
├── isos/                   # ISOs brutes uploadées
└── app/                    # Code FastAPI + BDD SQLite
    ├── .env
    └── ipxe.db
```

## Services systemd

```bash
systemctl status ipxe-manager    # Interface web
systemctl status celery-worker   # Worker extraction
systemctl status tftpd-hpa       # TFTP
systemctl status nginx            # Serveur web
systemctl status redis-server     # Broker Celery
```

## Développement local

```bash
python3 -m venv .venv
source .venv/bin/activate         # Linux/Mac
# .venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Créer un .env local (adapter les chemins)
cp .env.example .env

uvicorn app.main:app --reload --port 8000
# Celery (dans un autre terminal)
celery -A app.tasks.celery_app worker --loglevel=info
```

## Mise à jour

```bash
cd /tmp
git pull origin main
sudo rsync -av app/ /srv/ipxe/app/app/
sudo rsync -av static/ /srv/ipxe/app/static/
sudo systemctl restart ipxe-manager celery-worker
```
