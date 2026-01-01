# Guide d'Intégration - Hub d'Authentification

> **Version** : 1.1.0  
> **Dernière mise à jour** : 1er janvier 2026  
> **Auteur** : Équipe Hub

Ce document décrit comment intégrer vos applications avec le Hub d'authentification centralisé.

---

## 📑 Table des matières

### Concepts de base
1. [Principes fondamentaux](#1-principes-fondamentaux)
   - [Le Hub gère TOUTE l'authentification](#-le-hub-gère-toute-lauthentification)
   - [Adresses IP, pas localhost !](#-adresses-ip-pas-localhost-)
   - [Localisation du Hub](#-localisation-du-hub)

### Configuration
2. [Configuration de l'application](#2-configuration-de-lapplication)
   - [Fichier `.env`](#fichier-env)
   - [Fichier `config.py`](#fichier-configpy)

### Intégration Backend
3. [Intégration Backend (FastAPI)](#3-intégration-backend-fastapi)
   - [Étape 1 : Dépendances](#étape-1--dépendances)
   - [Étape 2 : Récupérer la clé publique du Hub](#étape-2--récupérer-la-clé-publique-du-hub)
   - [Étape 3 : Module d'authentification `hub_auth.py`](#étape-3--module-dauthentification-hub_authpy)
   - [Étape 4 : Utilisation dans les routes](#étape-4--utilisation-dans-les-routes)

### Intégration Frontend
4. [Intégration Frontend](#4-intégration-frontend)
   - [Principes](#principes)
   - [Paramètres URL du Hub](#paramètres-url-du-hub)
   - [Configuration JavaScript](#configuration-javascript)
   - [Module d'authentification](#module-dauthentification)
   - [Gestion des préférences UI locales](#gestion-des-préférences-ui-locales)

### Communication inter-services
5. [Communication Machine-to-Machine (M2M)](#5-communication-machine-to-machine-m2m)
   - [Création automatique du Service Account](#création-automatique-du-service-account)
   - [Exemple Python - Client M2M](#exemple-python---client-m2m)

6. [Communication inter-API](#6-communication-inter-api)
   - [Architecture](#architecture)
   - [Configurer l'IP du Hub](#configurer-lip-du-hub)
   - [Créer un Service Account](#créer-un-service-account)
   - [Client M2M complet (`hub_client.py`)](#client-m2m-complet-hub_clientpy)
   - [Checklist d'intégration inter-API](#checklist-dintégration-inter-api)

### Sécurité et rôles
7. [Gestion des rôles](#7-gestion-des-rôles)
   - [Structure du token JWT](#structure-du-token-jwt)
   - [Rôles disponibles](#rôles-disponibles)
   - [Vérification des rôles côté backend](#vérification-des-rôles-côté-backend)
   - [Vérification des rôles côté frontend](#vérification-des-rôles-côté-frontend)

### Référence API
8. [Endpoints du Hub](#8-endpoints-du-hub)
   - [Authentification](#authentification)
   - [Durée des tokens](#durée-des-tokens-géré-par-le-hub-uniquement)
   - [Format des réponses](#format-des-réponses)

### Déploiement
9. [Configuration du Dockerfile](#9-configuration-du-dockerfile)
   - [Fichiers requis](#fichiers-requis)
   - [Exemple de Dockerfile](#exemple-de-dockerfile)
   - [Avec Docker Compose (volumes)](#avec-docker-compose-volumes)

### Annexes
10. [Résumé](#10-résumé)
    - [Ce que l'application DOIT faire](#ce-que-lapplication-doit-faire)
    - [Ce que l'application NE DOIT PAS faire](#ce-que-lapplication-ne-doit-pas-faire)

11. [Contact Hub](#11-contact-hub)

---

## 1. Principes fondamentaux

### 🔐 Le Hub gère TOUTE l'authentification

| Responsabilité | Hub | Application |
|----------------|-----|-------------|
| Connexion utilisateur | ✅ | ❌ |
| Création des tokens | ✅ | ❌ |
| Renouvellement des tokens | ✅ | ❌ |
| Durée de vie des tokens | ✅ | ❌ |
| Révocation des tokens | ✅ | ❌ |
| Gestion des utilisateurs | ✅ | ❌ |
| Validation des tokens | ❌ | ✅ |
| Préférences UI utilisateur | ❌ | ✅ |
| Données métier | ❌ | ✅ |

### 🌐 Adresses IP, pas localhost !

> ⚠️ **CRITIQUE** : Les applications et le Hub doivent utiliser des **adresses IP** (ou noms DNS), **JAMAIS \`localhost\`**.
> 
> Quand un utilisateur est redirigé vers votre application depuis le Hub, il ne peut pas accéder à \`localhost\` car cela pointe vers sa propre machine !

**❌ INCORRECT :**
\`\`\`
http://localhost:8000/api/auth/token
http://localhost:4178/callback
\`\`\`

**✅ CORRECT :**
\`\`\`
http://192.0.2.12:8000/api/auth/token
http://192.0.2.12:4178/callback
\`\`\`

### 📍 Localisation du Hub

Par défaut, le Hub est accessible sur **la même IP que votre application**. Si le Hub est sur une machine différente, configurez l'IP dans le fichier `.env` de votre application.

---

## 2. Configuration de l'application

### Fichier `.env`

Créez un fichier \`.env\` à la racine de votre application :

\`\`\`env
# Adresse du Hub d'authentification
# Par défaut: même IP que l'application
# Décommentez et modifiez si le Hub est sur une autre machine
# HUB_HOST=192.168.1.100

# Port de l'API Hub (par défaut: 8000)
HUB_API_PORT=8000

# Port du Frontend Hub (par défaut: 80)
HUB_FRONTEND_PORT=80

# Slug de votre application (défini dans le Hub)
APP_SLUG=mon-app

# Port de votre API
API_PORT=18087

# Port de votre frontend (optionnel)
FRONTEND_PORT=4178
\`\`\`

### Fichier \`config.py\`

\`\`\`python
"""
Configuration de l'application avec détection automatique de l'IP
"""
import os
import socket
from functools import lru_cache
from pydantic_settings import BaseSettings


def get_server_ip() -> str:
    """Détecte automatiquement l'IP du serveur (jamais localhost)"""
    try:
        # Crée une connexion UDP factice pour obtenir l'IP locale
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback : essayer de récupérer l'IP depuis les interfaces
        import subprocess
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
            ip = result.stdout.strip().split()[0]
            return ip
        except Exception:
            raise RuntimeError("Impossible de détecter l'IP. Définissez HUB_HOST dans .env")


class Settings(BaseSettings):
    # Hub - utilise l'IP locale par défaut
    hub_host: str = ""  # Sera rempli automatiquement si vide
    hub_api_port: int = 8000
    hub_frontend_port: int = 80
    
    # Application
    app_slug: str = "mon-app"
    api_port: int = 18087
    frontend_port: int = 4178
    
    class Config:
        env_file = ".env"
        extra = "ignore"
    
    @property
    def hub_ip(self) -> str:
        """Retourne l'IP du Hub (configurée ou détectée automatiquement)"""
        if self.hub_host:
            return self.hub_host
        return get_server_ip()
    
    @property
    def hub_api_url(self) -> str:
        """URL de l'API du Hub"""
        return f"http://{self.hub_ip}:{self.hub_api_port}"
    
    @property
    def hub_frontend_url(self) -> str:
        """URL du frontend du Hub"""
        return f"http://{self.hub_ip}:{self.hub_frontend_port}"
    
    @property
    def app_api_url(self) -> str:
        """URL de l'API de cette application"""
        return f"http://{self.hub_ip}:{self.api_port}"
    
    @property
    def app_frontend_url(self) -> str:
        """URL du frontend de cette application"""
        return f"http://{self.hub_ip}:{self.frontend_port}"
    
    @property
    def app_callback_url(self) -> str:
        """URL de callback pour le SSO"""
        return f"{self.app_frontend_url}/callback"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
\`\`\`

---

## 3. Intégration Backend (FastAPI)

### Étape 1 : Dépendances

Ajouter au \`requirements.txt\` :

\`\`\`
pyjwt[crypto]>=2.8.0
cryptography>=42.0.0
httpx>=0.25.0
\`\`\`

### Étape 2 : Récupérer la clé publique du Hub

La clé publique peut être :
- Copiée manuellement dans un fichier \`jwt_public_key.pem\`
- Récupérée dynamiquement depuis le Hub

**Option A : Fichier statique** (recommandé en production)

Créez \`jwt_public_key.pem\` en récupérant la clé depuis :
\`\`\`
GET http://[IP_HUB]:8000/api/auth/public-key
\`\`\`

**Option B : Récupération dynamique** (pour développement)

\`\`\`python
import httpx
from config import get_settings

async def fetch_public_key() -> str:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{settings.hub_api_url}/api/auth/public-key")
        data = response.json()
        return data["public_key"]
\`\`\`

### Étape 3 : Module d'authentification \`hub_auth.py\`

\`\`\`python
"""
Module d'authentification Hub
=============================
Valide les tokens JWT émis par le Hub central.

⚠️ IMPORTANT :
- Ne JAMAIS créer de tokens dans l'application
- Ne JAMAIS gérer le renouvellement des tokens
- Seul le Hub gère le cycle de vie des tokens
"""

import jwt
from pathlib import Path
from typing import Optional, Dict
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from config import get_settings

settings = get_settings()

# Configuration
JWT_ALGORITHM = "RS256"
JWT_PUBLIC_KEY_PATH = "jwt_public_key.pem"

# Charger la clé publique
def load_public_key() -> str:
    """Charge la clé publique RSA pour vérifier les tokens du Hub."""
    key_path = Path(JWT_PUBLIC_KEY_PATH)
    if not key_path.exists():
        key_path = Path("secrets/jwt_public_key.pem")
    if not key_path.exists():
        raise FileNotFoundError(
            f"Clé publique JWT non trouvée. "
            f"Récupérez-la depuis {settings.hub_api_url}/api/auth/public-key"
        )
    return key_path.read_text().strip()

PUBLIC_KEY = load_public_key()

# OAuth2 scheme - pointe vers le Hub (pas localhost !)
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.hub_api_url}/api/auth/token",
    auto_error=True
)


class TokenPayload:
    """Représente le contenu d'un token JWT validé."""
    
    def __init__(self, payload: dict):
        self.sub: str = payload.get("sub", "")           # Username
        self.roles: list = payload.get("roles", [])       # ["admin"] ou ["user"]
        self.apps: dict = payload.get("apps", {})         # {"app-slug": "role"}
        self.exp: int = payload.get("exp", 0)             # Expiration
        self.token_type: str = payload.get("type", "user")  # "user" ou "service_account"
        self.raw: dict = payload
    
    def get_role_for_app(self, app_slug: str) -> Optional[str]:
        """Retourne le rôle de l'utilisateur pour une application donnée"""
        return self.apps.get(app_slug)
    
    def has_app_access(self, app_slug: str) -> bool:
        """Vérifie si l'utilisateur a accès à une application"""
        return app_slug in self.apps
    
    def is_admin(self) -> bool:
        """Vérifie si l'utilisateur est admin du Hub"""
        return "admin" in self.roles
    
    def is_service_account(self) -> bool:
        """Vérifie si c'est un service account (M2M)"""
        return self.token_type == "service_account"


def verify_hub_token(token: str) -> TokenPayload:
    """
    Vérifie un token JWT émis par le Hub.
    
    ⚠️ Cette fonction ne gère PAS l'expiration/renouvellement !
    Si le token est expiré, l'utilisateur doit être redirigé vers le Hub.
    """
    try:
        payload = jwt.decode(
            token,
            PUBLIC_KEY,
            algorithms=[JWT_ALGORITHM]
        )
        return TokenPayload(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expiré - Reconnectez-vous via le Hub",
            headers={
                "WWW-Authenticate": "Bearer",
                "X-Hub-Login-URL": settings.hub_frontend_url
            }
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalide: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"}
        )


async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    """Dépendance pour récupérer l'utilisateur courant"""
    return verify_hub_token(token)


async def get_current_admin(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """Dépendance pour vérifier que l'utilisateur est admin du Hub"""
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    return user


def require_app_access(app_slug: str, min_role: str = None):
    """
    Crée une dépendance pour vérifier l'accès à une application.
    
    Args:
        app_slug: Slug de l'application
        min_role: Rôle minimum requis (optionnel)
    
    Usage:
        @app.get("/data")
        async def get_data(user: TokenPayload = Depends(require_app_access("mon-app"))):
            ...
        
        @app.delete("/data/{id}")
        async def delete_data(id: int, user: TokenPayload = Depends(require_app_access("mon-app", "admin"))):
            ...
    """
    # Hiérarchie des rôles
    ROLE_HIERARCHY = {"admin": 4, "manager": 3, "user": 2, "viewer": 1, "service": 3}
    
    async def check_access(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        # Les admins du Hub ont accès à tout
        if user.is_admin():
            return user
        
        # Vérifier l'accès à l'app
        if not user.has_app_access(app_slug):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès non autorisé à l'application '{app_slug}'"
            )
        
        # Vérifier le rôle minimum si spécifié
        if min_role:
            user_role = user.get_role_for_app(app_slug)
            user_level = ROLE_HIERARCHY.get(user_role, 0)
            required_level = ROLE_HIERARCHY.get(min_role, 0)
            
            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôle '{min_role}' requis (vous avez '{user_role}')"
                )
        
        return user
    
    return check_access
\`\`\`

### Étape 4 : Utilisation dans les routes

\`\`\`python
from fastapi import FastAPI, Depends
from hub_auth import (
    get_current_user, 
    get_current_admin, 
    require_app_access, 
    TokenPayload
)
from config import get_settings

settings = get_settings()
app = FastAPI()

# Route protégée - tout utilisateur authentifié
@app.get("/api/profile")
async def get_profile(user: TokenPayload = Depends(get_current_user)):
    role = user.get_role_for_app(settings.app_slug)
    return {
        "username": user.sub,
        "role": role,
        "is_service": user.is_service_account()
    }

# Route avec vérification d'accès à cette app
@app.get("/api/employees")
async def list_employees(user: TokenPayload = Depends(require_app_access("salarie"))):
    return {"employees": [...]}

# Route admin de l'app uniquement
@app.delete("/api/employees/{id}")
async def delete_employee(
    id: int, 
    user: TokenPayload = Depends(require_app_access("salarie", "admin"))
):
    return {"deleted": id}

# Route manager ou admin
@app.post("/api/employees")
async def create_employee(
    data: dict,
    user: TokenPayload = Depends(require_app_access("salarie", "manager"))
):
    return {"created": data}
\`\`\`

---

## 4. Intégration Frontend

### Principes

1. **Connexion** : Rediriger vers le Hub
2. **Callback** : Récupérer le token depuis l'URL
3. **Stockage** : Garder le token en localStorage
4. **Requêtes** : Envoyer le token dans le header \`Authorization\`
5. **Expiration** : Rediriger vers le Hub si 401
6. **Déconnexion** : Rediriger vers le Hub avec \`action=logout\`
7. **Préférences** : L'app peut stocker des préférences UI localement

### Paramètres URL du Hub

Le Hub accepte plusieurs paramètres dans l'URL pour contrôler le comportement de l'authentification :

| Paramètre | Description | Exemple |
|-----------|-------------|---------|
| \`callback\` | URL de retour après connexion réussie | \`?callback=http://app:4178/callback\` |
| \`action\` | Action spéciale à effectuer | \`?action=logout\` |

#### ⚠️ Éviter la reconnexion automatique après déconnexion

Quand un utilisateur se déconnecte de votre application, **n'utilisez PAS** \`callback\` pour rediriger vers le Hub, sinon le Hub le reconnectera automatiquement s'il a une session active.

**❌ INCORRECT** (cause une boucle de reconnexion) :
\`\`\`javascript
// L'utilisateur reste connecté au Hub → reconnexion automatique !
window.location.href = \`\${HUB_URL}?callback=\${encodeURIComponent(callbackUrl)}\`;
\`\`\`

**✅ CORRECT** (affiche le portail sans reconnexion) :
\`\`\`javascript
// action=logout indique au Hub de ne PAS rediriger automatiquement
window.location.href = \`\${HUB_URL}?action=logout\`;
\`\`\`

Le paramètre \`action=logout\` :
- Efface tout callback en attente
- Affiche le portail Hub (dashboard si connecté, login sinon)
- **Ne redirige PAS automatiquement** vers une application

### Configuration JavaScript

\`\`\`javascript
// config.js
const CONFIG = {
    // Détection automatique de l'IP (jamais localhost pour la prod)
    get serverIP() {
        // En développement, vous pouvez forcer une IP
        // return '192.0.2.12';
        return window.location.hostname;
    },
    
    // URLs du Hub
    get HUB_API_URL() {
        const hubHost = localStorage.getItem('hub_host') || this.serverIP;
        return \`http://\${hubHost}:8000\`;
    },
    
    get HUB_FRONTEND_URL() {
        const hubHost = localStorage.getItem('hub_host') || this.serverIP;
        return \`http://\${hubHost}:80\`;
    },
    
    // URL de votre API
    get API_URL() {
        return \`http://\${this.serverIP}:18087\`;
    },
    
    // Slug de l'application
    APP_SLUG: 'salarie'
};
\`\`\`

### Module d'authentification

\`\`\`javascript
// auth.js
class HubAuth {
    constructor() {
        this.token = localStorage.getItem('hub_token');
        this.refreshToken = localStorage.getItem('hub_refresh_token');
    }
    
    /**
     * Redirige vers le Hub pour la connexion
     */
    login() {
        // Sauvegarder l'URL actuelle pour y revenir après
        localStorage.setItem('redirect_after_login', window.location.href);
        
        // Rediriger vers le Hub avec l'URL de callback
        const callbackUrl = \`\${window.location.origin}/callback\`;
        window.location.href = \`\${CONFIG.HUB_FRONTEND_URL}?callback=\${encodeURIComponent(callbackUrl)}\`;
    }
    
    /**
     * Traite le callback après connexion sur le Hub
     */
    handleCallback() {
        const params = new URLSearchParams(window.location.search);
        const accessToken = params.get('access_token');
        const refreshToken = params.get('refresh_token');
        
        if (accessToken) {
            this.setTokens(accessToken, refreshToken);
            
            // Nettoyer l'URL
            window.history.replaceState({}, '', window.location.pathname);
            
            // Rediriger vers la page d'origine
            const redirect = localStorage.getItem('redirect_after_login') || '/';
            localStorage.removeItem('redirect_after_login');
            window.location.href = redirect;
            
            return true;
        }
        return false;
    }
    
    /**
     * Stocke les tokens
     */
    setTokens(accessToken, refreshToken = null) {
        this.token = accessToken;
        localStorage.setItem('hub_token', accessToken);
        
        if (refreshToken) {
            this.refreshToken = refreshToken;
            localStorage.setItem('hub_refresh_token', refreshToken);
        }
    }
    
    /**
     * Tente de rafraîchir le token via le Hub
     * ⚠️ Le renouvellement est géré par le HUB, pas par l'application !
     */
    async refreshAccessToken() {
        if (!this.refreshToken) {
            throw new Error('Pas de refresh token');
        }
        
        try {
            const response = await fetch(\`\${CONFIG.HUB_API_URL}/api/auth/token\`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: new URLSearchParams({
                    grant_type: 'refresh_token',
                    refresh_token: this.refreshToken
                })
            });
            
            if (!response.ok) {
                throw new Error('Refresh failed');
            }
            
            const data = await response.json();
            this.setTokens(data.access_token, data.refresh_token);
            return data.access_token;
            
        } catch (error) {
            // Refresh échoué - déconnecter et rediriger vers le Hub
            this.logout();
            throw error;
        }
    }
    
    /**
     * Effectue une requête API avec gestion automatique du token
     */
    async request(url, options = {}) {
        if (!this.token) {
            this.login();
            return;
        }
        
        // Première tentative
        let response = await fetch(url, {
            ...options,
            headers: {
                ...options.headers,
                'Authorization': \`Bearer \${this.token}\`
            }
        });
        
        // Si 401 et qu'on a un refresh token, tenter le refresh
        if (response.status === 401 && this.refreshToken) {
            try {
                await this.refreshAccessToken();
                
                // Réessayer avec le nouveau token
                response = await fetch(url, {
                    ...options,
                    headers: {
                        ...options.headers,
                        'Authorization': \`Bearer \${this.token}\`
                    }
                });
            } catch (e) {
                // Refresh échoué, l'utilisateur a été déconnecté
                return;
            }
        }
        
        // Si toujours 401, rediriger vers le Hub
        if (response.status === 401) {
            this.login();
            return;
        }
        
        return response;
    }
    
    /**
     * Déconnexion - révoque le token sur le Hub
     * 
     * ⚠️ IMPORTANT : Utiliser `action=logout` pour éviter la reconnexion automatique !
     * Si l'utilisateur est connecté au Hub et qu'on redirige avec un `callback`,
     * le Hub le reconnectera automatiquement à l'application.
     */
    async logout() {
        if (this.token) {
            try {
                await fetch(\`\${CONFIG.HUB_API_URL}/api/auth/logout\`, {
                    method: 'POST',
                    headers: { 'Authorization': \`Bearer \${this.token}\` }
                });
            } catch (e) {
                // Ignorer les erreurs de logout
            }
        }
        
        this.token = null;
        this.refreshToken = null;
        localStorage.removeItem('hub_token');
        localStorage.removeItem('hub_refresh_token');
        
        // ⚠️ Rediriger vers le Hub SANS callback, avec action=logout
        // Cela évite que le Hub reconnecte automatiquement l'utilisateur
        window.location.href = \`\${CONFIG.HUB_FRONTEND_URL}?action=logout\`;
    }
    
    // ═══════════════════════════════════════════════════════════════════════════
    // ⚠️ IMPORTANT : FERMETURE DES ONGLETS LORS DE LA DÉCONNEXION
    // ═══════════════════════════════════════════════════════════════════════════
    //
    // Lors de la déconnexion d'une application, il est IMPÉRATIF de fermer 
    // manuellement tous les onglets correspondant à cette application.
    //
    // Pourquoi ?
    // - Le token JWT reste valide côté navigateur jusqu'à son expiration
    // - Les onglets ouverts conservent le token en mémoire/localStorage
    // - Un onglet non fermé pourrait continuer à fonctionner avec l'ancien token
    // - Pour garantir une déconnexion complète, fermez tous les onglets de l'app
    //
    // Bonne pratique :
    // Informez vos utilisateurs de fermer tous les onglets de l'application
    // après s'être déconnectés pour garantir la sécurité de leur session.
    // ═══════════════════════════════════════════════════════════════════════════
    
    /**
     * Vérifie si l'utilisateur est connecté
     */
    isLoggedIn() {
        return !!this.token;
    }
    
    /**
     * Décode le token pour obtenir les infos utilisateur
     * (sans validation - pour affichage UI uniquement)
     */
    getTokenPayload() {
        if (!this.token) return null;
        
        try {
            const parts = this.token.split('.');
            const payload = JSON.parse(atob(parts[1]));
            return payload;
        } catch (e) {
            return null;
        }
    }
    
    /**
     * Récupère le rôle de l'utilisateur pour cette application
     */
    getAppRole() {
        const payload = this.getTokenPayload();
        if (!payload || !payload.apps) return null;
        return payload.apps[CONFIG.APP_SLUG] || null;
    }
}

// Instance globale
const auth = new HubAuth();
\`\`\`

### Gestion des préférences UI locales

L'application peut stocker des préférences utilisateur localement (thème, langue, etc.) :

\`\`\`javascript
// preferences.js
class UserPreferences {
    constructor() {
        this.storageKey = 'app_preferences';
    }
    
    /**
     * Récupère les préférences de l'utilisateur courant
     */
    get() {
        const payload = auth.getTokenPayload();
        if (!payload) return {};
        
        const allPrefs = JSON.parse(localStorage.getItem(this.storageKey) || '{}');
        return allPrefs[payload.sub] || {};
    }
    
    /**
     * Sauvegarde une préférence
     */
    set(key, value) {
        const payload = auth.getTokenPayload();
        if (!payload) return;
        
        const allPrefs = JSON.parse(localStorage.getItem(this.storageKey) || '{}');
        if (!allPrefs[payload.sub]) {
            allPrefs[payload.sub] = {};
        }
        allPrefs[payload.sub][key] = value;
        localStorage.setItem(this.storageKey, JSON.stringify(allPrefs));
    }
    
    /**
     * Applique les préférences sauvegardées
     */
    apply() {
        const prefs = this.get();
        
        // Thème
        if (prefs.theme === 'dark') {
            document.body.classList.add('dark-theme');
        }
        
        // Langue
        if (prefs.language) {
            document.documentElement.lang = prefs.language;
        }
        
        // Sidebar collapse
        if (prefs.sidebarCollapsed) {
            document.getElementById('sidebar')?.classList.add('collapsed');
        }
    }
}

const userPrefs = new UserPreferences();
\`\`\`

---

## 5. Communication Machine-to-Machine (M2M)

### Création automatique du Service Account

Quand une application est créée dans le Hub, un **Service Account** est automatiquement créé avec ses credentials.

### Exemple Python - Client M2M

\`\`\`python
"""
Client M2M pour communication entre services
Le renouvellement des tokens est géré par le Hub !
"""
import httpx
from datetime import datetime, timedelta
from config import get_settings

settings = get_settings()


class M2MClient:
    def __init__(self, client_id: str, client_secret: str):
        self.hub_url = settings.hub_api_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self._token_expires = None
    
    async def get_token(self) -> str:
        """
        Obtient un token M2M depuis le Hub.
        Le token est mis en cache localement pour éviter les appels répétés.
        """
        # Vérifier si le token est encore valide (marge de 5 min)
        if self._token and self._token_expires:
            if datetime.utcnow() < self._token_expires - timedelta(minutes=5):
                return self._token
        
        # Demander un nouveau token au Hub
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
            )
            response.raise_for_status()
            data = response.json()
            
            self._token = data["access_token"]
            expires_str = data["expires_at"].replace("Z", "+00:00")
            self._token_expires = datetime.fromisoformat(expires_str).replace(tzinfo=None)
            
            return self._token
    
    async def call_api(self, url: str, method: str = "GET", **kwargs) -> dict:
        """Appelle une API avec authentification M2M"""
        token = await self.get_token()
        
        async with httpx.AsyncClient() as client:
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {token}"
            
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()


# Utilisation
m2m = M2MClient(
    client_id="sa_xxx",
    client_secret="secret_xxx"
)

# Appeler une autre API
employees = await m2m.call_api(f"http://{settings.hub_ip}:18087/api/employees")
\`\`\`

---

## 6. Communication inter-API

Cette section détaille comment vos applications peuvent communiquer entre elles via le Hub.

### Architecture

```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   API Salarié   │◄───────►│      HUB        │◄───────►│  Autre API      │
│  (client M2M)   │         │ (Auth centrale) │         │  (client M2M)   │
└─────────────────┘         └─────────────────┘         └─────────────────┘
        │                           │                           │
        │    1. Demande token       │                           │
        │   (client_credentials)    │                           │
        │──────────────────────────►│                           │
        │                           │                           │
        │    2. Token JWT           │                           │
        │◄──────────────────────────│                           │
        │                           │                           │
        │    3. Appel API avec token│                           │
        │───────────────────────────────────────────────────────►
        │                           │                           │
        │                           │    4. Valide token        │
        │                           │◄──────────────────────────│
        │                           │                           │
```

### Configurer l'IP du Hub

Dans le fichier `.env` du Hub :

```env
# Host du Hub (IP ou hostname accessible par les clients)
# Exemples: 192.168.1.100, monserveur.local, hub.mondomaine.fr
HUB_HOST=192.0.2.11
```

### Créer un Service Account

```bash
# 1. Obtenir un token admin
TOKEN=$(curl -s -X POST "http://192.0.2.11:8000/api/auth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&username=admin&password=admin123" | jq -r '.access_token')

# 2. Créer le service account
curl -X POST "http://192.0.2.11:8000/api/service-accounts/" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api_salarie",
    "description": "Service account pour API Salarié",
    "app_ids": [1]
  }'
```

### 📁 Sauvegarde automatique des credentials

Les credentials sont automatiquement sauvegardés dans :
```
secrets/service_accounts/sa_<nom>.env
```

Contenu du fichier :
```env
# Service Account: api_salarie
# Généré automatiquement - NE PAS COMMITER CE FICHIER !

CLIENT_ID=<VOTRE_CLIENT_ID>
CLIENT_SECRET=<VOTRE_CLIENT_SECRET>
```

### Client M2M complet (`hub_client.py`)

Créez ce fichier dans votre application :

\`\`\`python
"""
Client pour communiquer avec le Hub d'authentification (M2M)
"""
import httpx
from datetime import datetime, timedelta
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)


class HubClient:
    """Client pour l'authentification M2M avec le Hub"""
    
    def __init__(self):
        self.hub_host = os.getenv("HUB_HOST", "192.0.2.11")
        self.hub_port = os.getenv("HUB_API_PORT", "8000")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
    
    @property
    def hub_url(self) -> str:
        return f"http://{self.hub_host}:{self.hub_port}"
    
    async def get_token(self) -> str:
        """
        Obtient un token M2M, avec cache automatique.
        Renouvelle le token 5 minutes avant expiration.
        """
        if self._token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at - timedelta(minutes=5):
                return self._token
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Erreur auth Hub: {response.text}")
                raise Exception(f"Impossible d'obtenir un token: {response.status_code}")
            
            data = response.json()
            self._token = data["access_token"]
            expires_in = data.get("expires_in", 1800)
            self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            logger.info("✅ Token M2M obtenu avec succès")
            return self._token
    
    async def call_api(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Appelle une API externe avec le token M2M.
        """
        token = await self.get_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        
        async with httpx.AsyncClient() as client:
            return await client.request(method, url, headers=headers, **kwargs)
    
    async def validate_token(self, token: str) -> dict:
        """Valide un token utilisateur auprès du Hub."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.hub_url}/api/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 200:
                raise Exception("Token invalide")
            return response.json()


# Instance singleton
hub_client = HubClient()
\`\`\`

### Configuration `.env` de votre API

```env
# === Configuration Hub ===
HUB_HOST=192.0.2.11
HUB_API_PORT=8000

# === Credentials Service Account ===
# Copiés depuis secrets/service_accounts/sa_api_salarie.env
CLIENT_ID=<VOTRE_CLIENT_ID>
CLIENT_SECRET=<VOTRE_CLIENT_SECRET>

# === Votre API ===
APP_SLUG=salarie
API_PORT=18087
```

### Exemple d'utilisation

\`\`\`python
from fastapi import APIRouter, HTTPException
from hub_client import hub_client

router = APIRouter()


@router.get("/data-from-other-api")
async def get_external_data():
    """Appeler une autre API via le Hub"""
    response = await hub_client.call_api(
        "GET",
        "http://192.0.2.11:18088/api/other-service/data"
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Erreur API externe")
    
    return response.json()
\`\`\`

### Docker Compose - Réseau partagé

```yaml
services:
  api-salarie:
    build: .
    ports:
      - "18087:18087"
    environment:
      - HUB_HOST=${HUB_HOST:-192.0.2.11}
      - HUB_API_PORT=${HUB_API_PORT:-8000}
      - CLIENT_ID=${CLIENT_ID}
      - CLIENT_SECRET=${CLIENT_SECRET}
    networks:
      - hub-network

networks:
  hub-network:
    external: true
    name: hub_hub-network
```

### Checklist d'intégration inter-API

- [ ] Configurer `HUB_HOST` dans le `.env` du Hub
- [ ] Créer un service account pour votre API
- [ ] Copier les credentials dans votre application
- [ ] Implémenter le `HubClient` dans votre API
- [ ] Copier `hub_auth.py` et `jwt_public_key.pem` (voir section Dockerfile)
- [ ] Configurer le réseau Docker partagé
- [ ] Tester la communication M2M

---

## 7. Gestion des rôles

### Structure du token JWT

\`\`\`json
{
  "sub": "jean.dupont",
  "roles": ["user"],
  "apps": {
    "salarie": "admin",
    "compta": "viewer",
    "rh": "manager"
  },
  "type": "user",
  "exp": 1735650600,
  "iat": 1735648800,
  "jti": "abc123"
}
\`\`\`

### Rôles disponibles

| Rôle | Niveau | Description |
|------|--------|-------------|
| \`admin\` | 4 | Tous les droits sur l'application |
| \`manager\` | 3 | Gestion des données, création/modification |
| \`user\` | 2 | Accès standard lecture/écriture |
| \`viewer\` | 1 | Lecture seule |
| \`service\` | 3 | Service Account (M2M) |

### Vérification des rôles côté backend

\`\`\`python
from hub_auth import require_app_access

# Accès lecture seule (viewer+)
@app.get("/api/data")
async def read_data(user: TokenPayload = Depends(require_app_access("mon-app", "viewer"))):
    ...

# Accès écriture (user+)
@app.post("/api/data")
async def create_data(user: TokenPayload = Depends(require_app_access("mon-app", "user"))):
    ...

# Accès gestion (manager+)
@app.put("/api/settings")
async def update_settings(user: TokenPayload = Depends(require_app_access("mon-app", "manager"))):
    ...

# Accès admin uniquement
@app.delete("/api/data/{id}")
async def delete_data(user: TokenPayload = Depends(require_app_access("mon-app", "admin"))):
    ...
\`\`\`

### Vérification des rôles côté frontend

\`\`\`javascript
// Vérifier le rôle
const role = auth.getAppRole();

if (role === 'admin') {
    // Afficher les boutons admin
    document.getElementById('adminPanel').style.display = 'block';
}

if (['admin', 'manager'].includes(role)) {
    // Afficher les boutons de gestion
    document.getElementById('manageBtn').style.display = 'inline';
}

if (role === 'viewer') {
    // Masquer les boutons d'édition
    document.querySelectorAll('.edit-btn').forEach(btn => btn.style.display = 'none');
}
\`\`\`

---

## 8. Endpoints du Hub

### Authentification

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| \`/api/auth/token\` | POST | Login (password), refresh, ou M2M (client_credentials) |
| \`/api/auth/token/refresh\` | POST | Rafraîchir un token (JSON body) |
| \`/api/auth/token/introspect\` | POST | Vérifier si un token est valide |
| \`/api/auth/logout\` | POST | Déconnecter et révoquer le token |
| \`/api/auth/me\` | GET | Infos utilisateur connecté |
| \`/api/auth/public-key\` | GET | Clé publique RSA |
| \`/api/auth/hub-host\` | GET | IP/hostname du Hub |

### Durée des tokens (géré par le Hub uniquement)

| Type de token | Durée | Configurable par |
|---------------|-------|------------------|
| Access Token | 30 minutes | Hub uniquement |
| Refresh Token | 7 jours | Hub uniquement |
| Token M2M | 30 minutes | Hub uniquement |

> ⚠️ **Rappel** : La durée des tokens est gérée **exclusivement** par le Hub. Les applications ne doivent pas modifier ou prolonger la durée des tokens.

### Format des réponses

**Login réussi :**
\`\`\`json
{
  "access_token": "eyJhbG...",
  "token_type": "bearer",
  "expires_at": "2025-12-31T12:30:00",
  "refresh_token": "xYz123..."
}
\`\`\`

**Erreur 401 :**
\`\`\`json
{
  "detail": "Token expiré - Reconnectez-vous via le Hub"
}
\`\`\`

---

## 9. Configuration du Dockerfile

> **IMPORTANT** : Le Dockerfile de votre application doit copier les fichiers nécessaires à l'authentification !

### Fichiers requis

Votre application a besoin de ces fichiers pour valider les tokens JWT :

| Fichier | Description | Source |
|---------|-------------|--------|
| `hub_auth.py` | Module d'authentification | Fourni par le Hub |
| `jwt_public_key.pem` | Clé publique RSA | `secrets/jwt_public_key.pem` |

### Exemple de Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code de l'application
COPY ./app /app/app

# ⚠️ IMPORTANT : Copier les fichiers d'authentification Hub
COPY ./hub_auth.py /app/hub_auth.py
COPY ./secrets/jwt_public_key.pem /app/secrets/jwt_public_key.pem

# Utilisateur non-root
RUN adduser --disabled-password --gecos '' appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18087"]
```

### Avec Docker Compose (volumes)

Alternativement, montez les fichiers via `docker-compose.yml` :

```yaml
services:
  api-salarie:
    build: .
    volumes:
      # Monter les fichiers d'auth depuis le Hub
      - ../HUB/secrets/jwt_public_key.pem:/app/secrets/jwt_public_key.pem:ro
      - ./hub_auth.py:/app/hub_auth.py:ro
```

### Où trouver ces fichiers ?

1. **`hub_auth.py`** : Généré à partir du template dans ce guide (section [Intégration Backend](#3-intégration-backend-fastapi))
2. **`jwt_public_key.pem`** : Dans le dossier `secrets/` du projet Hub

```bash
# Copier la clé publique depuis le Hub
cp /chemin/vers/HUB/secrets/jwt_public_key.pem ./secrets/
```

---

## 10. Résumé

### Ce que l'application DOIT faire

✅ Valider les tokens JWT avec la clé publique du Hub  
✅ Vérifier les rôles et accès dans le token  
✅ Rediriger vers le Hub si token expiré (401)  
✅ Utiliser des IP (pas localhost) pour les URLs  
✅ Stocker les préférences UI localement si besoin  

### Ce que l'application NE DOIT PAS faire

❌ Créer des tokens JWT  
❌ Définir la durée des tokens  
❌ Gérer les utilisateurs (création, modification)  
❌ Stocker des mots de passe  
❌ Implémenter son propre système de login  
❌ Utiliser \`localhost\` dans les URLs de redirection  

---

## 11. Contact Hub

- **URL Hub API** : \`http://[IP_SERVEUR]:8000\`
- **URL Hub Frontend** : \`http://[IP_SERVEUR]:80\`
- **Documentation API** : \`http://[IP_SERVEUR]:8000/docs\`
