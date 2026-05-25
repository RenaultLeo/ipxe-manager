# ISOs — Fiche d’une version

**URL :** `/isos/{id}`  
Accès : clic sur une ligne dans **ISOs** ou après un upload.

En-tête : type d’OS + libellé + **badge de statut** (Prêt, Extraction…, Erreur).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-header-ready.png`
>
> **Description de la photo :** En-tête fiche version « Ubuntu 22.04 LTS » avec badge vert **Prêt**, flèche retour vers liste.
>
> **Éléments à cadrer :** Bouton retour, icône OS, titre, badge statut à droite.

---

## Carte Informations

- ID, taille ISO, URL HTTP publique de l’ISO (si présente), chemin disque serveur.
- **Ubuntu** : badge Server ou Desktop.
- Notes éventuelles.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-info-card.png`
>
> **Description de la photo :** Carte gauche « Informations » avec URL ISO cliquable et chemin `/srv/...` en monospace.
>
> **Éléments à cadrer :** Paires libellé/valeur, lien bleu URL HTTP.

---

## Carte Fichiers de boot

### Barre d’outils (si vous pouvez modifier)

| Action | Rôle |
|--------|------|
| **Extraire depuis l’ISO** | Lance Celery : décompresse l’ISO vers `http/boot/...` |
| **Supprimer ISO après extraction** | Case à cocher : efface l’ISO disque après **prochaine** extraction réussie (gain de place) |
| **Boot NFS dans le menu** | **Ubuntu** : bascule HTTP autoinstall vs netboot NFS (casper) |
| **Remplacer boot.wim** | **Windows** : upload d’un nouveau `boot.wim` |

**Ré-extraction** : une confirmation apparaît si l’ISO a déjà été extraite (risque d’écraser les fichiers boot).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-boot-toolbar.png`
>
> **Description de la photo :** Barre sous le titre « Fichiers de boot » : bouton orange Extraire, switches NFS / purge ISO si Ubuntu/Windows.
>
> **Éléments à cadrer :** Chaque bouton et interrupteur avec son libellé lisible.

---

### Contenu affiché

Liste des fichiers détectés : `vmlinuz`, `initrd`, `boot.wim`, chemins ESXi, rapport d’extraction (fichiers recherchés), etc.

**Arguments noyau** : champ éditable + enregistrement (si droits).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-boot-files-list.png`
>
> **Description de la photo :** Liste des chemins de boot (dl/dt) avec vmlinuz, initrd ou boot.wim.
>
> **Éléments à cadrer :** Au moins 2 entrées fichier, section arguments noyau en dessous.

---

### Erreur d’extraction

Bandeau rouge avec message ; parfois extrait des logs Celery. Si vide : consulter `journalctl -u ipxe-celery` sur le serveur.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-extract-error.png`
>
> **Description de la photo :** Fiche en statut Erreur : alerte rouge avec texte d’erreur (zone `<pre>` si long).
>
> **Éléments à cadrer :** Badge Erreur en titre + bloc message d’erreur.

---

### Extraction en cours

Badge **Extraction en cours** avec icône animée ; la page peut interroger le serveur toutes les ~8 s jusqu’à statut final.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-extracting-badge.png`
>
> **Description de la photo :** Badge jaune « Extraction en cours » dans l’en-tête (spinner).
>
> **Éléments à cadrer :** Badge seul ou en-tête complet pendant extraction.

---

## Script iPXE personnalisé (carte dédiée)

Upload `.ipxe` ou `.txt` pour cette version → apparaît dans **Menus iPXE → Scripts personnalisés** et sous-menu **Autres** du OS.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-custom-ipxe-upload.png`
>
> **Description de la photo :** Section « Script iPXE personnalisé » avec champ fichier + bouton Téléverser, lien vers Menus iPXE.
>
> **Éléments à cadrer :** Input fichier, script actuel affiché si déjà uploadé.

---

## Ubuntu — NFS vs HTTP autoinstall

Interrupteur **Boot NFS (casper)** :

- **Désactivé** (défaut) : autoinstall HTTP, `root=/dev/ram0`, URL ISO si fichier encore sur le serveur.
- **Activé** : menu généré avec `netboot=nfs` (nécessite vmlinuz/initrd extraits + infra NFS côté serveur).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-ubuntu-nfs-toggle.png`
>
> **Description de la photo :** Interrupteur NFS sur fiche Ubuntu, états ON et OFF (deux captures ou une avec infobulle).
>
> **Éléments à cadrer :** Libellé « Boot NFS », position du switch.

---

## Windows / WinPE — section déploiement

Pour les versions **Windows** avec WinPE :

### Images d’installation (`install.wim`)

- **Masters** : dossiers `installs/<nom>/install.wim`
- Ajout : identifiant dossier, libellé, index DISM, fichier `.wim`
- **Image active** + **Patcher boot.wim (startnet.cmd)**

### Pilotes

- Arborescence `boot/drivers/<type machine>/`
- Upload multi-fichiers `.inf`, `.sys`, `.cab`…
- Catalogue `drivers.json`

### Scripts WinPE

Bouton **Mettre à jour les scripts WinPE et boot.wim** :

- Génère `deploy.ps1`, `inject-drivers.ps1`, `masters.json` sur le partage
- Injecte `startnet.cmd` dans `boot.wim` (tâche Celery)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-winpe-masters-table.png`
>
> **Description de la photo :** Tableau des images install.wim avec boutons activer / supprimer.
>
> **Éléments à cadrer :** Colonnes nom, label, index, bouton « Image active ».

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-winpe-drivers-upload.png`
>
> **Description de la photo :** Formulaire upload pilotes : type de machine + sélection multi-fichiers.
>
> **Éléments à cadrer :** Liste types existants, bouton envoyer pilotes.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-winpe-regenerate-scripts.png`
>
> **Description de la photo :** Bouton « Mettre à jour les scripts WinPE » + message succès ou spinner Celery.
>
> **Éléments à cadrer :** Bouton principal, alerte succès verte après génération.

---

## Configurations automatiques liées

Liens vers configs **Ubuntu** (user-data / meta-data), **Proxmox** (answer.toml), etc. selon l’OS.

Boutons **Publier sur la version** / **Retirer config active** pour Ubuntu (copie vers `boot/ubuntu/<version>/`).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/05-iso-detail-ubuntu-autoconfig-active.png`
>
> **Description de la photo :** Bloc config Ubuntu avec badge « Config active » et chemins user-data / meta-data.
>
> **Éléments à cadrer :** Boutons publier / retirer, badge actif.

---


## Voir aussi

- [06-fichiers-boot.md](06-fichiers-boot.md)
- [07-configurations-automatiques.md](07-configurations-automatiques.md)
- [14-taches-arriere-plan.md](14-taches-arriere-plan.md)
