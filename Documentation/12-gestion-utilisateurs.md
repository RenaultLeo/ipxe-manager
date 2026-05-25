# Gestion des utilisateurs

**URL :** `/admin/users`  
**Menu :** Utilisateurs (administrateur uniquement)

Création de comptes, changement de mots de passe et suppression d’utilisateurs (sauf soi-même).

---

## Vue d’ensemble

Page en **deux colonnes** :

| Colonne | Contenu |
|---------|---------|
| Gauche (étroite) | Formulaire **Ajouter un utilisateur** |
| Droite (large) | **Liste** des comptes existants |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/12-users-page-layout.png`
>
> **Description de la photo :** Page Utilisateurs complète : carte création à gauche, liste à droite avec badge nombre d’utilisateurs.
>
> **Éléments à cadrer :** Titre « Gestion des utilisateurs », sous-titre, badge « 2 » (ou plus) sur la liste.

---

## Créer un utilisateur

Champs du formulaire :

| Champ | Règle |
|-------|--------|
| **Identifiant** | Minuscules, chiffres, tirets/underscores ; 3–32 caractères ; doit commencer par `a-z` ou `0-9` |
| **Mot de passe** | Minimum **6** caractères |
| **Rôle** | **Utilisateur** ou **Administrateur** |

Bouton **Créer** : enregistre et affiche un message flash en haut si succès ou erreur (identifiant déjà pris, etc.).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/12-users-create-form-filled.png`
>
> **Description de la photo :** Formulaire gauche rempli (ex. identifiant `tech1`, rôle Utilisateur) avant clic Créer.
>
> **Éléments à cadrer :** Les trois champs, texte d’aide sous l’identifiant, bouton bleu pleine largeur.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/12-users-create-success-alert.png`
>
> **Description de la photo :** Alerte bleue/verte en haut après création réussie ; nouvelle ligne visible dans la liste à droite.
>
> **Éléments à cadrer :** Message flash + ligne `tech1` avec badge Utilisateur.

---

## Rôles

| Rôle | Badge | Capacités (résumé) |
|------|-------|---------------------|
| **Administrateur** | Rouge | Toutes les pages + Firmware, Supervision, Utilisateurs, Paramètres |
| **Utilisateur** | Gris | ISOs (ses versions), boot, configs, menus en lecture/écriture selon propriété — pas d’admin |

Détail complet : [02-roles-et-permissions.md](02-roles-et-permissions.md).

---

## Changer le mot de passe d’un compte

Pour **chaque ligne** de la liste :

1. Saisir le **nouveau mot de passe** dans le champ à droite du nom (min. 6 caractères).
2. Cliquer **Changer le mot de passe** (icône clé).

Cela ne change **pas** votre propre session : c’est le mot de passe du compte ciblé.

---

## Supprimer un utilisateur

- Bouton **poubelle** rouge à droite de la ligne.
- **Impossible** de supprimer le compte avec lequel vous êtes connecté (pas de bouton sur votre propre ligne).
- **Confirmation** modale avant suppression définitive.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/12-users-delete-confirm-modal.png`
>
> **Description de la photo :** Modale « Supprimer cet utilisateur ? » (variante danger, bouton rouge).
>
> **Éléments à cadrer :** Texte avec nom d’utilisateur, boutons Annuler / Confirmer.

---

## Compte administrateur par défaut

Après installation : **`admin`** / **`admin`**.

**Recommandation :**

1. Se connecter en `admin`.
2. **Paramètres** → changer le mot de passe administrateur (compte connecté).
3. Créer des comptes **Utilisateur** pour les opérateurs quotidiens.
4. Ne pas partager le compte `admin`.

Voir [10-parametres.md](10-parametres.md).

---

## Messages d’erreur courants

| Message / comportement | Cause probable |
|------------------------|----------------|
| Identifiant invalide | Caractères majuscules, espaces, ou pattern non respecté |
| Utilisateur existe déjà | Identifiant déjà en base |
| Mot de passe trop court | Moins de 6 caractères |

---

## Voir aussi

- [01-connexion-et-navigation.md](01-connexion-et-navigation.md)
- [02-roles-et-permissions.md](02-roles-et-permissions.md)
- [11-supervision.md](11-supervision.md) — relance services (pas sur la page Utilisateurs)
