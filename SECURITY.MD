# Politique de sécurité (Security Policy)

## Statut du projet

**iPXE Manager** est en version **beta**. Il est destiné aux **tests**, démonstrations et labos PXE sur un **réseau de confiance** — pas à un déploiement production critique sans audit et durcissement supplémentaires.

**Aucune version publiée ne doit être considérée comme « sécurisée par défaut »** pour une exposition Internet ou des données sensibles.

## Ce qui est prévu aujourd’hui

| Élément | Détail |
|--------|--------|
| **HTTPS (optionnel)** | Certificat **auto-signé** via `deploy/enable-https.sh` ; renouvellement depuis l’UI (**Paramètres**). Usage **interne / LAN** — pas une PKI publique. |
| **Clients iPXE** | Après activation ou renouvellement TLS : **recompiler le firmware** (CA embarquée dans le binaire). |
| **Authentification** | Comptes locaux (admin / utilisateur), sessions par cookie. |
| **Supervision** | Contrôles d’intégrité et état des services (admin) — diagnostic, pas un SOC. |

## Limites connues (non couvertes ou partielles)

- Mots de passe et secrets applicatifs **non chiffrés au repos** (SQLite, `.env`).
- Pas de **SSO**, **MFA**, rotation centralisée des secrets ni intégration Vault.
- HTTPS **auto-signé** : confiance manuelle (navigateur, firmware iPXE) — pas de Let’s Encrypt / ACME intégré.
- Pas de durcissement système complet (SELinux/AppArmor, fail2ban, WAF, etc.) dans les scripts d’install par défaut.
- Surface d’attaque large en labo : **TFTP**, **HTTP(S)**, **SMB**, **Redis**, uploads de grosses ISO — à isoler par VLAN / pare-feu.
- Code et dépendances **non audités** régulièrement par un tiers.

## Signalement des vulnérabilités

Si vous identifiez une faille (configuration, code, contournement d’auth, injection, etc.) :

1. **Ne l’exploitez pas** à des fins malveillantes sur des systèmes que vous ne contrôlez pas.
2. Ouvrez une **[Issue](https://github.com/RenaultLeo/ipxe-manager/issues)** sur ce dépôt en décrivant :
   - version / commit ou tag ;
   - contexte (HTTP vs HTTPS, rôle admin ou user) ;
   - étapes pour reproduire ;
   - impact estimé.
3. Pour une divulgation sensible, vous pouvez limiter les détails publics dans l’issue et proposer un contact via **GitHub** (discussions privées du compte mainteneur, si disponible).

Il n’y a **pas de programme de bug bounty** ni d’engagement de délai de correctif pour ce projet personnel / éducatif.

## Recommandations de déploiement

- Réseau **isolé** ou VLAN dédié au PXE / à l’admin.
- Changer le mot de passe **`admin`** immédiatement après install.
- Ne pas exposer l’UI sur Internet sans **TLS public**, pare-feu strict et revue de sécurité.
- Mettre à jour le dépôt via `deploy/update.sh` et suivre les notes de release.

Merci pour les retours responsables qui aident à améliorer le projet.
