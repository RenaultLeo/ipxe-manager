# iPXE Manager

**iPXE Manager** est une interface web (FastAPI) pour administrer un petit **datacenter PXE** depuis une machine Debian : tu centralises les ISOs, les noyaux/initrd, les menus iPXE et les fichiers d’installation automatique (preseed, kickstart, cloud-init, etc.), sans retoucher à la main tous les scripts à chaque nouvelle version.

En pratique, la machine joue le rôle de **serveur TFTP** pour le premier chargement (iPXE BIOS/UEFI), puis sert le reste en **HTTP** (menus, fichiers de boot, configs). Les opérations longues (extraction d’ISO, compilation du firmware iPXE) passent par **Celery**, pour que l’interface reste réactive.

---

## Architecture

L’installation type est une **VM Proxmox** (ou équivalent) sous **Debian 12 (Bookworm)**. Tout le contenu « données » (ISOs, fichiers extraits, menus) vit sous **`/srv/ipxe/`** pour faciliter sauvegardes et montages disque.

Schéma logique des briques :

```
├── tftpd-hpa          → première étape PXE : firmwares iPXE sur le port 69 (TFTP)
├── Nginx              → reverse proxy vers l’API + service direct des gros fichiers
│                        (menus .ipxe, /boot/, /configs/) pour de meilleures perf
├── FastAPI + Jinja2   → pages web, authentification, formulaires
├── Celery + Redis     → file d’attente : extraction ISO, régénération menus,
│                        compilation iPXE depuis les sources officielles
├── Samba (smbd/nmbd) → partage SMB en lecture seule (boot / ISOs), pratique pour
│                       certains scénarios Windows / copies manuelles
└── SQLite             → références (versions, chemins, configs, chainloads distants)
```

**Pourquoi ces deux modes TFTP + HTTP ?** Le firmware iPXE téléchargé au boot via TFTP est volontairement limité en taille ; une fois iPXE lançé, il charge le **menu principal** et tous les gros fichiers (ISO extrait, WIM, squashfs, etc.) en **HTTP**, ce qui est plus simple à mettre à jour et à dimensionner.

---

## Prérequis VM

- **OS** : Debian 12 (recommandé ; le script `setup.sh` vise Bookworm / environnements proches).
- **vCPU** : 4 cœurs pour des extractions et compilations confortables (2 minimum possible).
- **RAM** : 4 Go minimum, **8 Go** si tu compiles souvent le firmware iPXE ou extrais de grosses ISO Windows.
- **Disque** : volume système (32 Go suffisent souvent) ; **à part**, beaucoup d’espace pour les ISOs sous `/srv/ipxe/isos/` et les arborescences extraites sous `http/boot/`.
- **Réseau** : une IP **fixe** sur le LAN (les menus et le DHCP pointent vers cette IP).

Exemple **Netplan** (adapter l’interface et la passerelle à ton site) :

```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: false
      addresses: [192.168.2.6/24]
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
```

```bash
sudo netplan apply
```

---

## Installation rapide

### Cloner le dépôt

Dépôt public : **https://github.com/RenaultLeo/ipxe-manager**

```bash
sudo mkdir -p /srv/ipxe
sudo git clone https://github.com/RenaultLeo/ipxe-manager.git /srv/ipxe/app
```

### Installation en une ligne (sans cloner à la main)

Télécharge **`deploy/setup.sh`** brut depuis GitHub et exécute‑le : l’**étape [4]** du script clone (ou met à jour) le dépôt sous **`/srv/ipxe/app`** avant **`pip install`**, donc ce flux est cohérent même quand le script est lu depuis **stdin** (pipe).

```bash
curl -fsSL https://raw.githubusercontent.com/RenaultLeo/ipxe-manager/main/deploy/setup.sh | sudo bash -s -- 192.168.2.6
```

- Remplace **`192.168.2.6`** par l’**IP vue par les clients PXE**, ou omets **`--`** et l’argument : la valeur par défaut est la première IP locale (comme pour **`setup.sh` seul**).
- **Important** : n’utilise pas **`curl -I`** ou **`-SI`** pour « installer » : **`-I`** interroge seulement les en‑têtes HTTP (**HEAD**) et **ne récupère pas le corps du script**. Il faut **`-fsSL`** (sans **`I`**) puis le passage à **`bash`**, comme ci‑dessus.
- **Fork ou autre URL / dossier** : modifie les constantes **`REPO_URL`** et **`APP_DIR`** en début de **`deploy/setup.sh`**, ou clone le dépôt à la main sous **`APP_DIR`** avant de lancer le script depuis le système de fichiers.

### Lancer le script d’installation

Le second argument est l’**IP du serveur** telle qu’elle sera annoncée aux clients (menus HTTP, embed, etc.) :

```bash
sudo bash /srv/ipxe/app/deploy/setup.sh 192.168.2.6
```

Ce que fait **`deploy/setup.sh`** (synthèse) :

- Installe les paquets : Nginx, tftpd-hpa, Redis, Python, p7zip, outils de build pour iPXE, Samba, etc.
- Crée l’utilisateur système **`ipxe`**, les répertoires sous `/srv/ipxe/`, les unités **`ipxe-manager`** et **`ipxe-celery`**.
- Configure **Nginx** avec des alias pour `/menus/`, `/boot/`, **`/isos-ipxe/`** (ISO sous `ISO_ROOT`, sans entrer en conflit avec les routes UI `/isos`), etc., et des limites d’upload pour les grosses ISO.
- Écrit **`/etc/default/tftpd-hpa`** et **redémarre `tftpd-hpa` à la fin** : pendant le script, le service peut démarrer avec une config encore incomplète ; le redémarrage final évite un TFTP qui ne lit qu’une partie des paramètres ou des répertoires.
- Initialise la base avec **`deploy/seed_db.py`** (types d’OS par défaut : Windows, Ubuntu, Debian, Rocky, Alma, Fedora, Proxmox, ESXi, Alpine, etc.).
- Au **premier démarrage** de l’appli (`uvicorn`), **`init_db()`** complète encore les migrations de schéma (colonnes SQLite ajoutées au fil des versions du code).
- Après **`pip install`**, régénère sous **`app/locale_values/`** les fichiers **`_en.list.json`**, **`de|es|it|pt.list.json`** (traductions alignées sur `app/i18n.py` via Node : `extract_en_list.mjs` + `build_locale_lists.mjs`). **Node.js** est installé avec les paquets système pour que ce soit reproductible sur toute machine ; en cas d’échec, les fichiers déjà présents dans le dépôt Git sont utilisés.
- Télécharge **`wimboot`** et, si besoin, des binaires iPXE **génériques** en secours dans TFTP (en attendant ta propre compilation depuis l’UI **Firmware**).

### À vérifier sur une machine neuve

- **`sudo`** : tout le flux `setup.sh` suppose un shell root ou `sudo bash …`.
- **Accès réseau** pendant l’installation : téléchargements `apt`, `git clone`, `wget` (boot.ipxe.org / wimboot GitHub).
- **Ports** après install : HTTP **80** (Nginx), TFTP **69/UDP**, **Redis** en local (**6379**), éventuellement **SMB** (**445**) et **NFS** (**2049** si `nfs-kernel-server` démarre). Ouvre/adapte le pare-feu sur la VM ou le VLAN.
- **Branche Git** : le premier clonage tente `main`, puis `master`, puis clone par défaut — un `git pull` ultérieur suit la **branche suivie**. Si tu as un flux personnalisé, clone la bonne branche **avant** de lancer `setup.sh` dans ce répertoire (le script ne supprime alors pas le dossier tant que `.git` est présent).
- **PostgreSQL** : le `.env` généré cible du **SQLite** sous `/srv/ipxe/app/` ; passer en Postgres se fait à la main en `.env`, sans script dédié.
- **Ubuntu NFS** (optionnel) : export NFS disponible après `setup.sh` ; les menus utilisent par défaut le **mode HTTP autoinstall**. Mets **`UBUNTU_NFS_ENABLED=true`** dans `.env` seulement si tu veux l’ancien boot `netboot=nfs`.

### Référence `deploy/` (noms systemd)

Les fichiers **`deploy/ipxe-manager.service`** et **`deploy/celery-worker.service`** documentent une unité proche ; sur le système réel **`setup.sh`** installe **`ipxe-manager`** et **`ipxe-celery`** sous `/etc/systemd/system/` (logs dans **`/var/log/ipxe-manager/`**). **`deploy/nfs-setup.sh`** ne fait que NFS + export Ubuntu. **`deploy/patch.sh`** est **historique** : préfère **`deploy/update.sh`** au quotidien. **`deploy/setup.sh`** est aussi exposé brut sur GitHub pour un **bootstrap** « **curl \| bash** » (voir Installation en une ligne).

### Première connexion

- Ouvre **`http://<IP>/`** dans un navigateur.
- Identifiants par défaut : **`admin` / `admin`**.
- Va dans **Paramètres** et **change immédiatement le mot de passe** (et vérifie **`SERVER_BASE_URL`** / URL de base si ton accès passe par un reverse proxy ou un autre port).

---

## DHCP et boot réseau

Le **serveur DHCP** (souvent **pfSense**, routeur, ou `isc-dhcp-server`) doit :

1. Indiquer le **serveur TFTP** (**next-server / option 66**) : l’IP de la machine iPXE Manager.
2. Donner un **fichier de démarrage** selon l’architecture du client :
   - **BIOS / Legacy** : en général **`undionly.kpxe`** (iPXE PXE stack).
   - **UEFI x86_64 en VM** (Proxmox, QEMU, VMware…) : de préférence **`snponly.efi`**, qui utilise la **pile réseau de l’EFI** (virtio, e1000…). **`ipxe.efi`** reste utile surtout pour du **bare-metal** avec drivers intégrés iPXE.

3. Une fois le client **déjà sous iPXE** (user-class `iPXE`), il est préférable de lui donner directement l’**URL HTTP** du menu central (`http://<IP>/menus/menu.ipxe`), pour **éviter un double chainload** iPXE qui casse souvent l’accès réseau.

L’interface **Firmware** affiche un bloc d’aide avec un exemple de clauses DHCP (pfSense / options) aligné sur les binaires que tu compiles.

---

## Parcours métier dans l’interface

### Dashboard

Vue d’ensemble : espace disque, statistiques par type d’OS, **jobs Celery** récents ou en cours. Les heures d’affichage des jobs suivent le **fuseau du navigateur** ; l’upload seul d’un ISO n’ouvre plus un faux job bloqué en « en cours ».

### ISOs

Tu crées une **version** pour un type d’OS (label lisible : « 22.04 LTS », « 11 », etc.). Tu peux :

- Uploader **une ISO** (stockée sous `/srv/ipxe/isos/...`) puis, quand tu es prêt, lancer **Extraire depuis l’ISO** : une tâche Celery décompresse avec **7z** et peuple `http/boot/<os>/<slug-version>/` selon les règles de la distribution.
- Ou **ne pas** fournir d’ISO et uploader uniquement **vmlinuz**, **initrd**, etc. (chemins enregistrés tels quels, **sans forcer** des noms génériques quand ce n’est pas voulu).

Pour **Windows**, l’extraction est **complète** (toute l’arborescence ISO) afin que les chemins d’installation réseau fonctionnent ; tu peux ensuite **remplacer uniquement `boot.wim`** depuis la fiche version. Un **`autounattend.xml`** à la racine du dossier version peut être pris en charge par le menu si présent.

Pour **Ubuntu**, l’ISO est aussi extraite **en entier** ; noyau et initrd dans **`casper/`**. Par défaut les menus utilisent le mode **HTTP autoinstall** (`root=/dev/ram0`, `url=` vers l’ISO si elle est encore sur le serveur, `autoinstall ds=nocloud-net` + `cloud-config-url=/dev/null` sur les configs auto). Option **`UBUNTU_NFS_ENABLED=true`** pour l’ancien mode NFS.

Pour **Rocky Linux**, **AlmaLinux**, **CentOS** et **Fedora** (Anaconda), l’ISO est extraite **en entier**. Les menus ajoutent selon l’OS : **`inst.repo=`** (Rocky, Alma, CentOS). **Fedora** : **`inst.stage2=`** + **`rd.neednet=1`** si l’ISO n’est **pas** Live ; si l’option **ISO Fedora Live** est cochée à l’upload (ou sur la fiche version), utilisation de **`root=live:http://…/LiveOS/squashfs.img`**, **`ro`**, **`rd.live.image`** à la place de `inst.stage2`. Toujours **`ip=dhcp`** si besoin. Si le type d’OS a **« extraction complète »** sans liste de noms dans Paramètres, le moteur intégré s’applique. **Fedora Workstation Live** place souvent le noyau en **`boot/x86_64/loader/linux`** ; l’[howto iPXE Fedora](https://ipxe.org/howto/fedora) pour une install « miroir » classique utilise plutôt **Everything** / **netinst** avec **`images/pxeboot/vmlinuz`**.

### Fichiers Boot

Page de gestion centralisée : remplacer ou compléter kernel, initrd, modloop (Alpine), éléments Windows, args kernel, etc. Les **libellés** reflètent les **vrais noms de fichier** sur disque (ex. `vmlinuz-lts`).

### Configs Auto (auto-install)

Tu relies une **config à une version**. Les **OS fournis par défaut** (seed) ont un **type imposé** (cohérent avec le boot) :

| Famille | Type / fichiers typiques |
|---------|---------------------------|
| Debian | `preseed.cfg` |
| Ubuntu | cloud-init : **`user-data`** et **`meta-data`** (le second peut être auto-créé pour un minimum viable) |
| CentOS / Rocky / Alma / Fedora | kickstart : `ks.cfg` |
| ESXi | `ks.cfg` |
| Windows | `autounattend.xml` / `unattend.xml` (choix du fichier quand les deux existent côté UX) |
| Proxmox | `answer.toml` |
| Alpine | `answers` ou `alpine.apkovl.tar.gz` |

Les arguments noyau iPXE (preseed URL, `inst.ks`, `autoinstall ds=nocloud-net` + `cloud-config-url=/dev/null` pour Ubuntu, etc.) sont dérivés automatiquement là où c’est prévu.

### Menus iPXE

- **Menus générés** : tous les `.ipxe` sous `menus/` ; tu peux prévisualiser l’URL ou **éditer** un fichier (attention : une régénération globale peut réécraser ce que tu as modifié manuellement selon le flux).
- **Scripts personnalisés** : versions qui ont un `.ipxe` uploadé ; sous-menu **Autres** dans le menu OS ; actions voir / modifier / supprimer.
- **Serveurs distants** : tu enregistres des **noms + URL exactes** vers d’autres menus (autre machine, autre `menu.ipxe`). Elles sont ajoutées **en bas du menu central** sous une section dédiée avec des `chain --autofree`.

### Firmware

Un job Celery **clone ou met à jour** le dépôt **ipxe/ipxe**, écrit **`embed.ipxe`** (chainload vers ton `menu.ipxe` avec logique DHCP résiliente), puis compile **`undionly.kpxe`**, **`snponly.efi`**, **`ipxe.efi`** et les copie dans **TFTP**. L’UI montre la **progression par étapes** (badges verts pour ce qui est terminé). Tu peux **annuler** une compilation en cours.

### Paramètres

Clé secrète de session, URL de base publique, mot de passe administrateur.

---

## Structure des répertoires (référence)

```
/srv/ipxe/
├── tftpboot/              # Racine TFTP (firmwares vus par les clients au premier boot)
├── build/                 # Sources iPXE + embed généré lors de la compilation UI
├── http/
│   ├── menus/             # menu.ipxe, debian.ipxe, windows.ipxe, *_autres.ipxe, …
│   ├── boot/              # Arborescences extraites ou uploadées par OS / version
│   └── configs/           # Fichiers créés par Configs Auto (par OS / version)
├── isos/                  # ISO brutes
├── venv/                  # Environnement Python du service
└── app/
    ├── .env               # Variables (chemins, REDIS, secrets…)
    └── ipxe.db            # SQLite (ou chemin défini dans la config)
```

---

## Services systemd (vérification rapide)

```bash
systemctl status ipxe-manager    # Processus web (uvicorn derrière le service)
systemctl status ipxe-celery      # Worker Celery (ISO, menus, firmware)
systemctl status tftpd-hpa nginx redis-server
systemctl status smbd nmbd        # Samba fichier
```

Journal utile en cas de souci :

```bash
journalctl -u ipxe-manager -u ipxe-celery -f --no-pager
```

---

## Mise à jour du code sur le serveur

Depuis la machine de production :

```bash
sudo bash /srv/ipxe/app/deploy/update.sh
```

Le script enchaîne : **`git pull --ff-only`** (branche suivie), mise à jour **`pip`**, régénération des **listes i18n** si **Node.js** est installé sur le serveur, exécution de **`deploy/seed_db.py`** (nouvelles colonnes / graines idempotentes), puis **redémarrage** de **`ipxe-manager`**, **`ipxe-celery`**, **`tftpd-hpa`** et **rechargement de Nginx** pour prendre en compte d’éventuels changements de config.

Si tu ajoutes des champs en base et que le service ne les voit pas tout de suite, tu peux aussi lancer manuellement (en utilisateur adapté, selon ton déploiement) :

```bash
sudo -u ipxe /srv/ipxe/venv/bin/python /srv/ipxe/app/deploy/seed_db.py
```

---

## Vérification smoke + charge HTTP

Le script **`scripts/ipxe_health_load.py`** permet de vérifier rapidement qu’un déploiement répond comme prévu **sans installer de dépendance** (stdlib Python uniquement).

**Ce qu’il fait**

1. **Smoke** — quelques requêtes ciblées : page de connexion (`/login`), redirection de la racine vers `/login` si non connecté, fichier statique CSS, tentative de POST login avec un mauvais mot de passe (attendu **401**), et optionnellement **`/menus/menu.ipxe`** (menu iPXE servi par Nginx en prod).
2. **Charge optionnelle** — enchaîne un grand nombre de requêtes **GET** concurrentes vers une même URL (par défaut **`/login`**, léger côté app) pour estimer débit approximatif et latences (min, p50, p95, max).

**À quelle URL passer `--base-url`**

- En **production** : l’URL telle que les clients y accèdent, en général **`http://IP_DU_SERVEUR`** (flux **identique au navigateur**, via **Nginx** sur le port 80).
- En **direct uvicorn** (port 8000) : même script avec **`--skip-menus`**, car le chemin **`/menus/`** est défini dans la config **Nginx** du `deploy/setup.sh`, pas sur l’app seule.

**Contrôles supplémentaires (à lancer sur la machine où tournent les services)**

- **`--check-redis`** : commande **`redis-cli ping`** si disponible.
- **`--celery-inspect`** : **`celery inspect ping`** via le virtualenv **`/srv/ipxe/venv`**, pour confirmer qu’au moins un **worker Celery** est joignable (extractions ISO, firmware, menus).

**Codes sortie**

- **0** : tous les checks obligatoires sont passés ; la phase charge aussi, si elle est active, n’a eu aucune erreur HTTP.
- **1** : au moins un échec (smoke, Redis/Celery si demandés, ou erreurs sous charge).

Exemples :

```bash
# Via Nginx (comme les clients PXE — recommandé)
python3 scripts/ipxe_health_load.py --base-url http://VOTRE_IP

# Contrôles en plus sur le serveur : Redis et worker Celery
python3 scripts/ipxe_health_load.py --base-url http://127.0.0.1 --check-redis --celery-inspect

# Volume plus élevé (stress léger — la cible reste une page peu coûteuse par défaut)
python3 scripts/ipxe_health_load.py --base-url http://VOTRE_IP --workers 50 --requests 2000

# Smoke seulement, sans phase charge
python3 scripts/ipxe_health_load.py --base-url http://VOTRE_IP --no-load
```

### Audit exhaustif (HTTP, données, SMB, NFS, disque…)

Le script **`scripts/ipxe_exhaustive_check.py`** (**stdlib uniquement**, dont **`sqlite3`**) vérifie l’ensemble des briques fonctionnelles habituelles.

**À distance (depuis ta machine)**

- **`--base-url`** : tout le périmètre HTTP décrit précédemment (pages publiques, menus **`/menus/*.ipxe`**, session admin avec **`--password`**, etc.).
- Une bannière d’avertissement s’affiche si tu actives aussi des audits **« locaux »** sans passer par **`http://127.0.0.1`** : plusieurs tests (ports **`127.0.0.1`**, **`smbclient`**, **`exportfs`**) s’exécutent sur **la machine où tourne le script**, pas sur l’IP de **`--base-url`**.

**Sur le serveur iPXE (SSH, répertoire applicatif sous `--app-dir` / défaut `/srv/ipxe/app`)**

- **`--full-local`** : active **`--check-db`**, **`--check-fs`**, **`--check-listen`**, **`--check-smb-shares`**, **`--check-nfs-export`**, **`--probe-static-http`**, **`--check-testparm`**.
  - **`--check-db`** : lecture de **`DATABASE_URL`** dans **`.env`** — SQLite (**`PRAGMA integrity_check`**, **`foreign_key_check`**, présence des tables **`os_types` … `remote_chains`**, **`COUNT(os_types)`** ≥ 1) ou **`pg_isready`** si Postgres.
  - **`--check-fs`** : dossiers **`TFTP_ROOT`**, **`HTTP_ROOT`**, **`ISO_ROOT`**, **`BUILD_DIR`**, puis **`menus/`**, **`boot/`**, **`configs/`**, **`menus/menu.ipxe`**, **`undionly.kpxe`** préconisés.
  - **`--check-listen`** : **`TCP :445`** (SMB **`smbd`**), **`TCP :6379`** (Redis), **UDP :69** (TFTP **`tftpd-hpa`**) sur **localhost** ; information sur **`TCP :2049`** (NFS).
  - **`--check-smb-shares`** : **`smbclient -g -L localhost -N`** attend les volumes **`disk`** **`boot`** et **`isos`** (comme dans **`deploy/setup.sh`**).
  - **`--check-nfs-export`** : **`exportfs -v`** doit refléter l’arborescence **Ubuntu nfsroot** (**`/srv/ipxe/http/boot/ubuntu`**) lorsque NFS est utilisé ; sinon ce n’est qu’informé.
  - **`--check-testparm`** : **`testparm -s`** (syntaxe **`smb.conf`**).
  - **`--probe-static-http`** : sondes **`/menus/menu-theme.png`** et **`/wimboot`** sur **`--base-url`**.
- **`--systemd`** : unités **`nginx`**, **`redis-server`**, **`tftpd-hpa`**, **`ipxe-manager`**, **`ipxe-celery`** ; avec **`--full-local`**, **`smbd`** et **`nmbd`** sont ajoutées automatiquement.

**Réutilisation avec le smoke léger**

- **`--strict-menus`**, **`--include-openapi`**, **`--check-redis`**, **`--celery-inspect`**, **`--systemd-unit`** restent disponibles comme avant.

À la fin du run, un encadré **`Bilan par catégorie`** résume pour chaque famille de tests : **État** (OK, OK avec avertissements, KO), **contrôles réussis**, **nombre d’échecs** et **nombre d’avertissements**, puis les **totaux globaux**.

```bash
# Sur le SERVEUR (diagnostic maximal — adapter le mot de passe)
python3 scripts/ipxe_exhaustive_check.py --base-url http://127.0.0.1 --password "$IPXE_ADMIN_PW" \
  --full-local --systemd --strict-menus --check-redis --celery-inspect
```

---

## Développement local

Sur ta machine de développement :

```bash
python3 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt
cp .env.example .env
# Éditer .env : DATABASE_URL, REDIS, chemins http_root / tftp_root en local si besoin

uvicorn app.main:app --reload --port 8000
```

Dans un **second terminal**, lance un worker Celery pour tester extraction / firmware :

```bash
celery -A app.tasks.celery_app worker --loglevel=info
```

Sans Redis/Celery local, certaines actions (extraction, compile) resteront en attente ; l’UI peut quand même servir à parcourir les écrans.

---

## Licence

**MIT** — voir le fichier `LICENSE` à la racine du dépôt.
