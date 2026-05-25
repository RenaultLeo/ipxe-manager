# Supervision

**URL :** `/admin/supervision`  
**Menu :** Supervision (administrateur uniquement)

Page de **santé du serveur** en temps quasi réel et d’**audits d’intégrité** (vérifications fichiers, services, base de données).

---

## Accès et chargement initial

À l’ouverture, l’onglet **Santé** est actif. Un bandeau « Chargement… » apparaît le temps de récupérer le premier **snapshot** (API JSON), puis les graphiques et tableaux se remplissent.

Bouton **Actualiser** (en haut à droite) : relance une requête snapshot sans recharger toute la page.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-loading.png`
>
> **Description de la photo :** Onglet Santé avec spinner « Chargement… » et cartes statistiques encore à « — ».
>
> **Éléments à cadrer :** Bandeau gris avec spinner, bouton Actualiser visible.

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

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-stats-row.png`
>
> **Description de la photo :** Quatre mini-cartes avec valeurs numériques remplies (ex. 8/8 services, CPU 12 %, RAM 45 %).
>
> **Éléments à cadrer :** Les quatre libellés et valeurs, onglet « Santé » actif.

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

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-charts-services.png`
>
> **Description de la photo :** Carte « Services » : donut coloré + tableau en dessous avec icônes vert/rouge par ligne.
>
> **Éléments à cadrer :** En-tête carte, au moins 4 lignes de services (nginx, tftpd, celery, etc.).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-paths-table.png`
>
> **Description de la photo :** Carte « Chemins configurés » : colonnes Statut, Nom, Chemin avec une ligne OK et une ligne KO si possible.
>
> **Éléments à cadrer :** Chemin complet en monospace, icône statut.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-checks-list.png`
>
> **Description de la photo :** Carte « Contrôles » avec liste à puces ou badges OK / avertissement.
>
> **Éléments à cadrer :** Au moins un check vert et un check orange/rouge.

### Pied de page Santé

- **Dernière mise à jour** : horodatage du dernier snapshot
- Indication **sudo** : si l’agent peut exécuter des commandes système pour l’audit (sinon message limité)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-last-update-sudo.png`
>
> **Description de la photo :** Bas de l’onglet Santé : texte « Dernière mise à jour : … » + mention sudo OK ou sudo indisponible.
>
> **Éléments à cadrer :** Horodatage lisible, texte sudo.

---

## Onglet Intégrité

Actions administratives (souvent avec **modale de confirmation** — voir [13-dialogues-et-confirmations.md](13-dialogues-et-confirmations.md)) :

| Bouton | Action |
|--------|--------|
| **Vérification rapide** | Contrôles légers (services, chemins clés) |
| **Vérification complète** | Audit approfondi (plus long) — confirmation |
| **Synchroniser la base** | Réaligner la BDD avec le disque (versions, fichiers) — confirmation |
| **Relancer les services** | Redémarrage stack iPXE (Nginx, workers, etc.) — confirmation |

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-integrity-toolbar.png`
>
> **Description de la photo :** Onglet Intégrité : barre de quatre boutons (rapide, complète, sync BDD, relancer services).
>
> **Éléments à cadrer :** Tous les boutons sur une ligne, texte d’aide en pied de carte si visible.

### Résultat du dernier audit

Après une vérification, une carte affiche :

- Mode **rapide** ou **complète**
- Statut global **OK** (vert) ou **KO** (rouge)
- Durée en secondes
- Tableau des **items** (catégorie, nom, icône succès / avertissement / échec)
- Bloc **log** texte (extrait des dernières lignes)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-verification-ok.png`
>
> **Description de la photo :** Carte résultat « Vérification complète — OK » avec tableau d’items majoritairement verts.
>
> **Éléments à cadrer :** En-tête OK vert, durée « X s », 5+ lignes du tableau.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-verification-ko-log.png`
>
> **Description de la photo :** Résultat KO avec zone `<pre>` de log en bas (erreur fichier manquant, service down, etc.).
>
> **Éléments à cadrer :** Titre KO rouge, extrait log avec message d’erreur lisible.

### État vide

Sans audit encore lancé : message « Aucune vérification pour l’instant » (ou équivalent selon la langue).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-no-verification-yet.png`
>
> **Description de la photo :** Onglet Intégrité sans carte résultat, seulement la barre d’actions et le texte gris d’invitation.
>
> **Éléments à cadrer :** Message central, boutons au-dessus.

---

## Relance des services

Après **Relancer les services** :

- Redirection ou fragment d’URL avec indicateur de redémarrage en cours
- Les services peuvent être **indisponibles** quelques secondes — ne pas fermer le navigateur pendant l’opération

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/11-supervision-services-restarting.png`
>
> **Description de la photo :** Bandeau ou page intermédiaire indiquant que les services redémarrent (si affiché par l’application).
>
> **Éléments à cadrer :** Message « redémarrage en cours », spinner éventuel.

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
