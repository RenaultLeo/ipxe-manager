# Tableau de bord

**URL :** `/`  
**Menu :** Tableau de bord (icône compteur de vitesse)

Page d’accueil après connexion : état du serveur, tâches en cours, accès rapide aux OS.

---

## Bandeau titre et jobs actifs

Si des tâches Celery tournent (extraction, compilation firmware, etc.) :

- Badge **« X job(s) en cours »** (jaune, icône qui tourne).
- Bouton **Tout arrêter** (admin / selon configuration) pour forcer l’arrêt des uploads suivis.


![Haut de la page tableau de bord avec au moins un job actif : badge jaune + tableau des jobs sous le titre.](images/03-dashboard-jobs-running.png)


---

## Alerte certificat TLS

Si le certificat HTTPS expire bientôt : alerte **orange** avec lien vers **Paramètres → Renouveler le certificat**.

![Bandeau d’avertissement certificat TLS sur le tableau de bord : date d’expiration, jours restants, lien vers Paramètres.](images/03-dashboard-tls-renew.png)

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


![Carte « Jobs en cours » avec une ou deux lignes, durée qui s’incrémente.](images/03-dashboard-jobs-table-detail.png)


---

## Espace disque

Carte avec barre de progression :

- **Utilisé** / **Total** (Go)
- Couleur : vert → orange → rouge selon le pourcentage (> 65 %, > 85 %)

![Carte disque : barre de progression avec pourcentage utilisé et libellés Go utilisé / total.](images/03-dashboard-disk-bar.png)

---

## Cartes par type d’OS

Une carte par **type d’OS** configuré (Debian, Ubuntu, Windows, etc.) :

- Nombre de versions **prêtes** / **total**
- Bouton **Gérer** → liste ISOs filtrée ou page ISOs
- Icône **œil** : masquer la carte du tableau de bord (réglage dans **Paramètres**, ordre des types d’OS)

Les types masqués du tableau de bord restent utilisables ailleurs (ISOs, menus).

![Grille de plusieurs cartes OS (ex. Debian, Ubuntu, Windows) avec compteurs et boutons.](images/03-dashboard-os-cards-grid.png)

![Paramètres : type d’OS avec icône œil barré (carte masquée du tableau de bord).](images/03-dashboard-os-cards-eye-hidden.png)



---

## Raccourcis en bas de page

Souvent :

- **Ajouter un OS** → `/isos/upload`
- **Menus iPXE** → `/ipxe-menus`
- **Nouvelle config auto** → création config


![Zone bas de page avec boutons ou liens rapides (Preseed / menus / upload).](images/03-dashboard-quick-links.png)


---

## Voir aussi

- [14-taches-arriere-plan.md](14-taches-arriere-plan.md) — détail Celery
- [04-isos-liste-et-ajout.md](04-isos-liste-et-ajout.md)
