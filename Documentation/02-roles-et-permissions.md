# Rôles et permissions

## Deux rôles

| Rôle | Accès menus admin | ISOs | Modification |
|------|-------------------|------|------------|
| **Administrateur** | Oui (Firmware, Supervision, Utilisateurs, Paramètres) | Toutes les versions | Toutes les versions |
| **Utilisateur** | Non | Voit **toutes** les versions | Uniquement **ses** versions (créées avec son compte) |

---

## Compte Utilisateur — lecture vs écriture

- **Lecture** : un utilisateur peut **ouvrir** la fiche de n’importe quelle version ISO (liste, détails, fichiers boot en lecture).
- **Écriture** : upload, extraction, suppression, configs auto, scripts perso — **seulement** sur les versions dont il est **propriétaire** (créées lors de son upload).

Sur une fiche en lecture seule, un bandeau gris s’affiche :

> *Mode lecture seule — vous ne pouvez modifier que les versions que vous avez ajoutées.*

Les boutons d’action (extraire, supprimer, upload) sont absents ou désactivés.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/02-readonly-banner-iso-detail.png`
>
> **Description de la photo :** Fiche d’une version ISO appartenant à **un autre** utilisateur, connecté en compte **user** : bandeau « lecture seule » sous le titre.
>
> **Éléments à cadrer :** Titre OS + version, badge statut (Prêt), bandeau alerte gris, absence du bouton « Extraire » ou zone grisée.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/02-own-version-editable.png`
>
> **Description de la photo :** Même type de fiche mais version **créée par l’utilisateur connecté** : pas de bandeau, barre d’outils avec « Extraire depuis l’ISO » visible.
>
> **Éléments à cadrer :** Boutons d’action dans la carte « Fichiers de boot ».

---

## Menus iPXE générés

- **Édition des `.ipxe` générés** (menu principal, debian.ipxe, etc.) : **administrateur** uniquement.
- **Scripts personnalisés** liés à une version : propriétaire de la version ou admin.

---

## Serveurs distants (chainload)

Ajout / suppression / activation des entrées **Serveurs distants** dans Menus iPXE : **administrateur** uniquement.

---

## Configurations automatiques

- Voir la liste : tout utilisateur connecté (filtrée aux versions possédées pour les actions).
- Créer / modifier / supprimer / publier : sur les versions que l’utilisateur peut modifier.

---

## Suppression de compte

Un administrateur ne peut pas supprimer le **dernier** compte administrateur.

Un compte ne peut pas être supprimé s’il possède encore des **versions ISO** — il faut d’abord les supprimer.

---

## Récapitulatif par page

| Page | User (lecture) | User (propriétaire) | Admin |
|------|----------------|---------------------|-------|
| Tableau de bord | Oui | Oui | Oui |
| ISOs liste | Oui | Oui + ajout | Oui |
| ISO fiche | Oui | Modifier si owner | Tout |
| Fichiers Boot | Oui | Modifier si owner | Tout |
| Configs auto | Oui | Modifier si owner | Tout |
| Menus — générés | Voir | Voir | Éditer |
| Menus — scripts perso | Voir | Gérer si owner | Tout |
| Menus — distants | Voir | — | Gérer |
| Firmware | — | — | Oui |
| Paramètres | — | — | Oui |
| Supervision | — | — | Oui |
| Utilisateurs | — | — | Oui |

Voir aussi [12-gestion-utilisateurs.md](12-gestion-utilisateurs.md).
