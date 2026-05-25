# ISOs — Liste et ajout de version

## Liste des ISOs

**URL :** `/isos`  
**Menu :** ISOs

Tableau des **versions** (une ligne = une version d’un type d’OS : ex. Debian 12, Ubuntu 22.04 LTS).

| Colonne | Description |
|---------|-------------|
| OS | Type (Debian, Ubuntu, …) |
| Version | Libellé choisi à l’ajout |
| Taille ISO | Taille du fichier ISO sur disque |
| Statut | Prêt, Extraction en cours, Erreur, Uploadé |
| Date | Date d’ajout |
| Actions | Détail, suppression, etc. |

**Statuts :**

- **Uploadé** — ISO ou fichiers enregistrés, extraction pas encore lancée (ou pas d’ISO).
- **Extraction en cours** — tâche Celery ; la ligne peut se mettre à jour automatiquement (polling).
- **Prêt** — Fichiers de boot utilisables pour les menus.
- **Erreur** — Échec d’extraction ; ouvrir la fiche pour le détail.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-list-table.png`
>
> **Description de la photo :** Page ISOs complète avec plusieurs lignes et statuts différents (au moins un « Prêt », un « Erreur » si possible).
>
> **Éléments à cadrer :** Bouton « Ajouter une version » en haut, en-têtes tableau, badges de couleur des statuts.

---

## Ajouter une version

**URL :** `/isos/upload`  
Bouton depuis la liste ISOs ou le tableau de bord.

### Champs principaux

| Champ | Obligatoire | Rôle |
|-------|-------------|------|
| Type d’OS | Oui | Debian, Ubuntu, Windows, Fedora, Proxmox, type personnalisé… |
| Libellé de version | Oui | Ex. `22.04 LTS`, `11`, `2022` |
| Fichier ISO | Non | Si fourni → extraction automatique possible ensuite |
| Arguments noyau | Non | Paramètres kernel ajoutés au boot |
| Script iPXE personnalisé | Non | Fichier `.ipxe` → sous-menu « Autres » |
| Notes | Non | Mémo interne |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-form-top.png`
>
> **Description de la photo :** Haut du formulaire d’ajout : sélecteur type d’OS + champ libellé version remplis.
>
> **Éléments à cadrer :** Liste déroulante OS, champ version, encadré info bleu en haut si présent.

---

### Section ISO

- Choix du fichier `.iso` ou `.img`.
- Texte d’aide : l’extraction des fichiers de boot pourra être lancée **depuis la fiche version** (pas forcément pendant l’upload).
- **Doublon** : si même type + libellé + nom de fichier ISO → badge avertissement avant envoi.


> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-iso-section.png`
>
> **Description de la photo :** Carte « Fichier ISO » avec bouton Parcourir / zone fichier, option Fedora Live visible si Fedora sélectionné.
>
> **Éléments à cadrer :** Input fichier, texte d’aide sous le champ, switch Fedora si applicable.

---

### Fichiers boot manuels (sans ISO)

Selon le type d’OS, des sections apparaissent :

- **Linux** : vmlinuz, initrd, modloop (Alpine), dépôt APK personnalisé (Alpine).
- **Windows** : boot.wim (BCD/boot.sdi souvent issus de l’ISO si vous en uploadez une).

**Ubuntu** : choix variante **Server** vs **Desktop** ; message indiquant extraction complète type casper.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-linux-fields.png`
>
> **Description de la photo :** Type Linux sélectionné (ex. Debian) : champs vmlinuz / initrd visibles.
>
> **Éléments à cadrer :** Section « Fichiers boot », noms des champs fichier.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-windows-bootwim.png`
>
> **Description de la photo :** Type Windows : champ boot.wim mis en avant, texte sur BCD/boot.sdi automatiques depuis ISO.
>
> **Éléments à cadrer :** Champ boot.wim, encadré d’information Windows.

---

### Upload en cours

L’envoi d’une grosse ISO utilise souvent **XHR** avec barre de progression (pas de rechargement brutal de page).

Messages possibles : espace disque insuffisant, doublon, erreur réseau.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-progress-bar.png`
>
> **Description de la photo :** Pendant l’upload : barre de progression ou texte « Upload en cours… » sous le formulaire.
>
> **Éléments à cadrer :** Barre % ou spinner, bouton désactivé.

---

### Plan d’extraction (types d’OS personnalisés)

Pour les types configurés dans **Paramètres**, un encadré peut lister les **noms de fichiers** recherchés dans l’ISO selon la fiche « Extraction ISO » du type.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/04-isos-upload-extract-plan-badges.png`
>
> **Description de la photo :** Sous le formulaire, badges ou liste « Fichiers recherchés dans l’ISO » pour un type OS custom.
>
> **Éléments à cadrer :** Badges de noms de fichiers (vmlinuz, initrd, …), texte explicatif.

---

## Après l’ajout

Redirection vers la **fiche version** (`/isos/{id}`) ou la liste.  
Suite : [05-isos-fiche-version.md](05-isos-fiche-version.md).
