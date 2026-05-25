# Dialogues et confirmations

L’application utilise une **modale de confirmation unique** pour les actions sensibles, au lieu de la boîte `confirm()` du navigateur.

Fichier technique : `app/templates/_confirm_modal.html` + script `static/js/app.js`.

---

## Apparence standard

| Élément | Description |
|---------|-------------|
| Titre | Par défaut « Confirmation » ; personnalisable par action |
| Icône | Triangle d’avertissement (couleur selon gravité) |
| Corps | Texte explicatif de l’action |
| **Annuler** | Ferme la modale sans rien faire |
| **Confirmer** | Soumet le formulaire ou exécute l’action |

---

## Variantes de bouton Confirmer

L’attribut `data-confirm-variant` sur le formulaire change la couleur du bouton principal :

| Variante | Usage typique |
|----------|----------------|
| `primary` (défaut) | Actions neutres ou informatives |
| `warning` | Ré-extraction ISO, audit complet, sync BDD |
| `danger` | Suppression ISO, utilisateur, script menu, arrêt jobs |
| `info` | Renouvellement certificat TLS |

---

## Où la modale apparaît (liste non exhaustive)

| Page | Action déclenchant la modale |
|------|------------------------------|
| **Tableau de bord** | Arrêter un job ; Tout arrêter |
| **ISOs** (liste / fiche) | Supprimer version ; Ré-extraire |
| **Configs auto** | Supprimer une config |
| **Menus iPXE** | Supprimer script personnalisé |
| **Firmware** | Annuler compilation en cours |
| **Paramètres** | Renouveler TLS ; Supprimer logo menu ; Supprimer type d’OS |
| **Supervision** | Vérification complète ; Sync BDD ; Relancer services |
| **Utilisateurs** | Supprimer un compte |

Chaque chapitre dédié décrit le **texte** attendu dans la langue choisie.

---

## Comportement utilisateur

1. Clic sur le bouton d’action (ex. Supprimer).
2. La modale s’ouvre — **le formulaire n’est pas encore envoyé**.
3. **Annuler** ou clic hors modale / Échap : rien ne se passe.
4. **Confirmer** : le POST ou la navigation prévue s’exécute.

Pas de double confirmation : une seule modale par action.

---

## Sélecteur de langue

Les textes de modale suivent la **langue de l’interface** (FR, EN, DE, ES, IT, PT). Changez la langue **avant** l’action si vous documentez dans une langue précise.

---

## Alertes non modales

Certaines infos utilisent des **alertes Bootstrap** en haut de page (vert succès, rouge erreur, bleu info) — pas de confirmation :

- Mot de passe changé
- URL serveur enregistrée
- Message retour Supervision

---

## Voir aussi

- [01-connexion-et-navigation.md](01-connexion-et-navigation.md) — changement de langue
- Tous les chapitres 03–12 pour les actions concernées
