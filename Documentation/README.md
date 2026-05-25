# Documentation utilisateur — iPXE Manager

Guide d’utilisation du **site web** iPXE Manager (interface FastAPI).  
Pour l’**installation**, le déploiement serveur et l’architecture technique, voir le [README du projet](../README.md) à la racine du dépôt.

---

## Public visé

- Administrateurs PXE qui gèrent ISOs, menus iPXE et firmwares.
- Opérateurs avec compte **Utilisateur** (versions ISO dont ils sont propriétaires).
- Techniciens qui configurent DHCP / TFTP / HTTP en parallèle de l’UI.

---

## Organisation des fichiers

| Fichier | Contenu |
|---------|---------|
| [00-introduction-et-concepts.md](00-introduction-et-concepts.md) | Rôle de l’outil, TFTP + HTTP, Celery |
| [01-connexion-et-navigation.md](01-connexion-et-navigation.md) | Login, barre de menu, langues |
| [02-roles-et-permissions.md](02-roles-et-permissions.md) | Admin vs Utilisateur, lecture seule |
| [03-tableau-de-bord.md](03-tableau-de-bord.md) | Jobs, disque, cartes OS |
| [04-isos-liste-et-ajout.md](04-isos-liste-et-ajout.md) | Liste ISOs, formulaire d’ajout |
| [05-isos-fiche-version.md](05-isos-fiche-version.md) | Extraction, boot, WinPE, Ubuntu, etc. |
| [06-fichiers-boot.md](06-fichiers-boot.md) | Page Fichiers Boot |
| [07-configurations-automatiques.md](07-configurations-automatiques.md) | Preseed, cloud-init, kickstart… |
| [08-menus-ipxe.md](08-menus-ipxe.md) | Menus générés, scripts perso, chainload |
| [09-firmware-ipxe.md](09-firmware-ipxe.md) | Compilation undionly / EFI |
| [10-parametres.md](10-parametres.md) | URL, mot de passe, TLS, logo, types d’OS |
| [11-supervision.md](11-supervision.md) | Santé serveur, audits (admin) |
| [12-gestion-utilisateurs.md](12-gestion-utilisateurs.md) | Comptes, relance services (admin) |
| [13-dialogues-et-confirmations.md](13-dialogues-et-confirmations.md) | Modales de confirmation |
| [14-taches-arriere-plan.md](14-taches-arriere-plan.md) | Celery : extraction, menus, firmware |
| [15-parcours-boot-pxe.md](15-parcours-boot-pxe.md) | Du client PXE au menu (vue utilisateur) |
| [16-depannage-interface.md](16-depannage-interface.md) | Problèmes fréquents côté UI |
| [images/README.md](images/README.md) | Convention de nommage des captures |

---

## Captures d’écran

Les illustrations sont dans le dossier [`images/`](images/README.md) (fichiers `.png`). Chaque chapitre les affiche en contexte avec des chemins relatifs du type `images/nom.png`.

---

## Accès rapide par URL

| Page | URL typique |
|------|-------------|
| Connexion | `/login` |
| Tableau de bord | `/` |
| ISOs | `/isos` |
| Ajouter une version | `/isos/upload` |
| Fiche version | `/isos/{id}` |
| Fichiers Boot | `/boot-files` |
| Configs auto | `/ipxe-configs` |
| Menus iPXE | `/ipxe-menus` |
| Firmware | `/firmware` |
| Paramètres | `/settings` |
| Supervision | `/admin/supervision` |
| Utilisateurs | `/admin/users` |

---

*Dernière mise à jour : alignée sur l’interface iPXE Manager (menus lazy-load, certificat TLS 2 ans, supervision).*
