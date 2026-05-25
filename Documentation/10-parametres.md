# Paramètres

**URL :** `/settings`  
**Menu :** Paramètres (administrateur)

Configuration globale du site et de la génération des menus.

---

## Section Serveur

Trois cartes côte à côte :

### URL du serveur HTTP

- Champ **URL de base** (ex. `http://192.168.2.6`)
- Intégrée dans **tous** les `.ipxe` générés
- Bouton **Enregistrer et régénérer les menus** → sauve + tâche regen menus

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-server-url.png`
>
> **Description de la photo :** Carte « URL du serveur HTTP » avec champ URL rempli + bouton enregistrer.
>
> **Éléments à cadrer :** Input type url, texte d’aide sous le champ, bouton primary.

### Mot de passe administrateur

- Nouveau mot de passe (min. 8 caractères)
- **Changer le mot de passe** — concerne le compte admin connecté (session)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-admin-password.png`
>
> **Description de la photo :** Carte mot de passe admin : champ masqué + bouton changer.
>
> **Éléments à cadrer :** Champ password, bouton outline warning.

### Certificat HTTPS (TLS)

- Date d’expiration, jours restants
- Badge alerte si expiration proche
- **Renouveler le certificat** → modale de confirmation (2 ans, reload Nginx, **recompiler firmware ensuite**)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-tls-card.png`
>
> **Description de la photo :** Carte TLS avec date d’expiration et bouton renouveler.
>
> **Éléments à cadrer :** Dates, badge jours restants, bouton renouveler.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-tls-renew-modal.png`
>
> **Description de la photo :** Modale « Renouveler le certificat HTTPS ? » avec texte avertissant recompilation iPXE.
>
> **Éléments à cadrer :** Titre modale, paragraphe 2 ans + Nginx, bouton « Oui, renouveler ».

---

## Section Apparence — Image du menu iPXE

- Upload **PNG/JPEG** (max 3 Mo) — logo coin **bas droite** des menus iPXE
- **Aperçu** / masquer aperçu
- **Supprimer l’image personnalisée** → revient au logo bleu intégré
- Régénération automatique des menus après changement

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-menu-logo-upload.png`
>
> **Description de la photo :** Carte image menu : champ fichier, bouton téléverser, aperçu du logo.
>
> **Éléments à cadrer :** Aperçu image dans le cadre, bouton supprimer image.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-menu-logo-in-ipxe.png`
>
> **Description de la photo :** Capture d’un client iPXE ou du PNG servi montrant le logo en bas à droite du menu (écran PXE ou fichier PNG dans navigateur).
>
> **Éléments à cadrer :** Logo personnalisé visible dans le menu bleu iPXE.

---

## Section Système — Chemins configurés

Lecture seule : chemins TFTP, HTTP, ISOs depuis le `.env` serveur.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-paths-readonly.png`
>
> **Description de la photo :** Carte chemins avec liste TFTP root, HTTP root, ISOs.
>
> **Éléments à cadrer :** Libellés et chemins monospace.

---

## Types d’OS

Tableau des types (slug, label, type boot, visibilité dashboard).

| Action | Rôle |
|--------|------|
| **Glisser-déposer** | Ordre dans menus iPXE et onglets ISO |
| **Œil** | Afficher / masquer carte sur tableau de bord |
| **Modifier** | Fiche type (extraction ISO, patterns fichiers, type autoconfig) |
| **Ajouter** | Nouveau type personnalisé |
| **Supprimer** | Types intégrés (seed) : **non supprimables** |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-os-types-table.png`
>
> **Description de la photo :** Tableau types d’OS avec poignées drag, icônes œil, boutons modifier.
>
> **Éléments à cadrer :** Colonne ordre, œil barré sur une ligne, slug + label.

---

## Éditer un type d’OS

Fiche dédiée (`/settings/os-types/new` ou `/edit/{id}`) :

- **Identité** : slug, label, icône Bootstrap
- **Extraction ISO** : case extraction complète, liste noms de fichiers / motifs
- **Avertissement** : types manuels = script iPXE à écrire vous-même
- **Type config auto** : contraindre preseed / kickstart / etc.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-os-type-edit-extract.png`
>
> **Description de la photo :** Formulaire édition type : case extraction complète + tableau lignes pattern/max.
>
> **Éléments à cadrer :** Checkbox vollextract, boutons ajouter/retirer ligne, colonnes pattern et Max.

---

## Messages flash

Après actions : alertes vertes/rouges en haut (mot de passe OK, image enregistrée, échec TLS, etc.).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/10-settings-success-alert.png`
>
> **Description de la photo :** Alerte verte « Image enregistrée. Menus en cours de régénération » en haut de Paramètres.
>
> **Éléments à cadrer :** Bandeau succès dismissible.

---

## Voir aussi

- [04-isos-liste-et-ajout.md](04-isos-liste-et-ajout.md)
- [09-firmware-ipxe.md](09-firmware-ipxe.md)
