# Tableau de bord

**URL :** `/`  
**Menu :** Tableau de bord (icône compteur de vitesse)

Page d’accueil après connexion : état du serveur, tâches en cours, accès rapide aux OS.

---

## Bandeau titre et jobs actifs

Si des tâches Celery tournent (extraction, compilation firmware, etc.) :

- Badge **« X job(s) en cours »** (jaune, icône qui tourne).
- Bouton **Tout arrêter** (admin / selon configuration) pour forcer l’arrêt des uploads suivis.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-jobs-running.png`
>
> **Description de la photo :** Haut de la page tableau de bord avec au moins un job actif : badge jaune + tableau des jobs sous le titre.
>
> **Éléments à cadrer :** Titre « Tableau de bord », badge jobs, bouton rouge « Tout arrêter » si visible.

---

## Alerte certificat TLS

Si le certificat HTTPS expire bientôt : alerte **orange** avec lien vers **Paramètres → Renouveler le certificat**.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-tls-warning.png`
>
> **Description de la photo :** Bandeau d’avertissement certificat sous le titre (si déployé avec TLS).
>
> **Éléments à cadrer :** Texte « Certificat HTTPS bientôt expiré », lien vers Paramètres, bouton fermer (×).

---

## Tableau des jobs en cours

Colonnes typiques :

| Colonne | Signification |
|---------|---------------|
| Fichier | Nom du fichier ou libellé de tâche |
| Type | ISO, Extraction, etc. |
| Taille | Taille concernée |
| Démarré | Heure locale (fuseau du **navigateur**) |
| Durée | Compteur mis à jour en direct |
| Action | **Arrêter** ce job (confirmation) |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-jobs-table-detail.png`
>
> **Description de la photo :** Carte « Jobs en cours » avec une ou deux lignes, durée qui s’incrémente.
>
> **Éléments à cadrer :** En-têtes de colonnes, une ligne type « Extraction », bouton Arrêter sur la ligne.

---

## Espace disque

Carte avec barre de progression :

- **Utilisé** / **Total** (Go)
- Couleur : vert → orange → rouge selon le pourcentage (> 65 %, > 85 %)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-disk-bar.png`
>
> **Description de la photo :** Carte disque seule, barre de progression avec pourcentage au centre.
>
> **Éléments à cadrer :** Titre carte disque, libellés Go utilisé/total, barre colorée.

---

## Cartes par type d’OS

Une carte par **type d’OS** configuré (Debian, Ubuntu, Windows, etc.) :

- Nombre de versions **prêtes** / **total**
- Bouton **Gérer** → liste ISOs filtrée ou page ISOs
- Icône **œil** : masquer la carte du tableau de bord (réglage dans **Paramètres**, ordre des types d’OS)

Les types masqués du tableau de bord restent utilisables ailleurs (ISOs, menus).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-os-cards-grid.png`
>
> **Description de la photo :** Grille de plusieurs cartes OS (ex. Debian, Ubuntu, Windows) avec compteurs et boutons.
>
> **Éléments à cadrer :** Au moins 3 cartes, une avec badge « X prêtes / Y total », bouton Gérer.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-os-card-eye-hidden.png`
>
> **Description de la photo :** Page Paramètres montrant une ligne type d’OS avec icône œil barré — **ou** message sur le dashboard quand toutes les cartes sont masquées.
>
> **Éléments à cadrer :** Lien entre œil dans Paramètres et absence de carte sur le dashboard (deux captures si besoin).

---

## Raccourcis en bas de page

Souvent :

- **Ajouter un OS** → `/isos/upload`
- **Menus iPXE** → `/ipxe-menus`
- **Nouvelle config auto** → création config

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/03-dashboard-quick-links.png`
>
> **Description de la photo :** Zone bas de page avec boutons ou liens rapides (Preseed / menus / upload).
>
> **Éléments à cadrer :** Libellés des raccourcis et icônes associées.

---

## Voir aussi

- [14-taches-arriere-plan.md](14-taches-arriere-plan.md) — détail Celery
- [04-isos-liste-et-ajout.md](04-isos-liste-et-ajout.md)
