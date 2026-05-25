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


![Carte « URL du serveur HTTP » avec champ URL rempli + bouton enregistrer.](images/10-settings-server-url.png)


### Mot de passe administrateur

- Nouveau mot de passe (min. 8 caractères)
- **Changer le mot de passe** — concerne le compte admin connecté (session)


![Carte mot de passe admin : champ masqué + bouton changer.](images/10-settings-admin-password.png)


### Certificat HTTPS (TLS)

- Date d’expiration, jours restants
- Badge alerte si expiration proche
- **Renouveler le certificat** → modale de confirmation (2 ans, reload Nginx, **recompiler firmware ensuite**)


![Carte TLS avec date d’expiration et bouton renouveler.](images/10-settings-tls-card.png)



![Modale « Renouveler le certificat HTTPS ? » avec texte avertissant recompilation iPXE.](images/10-settings-tls-renew-modal.png)


---

## Section Apparence — Image du menu iPXE

- Upload **PNG/JPEG** (max 3 Mo) — logo coin **bas droite** des menus iPXE
- **Aperçu** / masquer aperçu
- **Supprimer l’image personnalisée** → revient au logo bleu intégré
- Régénération automatique des menus après changement


![Carte image menu : champ fichier, bouton téléverser, aperçu du logo.](images/10-settings-menu-logo-upload.png)



![Capture d’un client iPXE ou du PNG servi montrant le logo en bas à droite du menu (écran PXE ou fichier PNG dans navi…](images/10-settings-menu-logo-in-ipxe.png)


---

## Section Système — Chemins configurés

Lecture seule : chemins TFTP, HTTP, ISOs depuis le `.env` serveur.


![Carte chemins avec liste TFTP root, HTTP root, ISOs.](images/10-settings-paths-readonly.png)


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


![Tableau types d’OS avec poignées drag, icônes œil, boutons modifier.](images/10-settings-os-types-table.png)


---

## Éditer un type d’OS

Fiche dédiée (`/settings/os-types/new` ou `/edit/{id}`) :

- **Identité** : slug, label, icône Bootstrap
- **Extraction ISO** : case extraction complète, liste noms de fichiers / motifs
- **Avertissement** : types manuels = script iPXE à écrire vous-même
- **Type config auto** : contraindre preseed / kickstart / etc.


![Formulaire édition type : case extraction complète + tableau lignes pattern/max.](images/10-settings-os-type-edit-extract.png)


---

## Messages flash

Après actions : alertes vertes/rouges en haut (mot de passe OK, image enregistrée, échec TLS, etc.).

---

## Voir aussi

- [04-isos-liste-et-ajout.md](04-isos-liste-et-ajout.md)
- [09-firmware-ipxe.md](09-firmware-ipxe.md)
