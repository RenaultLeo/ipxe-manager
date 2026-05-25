# Dépannage côté interface

Guide des **problèmes fréquents** observés dans le navigateur. Pour l’installation serveur, logs systemd et réseau, voir le [README du projet](../README.md).

---

## Connexion et session

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Redirection infinie vers `/login` | Cookie bloqué, mauvaise URL | HTTPS/HTTP cohérent ; autoriser cookies ; même hostname qu’à l’installation |
| « Identifiant ou mot de passe incorrect » | Mauvais compte | Réinitialiser via admin (**Utilisateurs**) ou compte `admin` |
| Menu admin invisible | Compte **Utilisateur** | Se connecter en administrateur ou demander le rôle |

---

## Menus iPXE — script bloqué sur « Chargement… »

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Texte « Chargement du script… » sans fin | Onglet jamais chargé ; session expirée ; erreur réseau | Changer d’onglet `.ipxe` puis revenir ; **Ctrl+F5** ; se reconnecter |
| Zone vide après chargement | Erreur 401/404 | Vérifier connexion admin pour édition ; fichier manquant → **Régénérer tous les menus** |
| Timeout | Serveur lent ou gros fichier | Réessayer ; vérifier Nginx et disque (Supervision) |

Détail page : [08-menus-ipxe.md](08-menus-ipxe.md).

---

## ISOs et extraction

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Statut « Erreur » après extraction | ISO corrompue, espace disque | Supervision → disque ; ré-extraire ; logs sur la fiche |
| Job infini sur le dashboard | Worker bloqué | **Arrêter** le job ; Supervision → relancer services |
| Version jamais « Prête » | Boot files manquants | Fiche version → liste fichiers ; rescan **Fichiers Boot** |

---

## Firmware

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Cartes « Absent » | Jamais compilé ou échec compile | Relancer **Compiler** ; lire logs dans la carte progression |
| Compile annulée | Action utilisateur | Relancer — les sources restent (plus rapide) |
| PXE OK mais pas HTTPS | Firmware pas recompilé après TLS | Paramètres → renouveler si besoin → Firmware → recompiler |

---

## Paramètres et menus

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Clients PXE pointent encore ancienne IP | URL non enregistrée ou menus pas regénérés | **Enregistrer et régénérer** ; recompiler firmware si embed ancien |
| Logo menu absent | PNG trop gros ou format refusé | Max 3 Mo, PNG/JPEG ; ré-uploader |
| Types d’OS invisibles sur dashboard | Œil barré | Paramètres → tableau types → réactiver œil |

---

## Supervision

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Graphiques vides « — » | Snapshot API en échec | Bouton **Actualiser** ; vérifier service app ; F12 → `/admin/supervision/api/snapshot` |
| sudo indisponible | Worker sans droits | Normal sur certaines installs — audits limités |
| Vérification KO | Service arrêté, chemin manquant | Lire tableau items + log ; corriger sur le serveur puis **rapide** à nouveau |

---

## Certificat TLS

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Alerte orange sur dashboard | Expiration < seuil | Paramètres → **Renouveler** → Firmware → recompiler |
| Navigateur « Non sécurisé » | CA interne | Normal en labo ; importer CA sur les postes d’admin |

---

## Langue et traductions

| Symptôme | Cause probable | Action |
|----------|----------------|--------|
| Mélange FR/EN | Clé manquante dans locale | Signaler la clé ; passer en FR pour la doc |
| Modale en mauvaise langue | Langue changée après ouverture page | Recharger la page après changement de langue |

---

## Bonnes pratiques de diagnostic

1. **Ctrl+F5** (cache navigateur) après mise à jour du serveur.
2. Noter **l’URL exacte**, le **rôle** connecté, l’**heure** du problème.
3. **Tableau de bord** : jobs et disque.
4. **Supervision** : santé + vérification rapide.
5. Pour le PXE : distinguer échec **TFTP** (étape 3) vs **HTTP** (menu) — [15-parcours-boot-pxe.md](15-parcours-boot-pxe.md).

---

## Index des chapitres par page

| Page | Chapitre |
|------|----------|
| `/login` | [01](01-connexion-et-navigation.md) |
| `/` | [03](03-tableau-de-bord.md) |
| `/isos` | [04](04-isos-liste-et-ajout.md), [05](05-isos-fiche-version.md) |
| `/boot-files` | [06](06-fichiers-boot.md) |
| `/ipxe-configs` | [07](07-configurations-automatiques.md) |
| `/ipxe-menus` | [08](08-menus-ipxe.md) |
| `/firmware` | [09](09-firmware-ipxe.md) |
| `/settings` | [10](10-parametres.md) |
| `/admin/supervision` | [11](11-supervision.md) |
| `/admin/users` | [12](12-gestion-utilisateurs.md) |

---

## Voir aussi

- [14-taches-arriere-plan.md](14-taches-arriere-plan.md)
- [README Documentation](README.md)
