# Tâches en arrière-plan (Celery)

Les opérations **longues** ne bloquent pas le navigateur : elles partent dans une file **Celery** exécutée par un worker sur le serveur.

Vous suivez l’avancement sur le **Tableau de bord** et parfois sur la page concernée (barre de progression, logs).

---

## Principe

```text
Clic dans l’UI → API FastAPI → tâche Celery enqueued → worker exécute → état en base / logs
```

Tant qu’un job est actif :

- Badge **jobs en cours** sur le tableau de bord
- Tableau avec durée mise à jour
- Possibilité **Arrêter** (upload / extraction suivie) — avec confirmation

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-jobs-dashboard-badge.png`
>
> **Description de la photo :** Tableau de bord avec badge jaune « N job(s) en cours » pendant une extraction.
>
> **Éléments à cadrer :** Badge + au moins une ligne dans le tableau jobs.

---

## Types de tâches visibles dans l’UI

| Type (libellé UI) | Déclenché depuis | Durée typique |
|-------------------|------------------|---------------|
| **Extraction ISO** | Fiche version → Extraire | Minutes à heures (taille ISO) |
| **Upload / traitement WinPE** | Fiche Windows → installs WIM | Long |
| **Régénération menus** | Menus, Paramètres (URL/logo), Boot files, etc. | Secondes à minutes |
| **Régénération scripts WinPE** | Fiche Windows | Minutes |
| **Compilation firmware** | Page Firmware | 15–45 min |
| **Injection Proxmox autoinstall** | Fiche version Proxmox (si utilisé) | Variable |

Les libellés exacts dépendent de la langue de l’interface.

---

## Extraction d’ISO

1. Sur `/isos/{id}`, lancer **Extraire** (ou upload avec extraction auto).
2. La fiche affiche un **statut** (en attente, en cours, terminé, erreur) — souvent mis à jour par fragment HTML ou polling.
3. Le tableau de bord liste le job avec le nom de fichier.

**Arrêter** : tente d’interrompre le worker ; l’ISO peut rester partiellement extraite — vérifier l’état sur la fiche.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-iso-extract-status-progress.png`
>
> **Description de la photo :** Fiche version ISO : bandeau ou carte statut « Extraction en cours » avec pourcentage ou spinner.
>
> **Éléments à cadrer :** Libellé statut, barre de progression si présente.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-iso-extract-complete.png`
>
> **Description de la photo :** Même fiche après succès : statut « Prêt » / fichiers boot listés.
>
> **Éléments à cadrer :** Badge vert prêt, liste fichiers boot non vide.

---

## Régénération des menus iPXE

Déclenchée automatiquement ou manuellement quand la structure des entrées de boot change :

- Bouton **Régénérer tous les menus** (`/ipxe-menus`)
- Changement **URL serveur**, **logo menu**, ordre types d’OS, boot files, etc.

Pendant la tâche : le site reste utilisable ; les clients PXE peuvent voir d’anciens menus jusqu’à la fin.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-menus-regenerate-click.png`
>
> **Description de la photo :** Page Menus : bouton vert « Régénérer tous les menus » entouré ou surligné.
>
> **Éléments à cadrer :** Bouton + message flash après clic (« régénération lancée »).

---

## Compilation firmware

- Page **Firmware** → **Compiler**
- Progression : étapes (clone, compile BIOS/EFI, copie TFTP) + logs
- **Annuler** : modale danger — les sources git restent pour accélérer la prochaine compile

Détail : [09-firmware-ipxe.md](09-firmware-ipxe.md).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-firmware-celery-progress-card.png`
>
> **Description de la photo :** Carte compilation avec badges d’étapes et zone log défilante.
>
> **Éléments à cadrer :** Étape courante en surbrillance, bouton Annuler désactivé ou actif selon l’état.

---

## Arrêter les jobs

| Action | Portée |
|--------|--------|
| **Arrêter** (ligne) | Un upload / job identifié |
| **Tout arrêter** | Tous les jobs suivis actifs |

Les deux demandent une **confirmation** (variante danger).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/14-kill-job-confirm.png`
>
> **Description de la photo :** Modale confirmation « Arrêter ce job ? » depuis le tableau de bord.
>
> **Éléments à cadrer :** Nom du fichier dans le message, bouton Confirmer rouge.

---

## Que faire si un job reste bloqué ?

1. Actualiser le **Tableau de bord** — la durée augmente-t-elle ?
2. Ouvrir la **fiche ISO** ou **Firmware** pour le message d’erreur.
3. **Supervision** → Santé (services Celery / Redis si affichés).
4. Admin : **Arrêter** le job puis relancer l’action.
5. En dernier recours : **Supervision → Relancer les services** (interruption courte du site).

Dépannage UI : [16-depannage-interface.md](16-depannage-interface.md).

---

## Relation avec le parcours PXE

Les tâches **préparent** le contenu HTTP/TFTP ; elles ne remplacent pas la configuration **DHCP** sur votre routeur/pare-feu.

Ordre recommandé après gros changement :

1. Attendre fin extraction / compile
2. **Régénérer les menus**
3. Tester un client PXE

Voir [15-parcours-boot-pxe.md](15-parcours-boot-pxe.md).

---

## Voir aussi

- [03-tableau-de-bord.md](03-tableau-de-bord.md)
- [05-isos-fiche-version.md](05-isos-fiche-version.md)
- [00-introduction-et-concepts.md](00-introduction-et-concepts.md)
