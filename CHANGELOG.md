# Changelog - Hub d'Authentification

> Ce fichier documente toutes les modifications apportées à la documentation et à l'API.
> Les agents IA peuvent utiliser `/api/docs/changelog` pour consulter cet historique.

---

## [2.3.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Support du header X-Slug
- Les endpoints `/api/apps/me/credentials` et `/api/apps/me/regenerate-secret` acceptent désormais `X-Slug` en plus de `X-Client-ID`
- Permet aux applications de s'identifier par leur slug au lieu de leur client_id
- Plus simple pour les applications qui ne connaissent pas encore leur client_id

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Version | 2.2.0 → 2.3.0 |
| Section 10 | Ajout sous-section "Headers d'identification" |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/apps.py` | Support X-Slug dans get_app_credentials |
| `hub/app/routes/apps.py` | Support X-Slug dans regenerate_app_secret |

### 📋 Checklist pour agents IA

- [ ] Possibilité d'utiliser `X-Slug: mon-app` au lieu de `X-Client-ID`
- [ ] Utile pour récupérer/régénérer credentials quand client_id inconnu

---

## [2.2.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Système de Changelog pour agents IA
- **Nouveau fichier** : `CHANGELOG.md` - Historique détaillé des modifications
- **Nouveau endpoint** : `GET /api/docs/changelog` - Consulter le changelog
- **Nouveau endpoint** : `GET /api/docs/changelog/versions` - Liste des versions
- **Nouveau endpoint** : `GET /api/docs/changelog/diff` - Comparer deux versions
- Support du format JSON pour parsing automatique

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Version | 2.1.0 → 2.2.0 |
| Section 10 | Ajout sous-section "Documentation et Changelog" |
| Table des matières | Ajout lien vers Documentation et Changelog |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/docs.py` | Ajout endpoints changelog (4 nouveaux) |
| `hub/app/routes/docs.py` | Fonctions parse_changelog_versions, extract_version_changes |

### 📋 Checklist pour agents IA

- [ ] Stocker la version actuelle pour comparaisons futures
- [ ] Utiliser `/api/docs/changelog/diff?from_version=X.Y.Z` pour voir les changements
- [ ] Vérifier `action_required` dans la réponse du diff
- [ ] Traiter les items de `checklist` dans la réponse

---

## [2.1.0] - 2 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Services Partagés M2M
- **Nouvelle section 7** : "Services Partagés (APIs de données)"
- Les applications peuvent accéder automatiquement aux APIs partagées (ex: API Salariés)
- Nouveau champ `is_shared_service` sur le modèle `Application`
- Attribution automatique des droits aux APIs partagées lors de l'auto-enregistrement

#### Documentation dynamique
- **Nouveau endpoint** : `GET /api/docs/integration/version` - Retourne la version actuelle
- **Nouveau endpoint** : `GET /api/docs/integration/sections` - Liste JSON des sections
- **Nouveau endpoint** : `GET /api/docs/changelog` - Historique des modifications
- Les sections sont maintenant parsées dynamiquement depuis `INTEGRATION.md`

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Table des matières | Ajout section 7, renumérotation 8-13 |
| Section 7 (nouvelle) | Services Partagés - concept, configuration, usage |
| Section 12 (Résumé) | Ajout checklist "APIs partagées accessibles automatiquement" |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/models.py` | Champ `is_shared_service` déjà présent (ligne 104) |
| `hub/app/routes/apps.py` | Auto-ajout apps partagées lors création SA |
| `hub/app/routes/service_accounts.py` | Auto-ajout apps partagées lors création SA |
| `hub/app/routes/docs.py` | Refonte complète - parsing dynamique |

### 🗄️ Modifications base de données

```sql
-- Nouveau champ (déjà migré)
ALTER TABLE applications ADD COLUMN is_shared_service BOOLEAN DEFAULT FALSE;

-- Marquer API Salariés comme partagée
UPDATE applications SET is_shared_service = TRUE WHERE slug = 'salarie';
```

### 📋 Checklist pour agents IA

- [ ] Mettre à jour la numérotation des sections (1-13 au lieu de 1-11)
- [ ] Ajouter la gestion de `is_shared_service` si manipulation d'apps
- [ ] Utiliser `/api/docs/integration/version` pour vérifier la version
- [ ] Les nouvelles apps ont automatiquement accès aux APIs partagées

---

## [2.0.0] - 1 janvier 2026

### ✨ Nouvelles fonctionnalités

#### Auto-enregistrement dynamique
- **Nouvelle section 3** : "Auto-enregistrement et récupération dynamique des credentials"
- Les applications peuvent s'enregistrer automatiquement auprès du Hub
- Credentials stockés en RAM (pas dans .env)
- Régénération du secret via `POST /api/apps/me/regenerate-secret`

#### Endpoints auto-enregistrement
- `POST /api/apps/register` - Auto-enregistrement (IP Docker uniquement)
- `GET /api/apps/me/credentials` - Récupérer client_id
- `POST /api/apps/me/regenerate-secret` - Régénérer le secret

### 📝 Modifications documentation

| Section | Modification |
|---------|--------------|
| Section 2 | `.env` simplifié - CLIENT_ID/SECRET optionnels |
| Section 3 (nouvelle) | Auto-enregistrement complet |
| Section 9 | Endpoints auto-enregistrement ajoutés |

### 🔧 Modifications code

| Fichier | Modification |
|---------|--------------|
| `hub/app/routes/apps.py` | Endpoints register, credentials, regenerate-secret |
| `hub/app/schemas.py` | `AppRegisterRequest`, `AppRegisterResponse` |

### 🔒 Sécurité

- Whitelist IP Docker pour auto-enregistrement
- Plages autorisées : `172.17-31.x.x`, `10.0.x.x`, `192.168.x.x`

---

## [1.0.0] - 15 décembre 2025

### 🎉 Version initiale

- Authentification centralisée JWT (RS256)
- Gestion des utilisateurs et rôles
- Service Accounts M2M
- Intégration Frontend/Backend
- Communication inter-API

---

## Format du changelog

Chaque entrée contient :

```markdown
## [VERSION] - DATE

### ✨ Nouvelles fonctionnalités
### 📝 Modifications documentation  
### 🔧 Modifications code
### 🗄️ Modifications base de données
### 🔒 Sécurité
### 🐛 Corrections
### ⚠️ Breaking changes
### 📋 Checklist pour agents IA
```

---

## Comment utiliser ce changelog

### Pour les agents IA

```bash
# Voir tout le changelog
curl http://hub:8000/api/docs/changelog

# Voir les changements depuis une version
curl "http://hub:8000/api/docs/changelog?since=2.0.0"

# Voir uniquement la dernière version
curl "http://hub:8000/api/docs/changelog?latest=true"

# Format JSON structuré
curl "http://hub:8000/api/docs/changelog?format=json"
```

### Comparaison de versions

```bash
# Quelle version ai-je ?
curl http://hub:8000/api/docs/integration/version

# Quels changements depuis ma version ?
curl "http://hub:8000/api/docs/changelog?since=2.0.0"
```
