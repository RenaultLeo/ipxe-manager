# Introduction et concepts

## À quoi sert iPXE Manager ?

iPXE Manager est une **interface web** pour piloter un petit datacenter **PXE** depuis une machine Debian :

- centraliser les **ISOs** et les **fichiers de boot** (noyau, initrd, boot.wim, etc.) ;
- générer et maintenir les **menus iPXE** ;
- gérer les **configs d’installation automatique** (preseed, kickstart, cloud-init, answer.toml, etc.) ;
- **compiler** les firmwares iPXE (BIOS + UEFI) adaptés à votre URL de menu.

Les opérations longues (extraction d’ISO, compilation firmware, régénération massive de menus) s’exécutent en **arrière-plan** via **Celery** pour que le navigateur reste utilisable.

---

## Les deux étapes du boot réseau

| Étape | Protocole | Contenu typique |
|-------|-----------|-----------------|
| 1 — Démarrage PXE | **TFTP** (port 69) | Petit firmware : `undionly.kpxe` (BIOS) ou `snponly.efi` / `ipxe.efi` (UEFI) |
| 2 — Suite iPXE | **HTTP** | Menu `menu.ipxe`, gros fichiers sous `/boot/`, configs, ISOs servies en HTTP |

Le firmware TFTP est **petit** ; une fois iPXE lancé, tout le reste passe par **HTTP** (plus simple à mettre à jour).

> ### 📷 Emplacement capture
> **Fichier suggéré :** `Documentation/images/00-architecture-schema.png`
>
> **Description de la photo :** Schéma dessiné (Draw.io, Excalidraw ou PowerPoint) montrant : PC client → DHCP → TFTP (firmware) → HTTP (menu + fichiers). Pas une capture d’écran obligatoire — un schéma clair suffit.
>
> **Éléments à cadrer :** Flèches « DHCP », « TFTP », « HTTP », IP du serveur iPXE Manager.

---

## Ce que vous faites dans l’interface (ordre logique)

1. **Paramètres** — URL HTTP publique, mot de passe admin, éventuellement certificat HTTPS.
2. **Firmware** — Compiler et copier les binaires dans TFTP.
3. **ISOs** — Ajouter des versions, extraire ou uploader les fichiers de boot.
4. **Configs auto** — Lier preseed / cloud-init / kickstart à une version.
5. **Menus iPXE** — Régénérer tous les menus après chaque changement important.
6. **DHCP** (hors UI) — Pointer les clients vers TFTP + URL du menu.

Détail du parcours PXE : [15-parcours-boot-pxe.md](15-parcours-boot-pxe.md).

---

## Langues de l’interface

L’UI est disponible en **FR, EN, DE, ES, IT, PT** (sélecteur en haut à droite). Voir [13-dialogues-et-confirmations.md](13-dialogues-et-confirmations.md) et [01-connexion-et-navigation.md](01-connexion-et-navigation.md).

---

## Suite de la lecture

- Première connexion : [01-connexion-et-navigation.md](01-connexion-et-navigation.md)
- Droits selon le rôle : [02-roles-et-permissions.md](02-roles-et-permissions.md)
