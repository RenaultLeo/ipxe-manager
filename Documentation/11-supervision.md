# Supervision

**URL :** `/admin/supervision`  
**Menu :** Supervision (administrateur uniquement)

Page de **santé du serveur** en temps quasi réel et d’**audits d’intégrité** (vérifications fichiers, services, base de données).

---

## Accès et chargement initial

À l’ouverture, l’onglet **Santé** est actif. Un bandeau « Chargement… » apparaît le temps de récupérer le premier **snapshot** (API JSON), puis les graphiques et tableaux se remplissent.

Bouton **Actualiser** (en haut à droite) : relance une requête snapshot sans recharger toute la page.


![Onglet Santé avec spinner « Chargement… » et cartes statistiques encore à « — ».](images/11-supervision-loading.png)


---

## Onglet Santé

### Cartes résumé (ligne du haut)

Quatre indicateurs compacts :

| Carte | Contenu |
|-------|---------|
| Services | Nombre actifs / total |
| CPU | Utilisation % |
| RAM | Utilisation % |
| Ports | Ports d’écoute utiles ouverts |


![Quatre mini-cartes avec valeurs numériques remplies (ex. 8/8 services, CPU 12 %, RAM 45 %).](images/11-supervision-stats-row.png)


### Graphiques et tableaux

| Zone | Rôle |
|------|------|
| **Services** | Donut Chart.js + tableau nom / état (actif, inactif, absent) |
| **Ports** | Graphique des ports réseau surveillés |
| **Ressources** | CPU + RAM combinés |
| **Disque** | Barres par point de montage (usage %) |
| **Hôte** | Tableau machines / VM détectées + détails texte |
| **Chemins** | TFTP, HTTP, ISOs, etc. — existe / manquant |
| **Contrôles** | Liste de checks rapides (binaires, permissions, …) |

Les graphiques se mettent à jour lors de chaque snapshot (rafraîchissement périodique côté navigateur).


![Carte « Services » : donut coloré + tableau en dessous avec icônes vert/rouge par ligne.](images/11-supervision-charts-services.png)



![Carte « Chemins configurés » : colonnes Statut, Nom, Chemin avec une ligne OK et une ligne KO si possible.](images/11-supervision-paths-table.png)



![Carte « Contrôles » avec liste à puces ou badges OK / avertissement.](images/11-supervision-checks-list.png)


### Pied de page Santé

- **Dernière mise à jour** : horodatage du dernier snapshot
- Indication **sudo** : si l’agent peut exécuter des commandes système pour l’audit (sinon message limité)


![Bas de l’onglet Santé : texte « Dernière mise à jour : … » + mention sudo OK ou sudo indisponible.](images/11-supervision-last-update-sudo.png)


---

## Onglet Intégrité

Actions administratives (souvent avec **modale de confirmation** — voir [13-dialogues-et-confirmations.md](13-dialogues-et-confirmations.md)) :

| Bouton | Action |
|--------|--------|
| **Vérification rapide** | Contrôles légers (services, chemins clés) |
| **Vérification complète** | Audit approfondi (plus long) — confirmation |
| **Synchroniser la base** | Réaligner la BDD avec le disque (versions, fichiers) — confirmation |
| **Relancer les services** | Redémarrage stack iPXE (Nginx, workers, etc.) — confirmation |


### Résultat du dernier audit

Après une vérification, une carte affiche :

- Mode **rapide** ou **complète**
- Statut global **OK** (vert) ou **KO** (rouge)
- Durée en secondes
- Tableau des **items** (catégorie, nom, icône succès / avertissement / échec)
- Bloc **log** texte (extrait des dernières lignes)



### État vide

Sans audit encore lancé : message « Aucune vérification pour l’instant » (ou équivalent selon la langue).


---

## Relance des services

Après **Relancer les services** :

- Redirection ou fragment d’URL avec indicateur de redémarrage en cours
- Les services peuvent être **indisponibles** quelques secondes — ne pas fermer le navigateur pendant l’opération

---

## Quand utiliser cette page ?

| Situation | Action recommandée |
|-----------|-------------------|
| PXE ne boot plus après mise à jour | Santé → vérifier Nginx / TFTP / ports |
| Fichiers manquants sur disque | Intégrité → vérification complète |
| BDD désynchronisée (ISO supprimée à la main) | Sync base |
| Changement `.env` ou certificat | Relancer services puis recompiler firmware si HTTPS |

---

## Voir aussi

- [12-gestion-utilisateurs.md](12-gestion-utilisateurs.md) — comptes (relance aussi disponible ici via Supervision)
- [14-taches-arriere-plan.md](14-taches-arriere-plan.md)
- [16-depannage-interface.md](16-depannage-interface.md)
