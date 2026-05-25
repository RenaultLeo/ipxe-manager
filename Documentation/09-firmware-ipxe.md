# Firmware iPXE

**URL :** `/firmware`  
**Menu :** Firmware (administrateur)

Compilation des binaires iPXE depuis les sources officielles et copie vers **TFTP**.

---

## Objectif

Produire les fichiers que le **DHCP/TFTP** envoie au tout premier boot :

| Fichier | Usage |
|---------|--------|
| `undionly.kpxe` | PCs **BIOS** (Legacy) |
| `snponly.efi` | **UEFI** en VM (Proxmox, QEMU…) — pilotes réseau EFI |
| `ipxe.efi` | **UEFI** bare-metal (pilotes intégrés iPXE) |

---

## Cartes d’état

Trois cartes : présent / absent, taille en Ko, chemin sur disque.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-three-cards-present.png`
>
> **Description de la photo :** Trois cartes avec badge vert « Présent » et tailles en Ko.
>
> **Éléments à cadrer :** Badges VMs / bare-metal sur snponly et ipxe.efi, chemins en petit texte.

---

## Bouton Compiler

Lance une tâche **Celery** longue (clone git, patch, compile BIOS + EFI, copie TFTP).

Pendant la compilation :

- Bouton désactivé « Compilation… »
- Carte **progression** avec badges d’étapes (clone, pull, embed, patch, compile BIOS/EFI, copie)
- Logs défilants
- Bouton **Annuler** (confirmation) — les sources restent sur disque pour accélérer la prochaine compile

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-build-steps-green.png`
>
> **Description de la photo :** Compilation en cours : badges étapes (les premiers verts, étape courante en bleu/animé).
>
> **Éléments à cadrer :** Titre carte « Compilation en cours », rangée de 7 badges, zone logs.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-build-success.png`
>
> **Description de la photo :** Alerte verte succès + cartes firmware toutes « Présent ».
>
> **Éléments à cadrer :** Message succès avec chemin TFTP, badges verts sur toutes les étapes.

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-cancel-confirm.png`
>
> **Description de la photo :** Modale annulation compilation : avertissement que les sources restent sur disque.
>
> **Éléments à cadrer :** Texte modale, boutons Annuler compilation / Retour.

---

## Répertoires

Affichage :

- **TFTP** : racine tftpboot
- **Sources** : clone iPXE — cloné / non cloné

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-dirs-card.png`
>
> **Description de la photo :** Carte « Répertoires » avec chemins TFTP et sources + badge cloné.
>
> **Éléments à cadrer :** Chemins complets, badge état clone.

---

## Embed chainload (personnaliser)

Section **script intégré** dans le firmware :

- URL **menu.ipxe** utilisée au build (souvent dérivée de `SERVER_BASE_URL`)
- Bouton **Personnaliser** : champ URL chainload, aperçu du `#!ipxe` généré (dhcp + chain)
- **Compiler avec cette URL**

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-embed-customize.png`
>
> **Description de la photo :** Formulaire embed ouvert : champ URL, aperçu code embed.ipxe, bouton compiler avec URL.
>
> **Éléments à cadrer :** Champ URL, bloc code généré, bouton bleu/primary compiler.

---

## HTTPS

Si le site est en HTTPS :

- Bandeau rappel de recompiler après changement de certificat / activation HTTPS
- Nécessité d’embarquer la CA dans le firmware (`CERT/TRUST`)

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-https-banner.png`
>
> **Description de la photo :** Alerte bleue en haut page Firmware mentionnant HTTPS et recompilation.
>
> **Éléments à cadrer :** Texte HTTPS actif, chemin ca.crt si affiché.

---

## Aide DHCP

Carte en bas : exemple de configuration **DHCP** (pfSense / options) :

- next-server, fichier boot BIOS vs UEFI VM
- user-class **iPXE** pour servir l’URL HTTP du menu directement

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/09-firmware-dhcp-help-card.png`
>
> **Description de la photo :** Carte configuration DHCP avec extrait de config et flux DHCP → TFTP → HTTP.
>
> **Éléments à cadrer :** Bloc code DHCP, texte explicatif snponly vs ipxe.efi.

---

## Après renouvellement certificat TLS

Workflow recommandé :

1. **Paramètres** → Renouveler certificat (2 ans)
2. **Firmware** → Recompiler (embed HTTPS)
3. Tester un client PXE

Voir [10-parametres.md](10-parametres.md).

---

## Voir aussi

- [15-parcours-boot-pxe.md](15-parcours-boot-pxe.md)
- [14-taches-arriere-plan.md](14-taches-arriere-plan.md)
