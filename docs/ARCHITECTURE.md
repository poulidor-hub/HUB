# POULIDOR Hub — Architecture

> ⚠️ **À mettre à jour** lorsque la branche `prod-ready` sera créée et que toutes les anciennes applications seront migrées. Ce document reflète l'état actuel (pré-prod-ready).

---

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────┐
│                  SERVEUR CLIENT (LAN)                      │
│                                                            │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐            │
│  │  Nginx   │◄───│ Hub API  │◄───│PostgreSQL│            │
│  │Frontend :80│   │  :8000   │    │   :5432  │            │
│  └────┬─────┘    └────┬─────┘    └──────────┘            │
│       │               │                                    │
│       │         ┌─────┴──────┐                             │
│       │         │ Docker     │                             │
│       │         │ Socket (ro)│                             │
│       │         └────────────┘                             │
│       │                                                    │
│  ┌────┴─────────────────────────────────────┐             │
│  │           Applications                    │             │
│  │  ┌─────────┐  ┌─────────┐  ┌──────────┐  │             │
│  │  │App A    │  │App B    │  │App C     │  │             │
│  │  │:42xx    │  │:42xx    │  │:42xx     │  │             │
│  │  │API :9xxx│  │API :9xxx│  │API :9xxx │  │             │
│  │  └─────────┘  └─────────┘  └──────────┘  │             │
│  └──────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────┘
                          │
                    ┌─────┴─────┐
                    │ WireGuard │  ← VPN site-à-site
                    │  Client   │     (optionnel)
                    └───────────┘
```

## Composants

### 1. Hub API (`hub-api:8000`)

FastAPI + SQLAlchemy async + PostgreSQL.

| Responsabilité | Détail |
|---|---|
| **Authentification** | JWT RS256 signé, JWKS public, refresh tokens hashés, `jti` révocable |
| **Registre d'apps** | Auto-enregistrement M2M, approbation admin, allocation de ports |
| **Audit** | Logs d'accès, sessions, erreurs HTTP, KPIs, alertes |
| **Messages** | Système de messagerie interne (broadcast, user, app), boîte de réception |
| **Backups** | Réception, stockage, planification cron, export chiffré, snapshots |
| **PKI** | Génération de certificats auto-signés pour HTTPS |
| **VPN** | Configuration WireGuard client intégré |

**Fichiers clés :**
- `hub/app/main.py` — Point d'entrée FastAPI, lifespan, middlewares
- `hub/app/config.py` — Configuration centralisée via `pydantic.BaseSettings`
- `hub/app/auth.py` — JWT, refresh, roles, `get_current_*`
- `hub/app/models.py` — ORM SQLAlchemy (User, Application, ServiceAccount, etc.)
- `hub/app/routes/` — Route handlers par domaine

### 2. Hub Frontend (Nginx + HTML statique)

Single Page Application vanilla JS servie par Nginx.

| Écran | Description |
|---|---|
| Login | Formulaire, demande de compte, mot de passe oublié |
| Dashboard | Grille d'apps, recherche, favoris, catégories |
| Audit | KPIs, alertes, logs, sessions, erreurs, exports |
| Users | CRUD, profils, demandes, opérations en masse |
| Admin | Apps, service accounts, backups, snapshots, VPN |
| Messages | 3 volets (sidebar/liste/détail), compose, preview |

**Fichiers clés :**
- `frontend/public/index.html` — Structure SPA
- `frontend/public/js/` — Modules JS par écran
- `frontend/public/css/style.css` — Styles Bootstrap 5 + custom

### 3. PostgreSQL (`db:5432`)

Base relationnelle avec migrations SQL manuelles (pas Alembic).

**Tables principales :** `users`, `applications`, `service_accounts`, `app_backups`, `audit_logs`, `messages`, `user_preferences`, `profiles`, `sessions`, `error_logs`

### 4. Applications (template)

Chaque app = projet Docker Compose indépendant (FastAPI + React + PostgreSQL).

**Cycle de vie d'une app :**
1. Création via `create_app.sh` depuis le template
2. Configuration `.env` (ports, Hub host, bootstrap token)
3. Démarrage Docker Compose
4. Auto-enregistrement auprès du Hub (M2M)
5. Approbation admin → statut `active`
6. Apparaît dans le dashboard du Hub

**Communication Hub ↔ App :**
- **M2M :** L'app obtient un token `client_credentials`, appelle les API du Hub
- **SSO :** Le Hub redirige le navigateur vers l'app avec un token JWT
- **SSE :** L'app se connecte au stream d'événements du Hub
- **Backups :** L'app pousse ses backups vers le Hub (`POST /api/apps/{slug}/backup`)

## Flux principaux

### Authentification utilisateur

```
Navigateur       Hub Frontend       Hub API
    │                 │                 │
    │  GET /          │                 │
    │────────────────►│                 │
    │                 │                 │
    │  Login form     │                 │
    │◄────────────────│                 │
    │                 │                 │
    │  POST /token    │                 │
    │─────────────────┼────────────────►│
    │                 │                 ├── Vérifier credentials
    │                 │                 ├── Générer access_token (RS256)
    │                 │                 ├── Générer refresh_token
    │                 │                 │
    │  JSON {access, refresh}          │
    │◄────────────────┼─────────────────│
    │                 │                 │
    │  localStorage   │                 │
```

### Lancement d'une application

```
Navigateur       Hub Frontend       Hub API         App (backend)
    │                 │                 │                 │
    │ Clic sur app    │                 │                 │
    │────────────────►│                 │                 │
    │                 │ GET /api/apps/{slug}/launch      │
    │                 │────────────────►│                 │
    │                 │                 ├── Générer token │
    │                 │   {redirect_url}│                 │
    │                 │◄────────────────│                 │
    │                 │                 │                 │
    │ Redirect 302 → app?token=xxx      │                 │
    │───────────────────────────────────────────────────►│
    │                 │                 │   Valide JWT    │
    │                 │                 │◄────────────────│
    │   App UI (authentifié)            │                 │
    │◄────────────────────────────────────────────────────│
```

### Backup (sync-to-hub)

```
App (backend)                     Hub API
    │                                │
    │ POST /api/apps/{slug}/backup   │
    │ M2M token (Authorization)      │
    │ ZIP file (multipart)           │
    │───────────────────────────────►│
    │                                ├── check_backup_entity_access()
    │                                ├── Valider checksum
    │                                ├── Stocker ZIP
    │                                ├── Enregistrer métadonnées
    │   {backup_id, status}          │
    │◄───────────────────────────────│
```

## Réseau

```
Internet ──► WireGuard VPN ──► LAN
                                  │
                         ┌────────┴────────┐
                         │   Hub Frontend  │  :80 / :443
                         │   Hub API       │  :8000 (interne)
                         │   PostgreSQL    │  :5432 (Docker interne)
                         │   Apps API      │  :9000-9499
                         │   Apps Frontend │  :4200-4699
                         └─────────────────┘
```

**Règles :**
- Rien n'est exposé sur Internet. Tout passe par le LAN ou le VPN.
- L'API Hub (`:8000`) n'est pas accessible aux navigateurs hors LAN.
- Les apps communiquent avec le Hub en HTTP à l'intérieur du réseau Docker.
- Le HTTPS est optionnel mais recommandé (PKI intégrée).

## Sécurité

| Couche | Mécanisme |
|---|---|
| Authentification | JWT RS256 + `jti` + refresh token rotation |
| M2M | `client_credentials` grant, secrets Fernet-encryptés |
| Rate limiting | 5 tentatives/min par IP sur `/token`, `/login`, `/sso` |
| Secrets | Fernet (`SECRETS_ENCRYPTION_KEY`), clés JWT hors Git |
| HTTPS | PKI auto-générée, certificats auto-signés |
| Headers | X-Frame-Options, X-Content-Type, Referrer-Policy, CSP |
| Réseau | Whitelist CIDR pour auto-registration, ports bindés au LAN |
| Backup | Export chiffré RSA+AES-GCM, accès contrôlé par slug |

Pour l'audit complet : voir `SECURITY_AUDIT.md`.

---

> **Dernière mise à jour :** 28 juin 2026 — reflète l'état pré-`prod-ready`.  
> Lorsque la branche `prod-ready` sera mergée, mettre à jour le flux SSO (suppression du token dans l'URL, remplacement par code one-shot + échange M2M).
