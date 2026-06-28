# Matrice de compatibilité — POULIDOR

Compatibilité entre les versions des composants de l'écosystème POULIDOR.

---

## Versions actuelles (28 juin 2026)

| Composant | Version |
|---|---|
| HUB_base | 3.7.1 |
| HUBapp_TEMPLATE_base | 2.4.0 |
| HUBCSS | 0.1.0 |
| hub-mcp | 0.1.0 |

---

## Matrice

| Hub \ Template | 2.3.x | 2.4.x | 2.5.x |
|---|---|---|---|
| **3.6.x** | ✅ | ⚠️ | ❌ |
| **3.7.x** | ⚠️ | ✅ | — |
| **3.8.x** | ❌ | ✅ | ✅ |

| Template \ HUBCSS | 0.1.x | 0.2.x |
|---|---|---|
| **2.3.x** | ✅ | ❌ |
| **2.4.x** | ✅ | ✅ |
| **2.5.x** | ⚠️ | ✅ |

**Légende :**
- ✅ Testé et compatible
- ⚠️ Fonctionne avec ajustements mineurs
- ❌ Incompatible
- — Non testé / future version

---

## Règles de breaking change

Un **bump de version majeure** est requis quand :

| Composant | Breaking change |
|---|---|
| **Hub API** | Changement de route, schéma JWT, flow SSO, format de backup |
| **Template** | Changement de `hub_client.py`, `hub_auth.py`, structure `.env` |
| **HUBCSS** | Changement de classe CSS, breakpoint, convention de nommage |

---

## Historique des compatibilités critiques

| Date | Hub | Template | Changement |
|---|---|---|---|
| 2026-06 | 3.7.x | 2.4.x | Ajout `check_backup_entity_access()`, rate limiting, beacon_token |
| 2026-05 | 3.6.x | 2.3.x | Audit sécurité — migration JWT, PKI, secrets Fernet |
| À venir | 3.8.x (prod-ready) | 2.5.x | Suppression token URL → flow SSO par code one-shot |

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

À exécuter après chaque release du Hub ou du Template.

---

## Notes de migration

### Template 2.3.x → 2.4.x
- `check_backup_entity_access()` requis côté Hub (v3.7+)
- `ACCESS_TOKEN_EXPIRE_MINUTES` passé à 30 (était 480)
- Rate limiting activé par défaut sur le Hub

### Préparation Template 2.4.x → 2.5.x (prod-ready)
- Le flux SSO changera : `?token=xxx` → `?code=xxx` + échange M2M
- Toutes les apps devront avoir un endpoint `POST /api/auth/exchange`
- Le Hub devra générer des codes one-shot (30s TTL)
- Impact : **toutes les apps existantes** doivent être mises à jour
