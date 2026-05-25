# Configurations automatiques

**URL :** `/ipxe-configs`  
**Menu :** Configs auto (ou « Configurations automatiques »)

Gestion des fichiers d’**installation automatique** liés à une **version ISO** : preseed, kickstart, cloud-init, autounattend, answer.toml, etc.

---

## Liste des configurations

Tableau : OS / version, **type** de config, libellé, fichier, date, actions (éditer, supprimer).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-list.png`
>
> **Description de la photo :** Page liste configs avec plusieurs lignes (Debian preseed, Ubuntu user-data, etc.).
>
> **Éléments à cadrer :** Bouton « Nouvelle config » / « Scanner », colonnes Type et Version.

---

## Scanner configs/

Comme pour boot/ : importe les fichiers déjà présents sous `configs/` sur le disque mais non enregistrés en base.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-scan-button.png`
>
> **Description de la photo :** Bouton « Scanner fichiers » et message de résultat (N importés, M ignorés).
>
> **Éléments à cadrer :** Bouton + alerte résultat.

---

## Créer une configuration

**Formulaire** (nouvelle config) :

| Champ | Description |
|-------|-------------|
| Version OS | Version cible |
| Type | preseed, kickstart, Ubuntu autoinstall (paire user-data+meta-data), proxmox-answer, alpine-answer, custom… |
| Nom du dossier | Ex. `config-1`, `prod-web` — chemin sous la version |
| Libellé menu iPXE | Texte affiché dans le sous-menu d’install |
| Contenu | Éditeur (CodeMirror sur la page d’édition) |

Les types **imposés** par le seed pour les OS intégrés (ex. Debian → preseed) : le type est verrouillé à la création.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-new-form.png`
>
> **Description de la photo :** Formulaire nouvelle config : version sélectionnée, type preseed, nom de dossier.
>
> **Éléments à cadrer :** Liste déroulante type, champ contenu ou lien vers éditeur.

---

## Éditer une configuration

Page d’édition avec **éditeur de code** (coloration selon le type : shell, XML, YAML).

- Boutons **Modèles** : insère un template vide (preseed, kickstart, unattended, cloud-init, etc.)
- Variables documentées dans l’aide (hostname, miroir, etc.)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-editor-codemirror.png`
>
> **Description de la photo :** Éditeur plein écran avec coloration syntaxique (thème sombre Dracula).
>
> **Éléments à cadrer :** Zone CodeMirror, boutons Modèles / Enregistrer, liste des variables disponibles sur le côté ou en dessous.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-templates-dropdown.png`
>
> **Description de la photo :** Menu ou boutons « Modèles » avec choix Preseed / Kickstart / Unattend visibles.
>
> **Éléments à cadrer :** Libellés des templates, un template en surbrillance.

---

## Types par famille (référence)

| OS / famille | Type UI | Fichiers typiques |
|--------------|---------|-------------------|
| Debian | preseed | `preseed.cfg` |
| Ubuntu | autoinstall | `user-data` + `meta-data` |
| RHEL / Rocky / Alma / Fedora / ESXi | kickstart | `ks.cfg` |
| Windows | unattend | `autounattend.xml` |
| Proxmox | proxmox-answer | `answer.toml` |
| Alpine | alpine-answer | `answers` / apkovl |

---

## Config active (Ubuntu)

Sur la fiche ISO Ubuntu : **Publier sur la version** copie user-data et meta-data vers `boot/ubuntu/<version>/` et les définit comme **actives** pour le menu iPXE (`ds=nocloud`).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/07-configs-ubuntu-publish-active.png`
>
> **Description de la photo :** Fiche ISO Ubuntu OU liste configs avec badge « Active » et bouton Publier.
>
> **Éléments à cadrer :** Badge Actif, bouton « Publier sur la version ».

---

## Proxmox

`answer.toml` publié ; si config active, tâche Celery peut préparer `proxmox-netboot-autoinstall.iso` (assistant Proxmox).

---

## Après modification

Pensez à **Régénérer tous les menus** ([08-menus-ipxe.md](08-menus-ipxe.md)) pour que les entrées d’install et les URLs de config soient à jour.

---

## Voir aussi

- [05-isos-fiche-version.md](05-isos-fiche-version.md)
- [10-parametres.md](10-parametres.md) — types d’OS et contrainte autoconfig
