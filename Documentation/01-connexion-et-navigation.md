# Connexion et navigation

## Accéder au site

Ouvrez un navigateur et allez à l’adresse de votre serveur, par exemple :

```text
http://192.168.2.6/
```

Si HTTPS est activé sur votre déploiement :

```text
https://192.168.2.6/
```

(Le navigateur peut afficher « Non sécurisé » avec un certificat interne — comportement normal.)

---

## Page de connexion

Si vous n’êtes pas connecté, toute URL renvoie vers **`/login`**.

| Champ | Description |
|-------|-------------|
| Identifiant | Nom d’utilisateur (minuscules, chiffres, tirets) |
| Mot de passe | Mot de passe du compte |

Identifiants par défaut après installation : **`admin`** / **`admin`** — à changer immédiatement dans **Paramètres**.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-login-form-empty.png`
>
> **Description de la photo :** Page de connexion complète, formulaire vide, logo « iPXE Manager » visible.
>
> **Éléments à cadrer :** Champs Identifiant / Mot de passe, bouton de connexion, fond sombre de l’application.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-login-error-message.png`
>
> **Description de la photo :** Tentative avec mauvais mot de passe : message d’erreur affiché sous le formulaire.
>
> **Éléments à cadrer :** Bandeau ou texte d’erreur « Identifiant ou mot de passe incorrect » (libellé selon la langue).

---

## Barre de navigation (après connexion)

Barre **sticky** en haut de toutes les pages :

### Groupe « Exploitation » (tous les utilisateurs connectés)

| Icône | Menu | Rôle |
|-------|------|------|
| Compteur | **Tableau de bord** | Vue d’ensemble |
| Disque | **ISOs** | Versions et ISOs |
| Pile | **Fichiers Boot** | Fichiers par version |
| Code | **Configs auto** | Preseed, cloud-init, etc. |
| Liste | **Menus iPXE** | Scripts `.ipxe` |

### Groupe « Administration » (compte **Administrateur** uniquement)

| Menu | Rôle |
|------|------|
| **Firmware** | Compilation iPXE → TFTP |
| **Supervision** | Santé serveur, audits |
| **Utilisateurs** | Comptes, mots de passe |
| **Paramètres** | URL, TLS, types d’OS, logo menu |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-navbar-admin-full.png`
>
> **Description de la photo :** Barre de navigation avec un compte **admin** connecté : tous les menus visibles, un onglet actif (ex. Tableau de bord) surligné.
>
> **Éléments à cadrer :** Logo « iPXE Manager » à gauche ; liens Exploitation + Administration ; à droite le sélecteur de langue et le bouton Déconnexion.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-navbar-user-limited.png`
>
> **Description de la photo :** Même barre avec un compte **utilisateur** : pas de Firmware, Supervision, Utilisateurs, Paramètres.
>
> **Éléments à cadrer :** Contraste avec la capture admin (idéalement même résolution).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-navbar-mobile-collapsed.png`
>
> **Description de la photo :** Fenêtre étroite (ou F12 mode mobile) : menu hamburger ouvert montrant les entrées empilées.
>
> **Éléments à cadrer :** Bouton « Menu » (trois traits), liste déroulante des liens.

---

## Sélecteur de langue

En haut à droite : bouton du type **FR** / **EN** avec icône traduction.

- Cliquez → liste : Français, English, Deutsch, Español, Italiano, Português.
- La page se recharge dans la langue choisie (cookie `lang`).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/01-language-dropdown-open.png`
>
> **Description de la photo :** Menu déroulant des langues ouvert, une langue surlignée au survol.
>
> **Éléments à cadrer :** Bouton langue actif + liste des 6 langues, sans masquer le reste de la page.

---

## Déconnexion

Bouton **Déconnexion** (icône porte sortante) à droite de la barre → retour à `/login`.

---

## Pages suivantes

- [02-roles-et-permissions.md](02-roles-et-permissions.md)
- [03-tableau-de-bord.md](03-tableau-de-bord.md)
