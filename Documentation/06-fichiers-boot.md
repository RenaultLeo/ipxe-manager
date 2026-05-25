# Fichiers Boot

**URL :** `/boot-files`  
**Menu :** Fichiers Boot

Vue **centralisée** de tous les fichiers de boot par version (complément de la fiche ISO).

---

## Objectif

- Voir rapidement quelles versions ont un **BootEntry** (noyau, initrd, boot.wim, etc.).
- **Remplacer** ou **ajouter** des fichiers sans repasser par tout le formulaire d’upload ISO.
- **Scanner** le disque pour enregistrer des fichiers déjà copiés manuellement sous `http/boot/`.

---

## Structure de la page

Souvent organisée par **type d’OS**, puis **version**, avec pour chaque entrée :

| Élément | Exemple |
|---------|---------|
| Rôle / fichier | vmlinuz, initrd, boot.wim, BCD, modloop |
| Chemin relatif | Sous `boot/<os>/<version>/` |
| Actions | Upload, édition args kernel |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-page-overview.png`
>
> **Description de la photo :** Page Fichiers Boot entière avec plusieurs versions groupées par OS.
>
> **Éléments à cadrer :** Titre page, bouton « Scanner boot/ », au moins un groupe OS déplié.

---

## Scanner les fichiers boot

Bouton du type **Scanner boot/** :

- Parcourt `boot/` sur le serveur
- Met à jour les enregistrements en base pour les fichiers déjà présents
- Affiche un résumé : X versions mises à jour, Y ignorées, erreurs éventuelles

Utile après une copie manuelle SSH/rsync.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-scan-result.png`
>
> **Description de la photo :** Message flash ou alerte après scan : « Scan terminé — N version(s) mises à jour ».
>
> **Éléments à cadrer :** Bouton scanner + message de résultat (alerte verte ou info).

---

## Upload de fichiers

Pour une version donnée, remplacement ciblé :

- **boot.wim** (Windows)
- **vmlinuz** / **initrd** (Linux)
- **modloop** (Alpine)
- Fichiers **ESXi** (mboot, modules)
- **Script iPXE** personnalisé

Les libellés reflètent les **vrais noms** sur disque (ex. `vmlinuz-lts`).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-upload-row.png`
>
> **Description de la photo :** Une ligne version avec bouton « Téléverser » ou formulaire fichier pour un rôle précis (ex. initrd).
>
> **Éléments à cadrer :** Nom du rôle, input fichier, bouton envoyer.

---

## Arguments noyau (imgargs)

Champ pour modifier les **paramètres kernel** passés au boot iPXE (quiet, console série, repo inst., etc.).

Enregistrement → régénération des menus recommandée (**Menus iPXE → Régénérer tous**).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-kernel-args.png`
>
> **Description de la photo :** Zone texte « Arguments noyau » remplie + bouton sauvegarder.
>
> **Éléments à cadrer :** Champ multiligne, bouton enregistrer.

---

## Alpine — dépôt APK

Pour les versions Alpine : choix entre CDN public ou **URL miroir APK** personnalisée (paramètre `alpine_repo=` dans le menu).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-alpine-repo.png`
>
> **Description de la photo :** Options dépôt APK : switch « dépôt personnalisé » + champ URL.
>
> **Éléments à cadrer :** Switch, champ URL miroir, texte d’aide.

---

## Fedora — mode Live

Bascule **Live boot (squashfs)** : même logique que à l’upload (Live vs netinst).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/06-boot-files-fedora-live.png`
>
> **Description de la photo :** Interrupteur Live sur une version Fedora dans Fichiers Boot.
>
> **Éléments à cadrer :** Libellé Live, état coché/décoché.

---

## Permissions

Même règles que les ISOs : modification uniquement sur **vos** versions (utilisateur) ou toutes (admin).

---

## Voir aussi

- [05-isos-fiche-version.md](05-isos-fiche-version.md)
- [08-menus-ipxe.md](08-menus-ipxe.md)
