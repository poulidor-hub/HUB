# Matrice de compatibilité — POULIDOR

Compatibilité entre les versions des composants de l'écosystème POULIDOR.

---

## Versions actuelles (17 septembre 2026)

| Composant | Version | Dépôt |
|---|---|---|
| HUB_base | 3.59.3 | [HUB_base](https://github.com/POULIDOR-Audit-Developpement/HUB_base) |
| HUBsocle | 2.25.1 | [HUBsocle](https://github.com/POULIDOR-Audit-Developpement/HUBsocle) |
| HUBapp_TEMPLATE_base | 2.24.2 | [HUBapp_TEMPLATE_base](https://github.com/POULIDOR-Audit-Developpement/HUBapp_TEMPLATE_base) |
| Hubctl | 0.9.1 | [Hubctl](https://github.com/POULIDOR-Audit-Developpement/Hubctl) |
| HUBCSS | 0.2.4 | [HUBCSS](https://github.com/POULIDOR-Audit-Developpement/HUBCSS) |
| hub-mcp | 0.4.1 | [hub-mcp](https://github.com/POULIDOR-Audit-Developpement/hub-mcp) |

> Instantané à la date du titre — faire `git submodule status` depuis le méta-repo
> pour connaître l'état réel à un instant donné.

---

## Compatibilité app ↔ Hub (contrat actuel)

| Couple | État | Détail |
|---|---|---|
| Template **2.24.x** ↔ Hub **3.59.x** | ✅ Flotte en production | CI quotidienne : ~1050 tests Hub, suite pytest template, `test_flux_complet.sh` |
| Socle **2.25.x** ↔ Hub **3.59.x** | ✅ | Le Hub vérifie la compatibilité app↔socle (`APP_SOCLE_VERSION`, endpoint socle-check) |
| HUBCSS **0.2.x** ↔ Hub **3.59.x** | ✅ | Bundle runtime servi par le Hub (`/api/design-system/manifest`), seed fallback dans les apps |
| hub-mcp **0.4.x** ↔ Template **2.24.x** | ✅ | Copie synchronisée via `make sync-hub-tooling` ; délègue 100 % au `hub_client` de l'app |
| Hubctl **0.9.x** ↔ Hub **< 3.59.0** | ⚠️ | La commande `jwt` (trousseau multi-kid) requiert **HUB_base ≥ 3.59.0** ; le reste du CLI fonctionne sur des Hub plus anciens |
| Apps externes ↔ Hub **3.59.x** (OIDC) | ✅ | Provider OIDC : discovery, codes d'autorisation one-shot 384 bits (TTL 30 s), PKCE, back-channel logout |

**Contrat JWT :** RS256, JWKS public sur `/api/auth/.well-known/jwks.json`. Les apps
récupèrent le JWKS dynamiquement — la **rotation multi-clés** (Hub 3.59.0+) est
sans rupture ni action côté apps (chevauchement configurable : `JWT_KEY_OVERLAP_HOURS`,
défaut 24 h).

---

## Règles de breaking change

Un **bump de version majeure** est requis quand :

| Composant | Breaking change |
|---|---|
| **Hub API** | Changement de route, schéma JWT, flow SSO, format de backup |
| **Template** | Changement de `hub_client.py`, `hub_auth.py`, structure `.env` |
| **HUBCSS** | Changement de classe CSS, breakpoint, convention de nommage |
| **Socle** | Changement de l'API `create_app()`, des exports npm, des images de base |

---

## Historique des compatibilités critiques

| Date | Hub | Autre | Changement |
|---|---|---|---|
| 2026-09 | 3.59.x | Socle 2.25.x | Socle versionné en production (`APP_SOCLE_VERSION`, drift surveillé par `hubctl socle status`) |
| 2026-09 | 3.59.0 | Apps : aucun changement | Rotation multi-kid du JWKS sans rupture (overlap 24 h) + découplage du chiffrement des snapshots |
| 2026-06 | 3.7.x | Template 2.4.x | Ajout `check_backup_entity_access()`, rate limiting, beacon_token |
| 2026-05 | 3.6.x | Template 2.3.x | Audit sécurité — migration JWT, PKI, secrets Fernet |

---

## Procédure de test de compatibilité

```bash
# Depuis la racine du méta-repo POULIDOR :
./scripts/test_flux_complet.sh
```

Ce script :
1. Lance un Hub isolé (Docker Compose)
2. Crée une app depuis le template
3. Vérifie l'auto-registration
4. Teste le flux SSO complet
5. Vérifie la communication M2M
6. Nettoie les conteneurs

À exécuter après chaque release du Hub ou du Template. En complément :
`make test` dans chaque dépôt (Hub : ~1050 tests ; template et socle : suites pytest dédiées).

---

## Notes de migration

### Rotation des clés JWT (Hub ≥ 3.59.0)
- Aucune action applicative : les apps fetchent le JWKS public à la volée.
- L'ancienne clé reste acceptée pendant `JWT_KEY_OVERLAP_HOURS` (défaut 24 h,
  configurable dans `.env` — voir `.env.example`).
- Révocation anticipée possible : `./hubctl jwt revoke` (ou UI admin).

### Adoption du socle (apps existantes)
- Les apps forkées du template continuent de fonctionner telles quelles.
- Le passage « app fine » (INITIALISE v2) se prépare via HUBsocle — voir
  `HUBsocle/docs/REFERENCE-APP-FINE.md` et `./hubctl socle status` pour l'état de la flotte.
