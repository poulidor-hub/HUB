# Guide d'Intégration - Hub d'Authentification

> **Version** : 3.37.1
> **Dernière mise à jour** : 25 août 2026
> **Auteur** : Équipe Hub

Ce document décrit comment intégrer vos applications avec le Hub d'authentification centralisé.

# 1. Vérifier la version actuelle du Hub
curl http://hub:8000/api/docs/integration/version


---

## 📑 Table des matières

### Concepts de base
1. [Principes fondamentaux](#1-principes-fondamentaux)
   - [Le Hub gère TOUTE l'authentification](#-le-hub-gère-toute-lauthentification)
   - [Adresses IP, pas localhost !](#-adresses-ip-pas-localhost-)
   - [Localisation du Hub](#-localisation-du-hub)

### Configuration
2. [Configuration de l'application](#2-configuration-de-lapplication)
   - [Fichier `.env` simplifié](#fichier-env-simplifié)
   - [Fichier `config.py`](#fichier-configpy)

### 🆕 Auto-enregistrement dynamique
3. [Auto-enregistrement et récupération dynamique des credentials](#3-auto-enregistrement-et-récupération-dynamique-des-credentials)
   - [Principe](#principe)
   - [Flux d'auto-enregistrement](#flux-dauto-enregistrement)
   - [Client Python avec auto-enregistrement (`hub_client.py`)](#client-python-avec-auto-enregistrement-hub_clientpy)
   - [Endpoints d'auto-enregistrement](#endpoints-dauto-enregistrement)

### Intégration Backend
4. [Intégration Backend (FastAPI)](#4-intégration-backend-fastapi)
   - [Étape 1 : Dépendances](#étape-1--dépendances)
   - [Étape 2 : Récupérer la clé publique du Hub](#étape-2--récupérer-la-clé-publique-du-hub)
   - [Étape 3 : Module d'authentification `hub_auth.py`](#étape-3--module-dauthentification-hub_authpy)
   - [Étape 4 : Utilisation dans les routes](#étape-4--utilisation-dans-les-routes)

### Intégration Frontend
5. [Intégration Frontend](#5-intégration-frontend)
   - [Principes](#principes)
   - [Paramètres URL du Hub](#paramètres-url-du-hub)
   - [Configuration JavaScript](#configuration-javascript)
   - [Module d'authentification](#module-dauthentification)
   - [Gestion des préférences UI locales](#gestion-des-préférences-ui-locales)

### Communication inter-services
6. [Communication Machine-to-Machine (M2M)](#6-communication-machine-to-machine-m2m)
   - [Création automatique du Service Account](#création-automatique-du-service-account)
   - [Exemple Python - Client M2M](#exemple-python---client-m2m)

7. [🆕 Services Partagés (APIs de données)](#7-services-partagés-apis-de-données)
   - [Concept](#concept)
   - [Configuration d'une API comme service partagé](#configuration-dune-api-comme-service-partagé)
   - [Accès automatique pour les nouvelles applications](#accès-automatique-pour-les-nouvelles-applications)
   - [APIs partagées disponibles](#apis-partagées-disponibles)

8. [Communication inter-API](#8-communication-inter-api)
   - [Architecture](#architecture)
   - [Configurer l'IP du Hub](#configurer-lip-du-hub)
   - [Créer un Service Account](#créer-un-service-account)
   - [Client M2M complet (`hub_client.py`)](#client-m2m-complet-hub_clientpy)
   - [Checklist d'intégration inter-API](#checklist-dintégration-inter-api)

### Sécurité et rôles
9. [Gestion des rôles](#9-gestion-des-rôles)
   - [Structure du token JWT](#structure-du-token-jwt)
   - [Rôles disponibles](#rôles-disponibles)
   - [Vérification des rôles côté backend](#vérification-des-rôles-côté-backend)
   - [Vérification des rôles côté frontend](#vérification-des-rôles-côté-frontend)

### Référence API
10. [Endpoints du Hub](#10-endpoints-du-hub)
    - [Authentification](#authentification)
    - [Auto-enregistrement](#auto-enregistrement)
    - [Documentation et Changelog](#documentation-et-changelog)
    - [Durée des tokens](#durée-des-tokens-géré-par-le-hub-uniquement)
    - [Format des réponses](#format-des-réponses)

### Troubleshooting
11. [🆕 Troubleshooting M2M (Erreurs courantes)](#11-troubleshooting-m2m-erreurs-courantes)
    - [Erreur 401 - Not Authenticated](#-erreur-401---not-authenticated)
    - [🆕 Resync Automatique M2M](#-resync-automatique-m2m-v252)
    - [Erreur 403 - Forbidden (Token reconnu mais accès refusé)](#-erreur-403---forbidden-token-reconnu-mais-accès-refusé)
    - [Erreur 403 - IP non autorisée](#-erreur-403---ip-non-autorisée-auto-enregistrement)
    - [Tableau récapitulatif des erreurs M2M](#-tableau-récapitulatif-des-erreurs-m2m)

### Déploiement
12. [Configuration du Dockerfile](#12-configuration-du-dockerfile)
    - [Fichiers requis](#fichiers-requis)
    - [Exemple de Dockerfile](#exemple-de-dockerfile)
    - [Avec Docker Compose (volumes)](#avec-docker-compose-volumes)


### Annexes
13. [Résumé](#13-résumé)
    - [Ce que l'application DOIT faire](#ce-que-lapplication-doit-faire)
    - [Ce que l'application NE DOIT PAS faire](#ce-que-lapplication-ne-doit-pas-faire)

14. [Contact Hub](#14-contact-hub)

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

### Fichier `.env` simplifié

Créez un fichier \`.env\` à la racine de votre application :

\`\`\`env
# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION MINIMALE (mode auto-enregistrement)
# ══════════════════════════════════════════════════════════════════════════════

# Slug de votre application (identifiant unique)
# ⚠️ IMPORTANT : Ce slug est choisi par VOUS, pas par le Hub !
APP_SLUG=mon-app

# Port de votre API backend
API_PORT=18087

# Port de votre frontend (optionnel)
FRONTEND_PORT=4178

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION HUB (optionnel - auto-détection par défaut)
# ══════════════════════════════════════════════════════════════════════════════

# Adresse du Hub d'authentification
# Par défaut: même IP que l'application (auto-détection)
# Décommentez si le Hub est sur une autre machine
# HUB_HOST=192.168.1.100

# Port de l'API Hub (par défaut: 8000)
HUB_API_PORT=8000

# Port du Frontend Hub (par défaut: 80)
HUB_FRONTEND_PORT=80
\`\`\`

> 💡 Les credentials sont récupérés **automatiquement** au démarrage via l'auto-enregistrement (voir section 3). Aucune configuration manuelle n'est nécessaire.

### Fichier \`config.py\`

\`\`\`python
"""
Configuration de l'application avec détection automatique de l'IP
"""
import os
import socket
from functools import lru_cache
from pydantic_settings import BaseSettings


> ⚠️ **PIÈGE DOCKER** : Dans un conteneur Docker, `socket.gethostbyname(socket.gethostname())` retourne l'**IP interne du conteneur** (ex: `172.17.0.5`), pas l'IP de l'hôte !
> 
> **Solution** : Utilisez **toujours** la variable d'environnement `HUB_HOST` explicitement définie dans votre `docker-compose.yml` ou `.env`, pointant vers l'IP de l'hôte Docker.
> 
> \`\`\`python
> # ❌ INCORRECT en Docker - retourne l'IP interne du conteneur
> import socket
> local_ip = socket.gethostbyname(socket.gethostname())  # 172.17.0.5 (inaccessible de l'extérieur)
> 
> # ✅ CORRECT - toujours utiliser HUB_HOST
> hub_host = os.getenv("HUB_HOST")  # 192.0.2.12 (IP réelle de l'hôte)
> if not hub_host:
>     raise RuntimeError("❌ HUB_HOST doit être défini dans l'environnement Docker")
> \`\`\`

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

## 3. Auto-enregistrement et récupération dynamique des credentials

### Principe

> 🆕 **Nouveauté v2.0** : Les applications peuvent désormais s'auto-enregistrer auprès du Hub et récupérer leurs credentials M2M dynamiquement, **sans avoir à modifier leur fichier `.env`**.

Le flux traditionnel nécessitait de :
1. Créer manuellement l'application dans le Hub
2. Copier les credentials dans le `.env` de l'application
3. Redémarrer l'application

**Avec l'auto-enregistrement**, l'application :
1. Définit son `APP_SLUG` dans son `.env` (son identifiant unique)
2. Au démarrage, s'enregistre automatiquement auprès du Hub
3. Reçoit et stocke ses credentials **en mémoire** (pas dans un fichier)
4. Peut régénérer son secret si besoin (perte, rotation...)

### Flux d'auto-enregistrement

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FLUX AUTO-ENREGISTREMENT                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐                        ┌──────────────┐                   │
│  │ Application  │                        │     HUB      │                   │
│  │  (.env avec  │                        │  (API 8000)  │                   │
│  │  APP_SLUG)   │                        │              │                   │
│  └──────┬───────┘                        └──────┬───────┘                   │
│         │                                       │                           │
│         │  1. POST /api/apps/register           │                           │
│         │  { slug: "mon-app", api_port: 18087 } │                           │
│         │──────────────────────────────────────►│                           │
│         │                                       │                           │
│         │                                       │ 2. Vérifie IP Docker      │
│         │                                       │    (whitelist réseau)     │
│         │                                       │                           │
│         │                                       │ 3. Crée app + SA          │
│         │                                       │    (si nouvelle)          │
│         │                                       │                           │
│         │  4. Réponse avec credentials          │                           │
│         │  { client_id, client_secret, ... }    │                           │
│         │◄──────────────────────────────────────│                           │
│         │                                       │                           │
│         │  5. Stocke en MÉMOIRE                 │                           │
│         │     (pas dans .env !)                 │                           │
│         │                                       │                           │
│         │  6. Utilise pour auth M2M             │                           │
│         │──────────────────────────────────────►│                           │
│         │                                       │                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Sécurité : IP Whitelist

L'auto-enregistrement est **sécurisé par whitelist IP**. Seules les requêtes provenant des réseaux Docker sont acceptées :

| Réseau | Plage IP |
|--------|----------|
| Docker Bridge | `172.17.x.x` à `172.31.x.x` |
| Docker Overlay | `10.0.x.x` |
| Réseaux locaux | `192.168.x.x` |

> ⚠️ Une application externe au réseau Docker ne peut PAS s'auto-enregistrer.

### Client Python avec auto-enregistrement (`hub_client.py`)

Créez ce fichier dans votre application pour gérer l'auto-enregistrement et l'authentification M2M :

> 🆕 **v2.5.2** : Ajout du resync automatique avec protection contre les boucles infinies et thread-safety.

\`\`\`python
"""
Hub Client avec Auto-enregistrement et Resync Automatique
==========================================================
Client pour l'authentification M2M avec le Hub.
Gère automatiquement :
- L'enregistrement de l'application
- La récupération des credentials
- Le resync automatique en cas de secret invalide (401)

Usage:
    from hub_client import hub_client
    
    # Au démarrage de l'app (dans main.py ou lifespan)
    await hub_client.register()
    
    # Pour appeler une autre API
    response = await hub_client.call_api("GET", "http://autre-api:8080/data")
"""
import asyncio
import httpx
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# 🆕 Constantes de protection contre les boucles infinies
MAX_RESYNC_ATTEMPTS = 3   # Nombre max de tentatives de resync
RESYNC_COOLDOWN = 30      # Secondes minimum entre deux resyncs


class HubClient:
    """Client Hub avec auto-enregistrement, authentification M2M et resync automatique"""
    
    def __init__(self):
        # Configuration depuis .env
        self.app_slug = os.getenv("APP_SLUG")
        self.api_port = int(os.getenv("API_PORT", "8000"))
        self.frontend_port = os.getenv("FRONTEND_PORT")
        
        self.hub_host = os.getenv("HUB_HOST", self._detect_hub_host())
        self.hub_port = int(os.getenv("HUB_API_PORT", "8000"))
        
        # Credentials (récupérés dynamiquement via auto-enregistrement)
        self._client_id: Optional[str] = None
        self._client_secret: Optional[str] = None
        
        # Token cache
        self._token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        # État
        self._registered = False
        
        # 🆕 Protection resync (thread-safety + anti-boucle)
        self._resync_lock = asyncio.Lock()
        self._last_resync_attempt: Optional[datetime] = None
        self._resync_count = 0
    
    def _detect_hub_host(self) -> str:
        """Détecte l'IP du Hub (même serveur par défaut)"""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "hub-api"  # Fallback Docker service name
    
    @property
    def hub_url(self) -> str:
        return f"http://{self.hub_host}:{self.hub_port}"
    
    @property
    def has_credentials(self) -> bool:
        """Vérifie si les credentials sont disponibles"""
        return bool(self._client_id and self._client_secret)
    
    async def register(self) -> bool:
        """
        Auto-enregistre l'application auprès du Hub.
        
        - Si l'app est nouvelle : crée l'app et retourne les credentials
        - Si l'app existe déjà : régénère automatiquement le secret
        
        Returns:
            True si l'enregistrement a réussi
        """
        if not self.app_slug:
            logger.error("❌ APP_SLUG non défini dans .env - impossible de s'enregistrer")
            return False
        
        # Si déjà enregistré avec des credentials valides
        if self.has_credentials and self._registered:
            logger.info(f"✅ Déjà enregistré pour '{self.app_slug}'")
            return True
        
        logger.info(f"📝 Auto-enregistrement de '{self.app_slug}' auprès du Hub...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.hub_url}/api/apps/register",
                    json={
                        "slug": self.app_slug,
                        "api_port": self.api_port,
                        "frontend_port": int(self.frontend_port) if self.frontend_port else None
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get("is_new") and data.get("credentials"):
                        # Nouvelle app - stocker les credentials en mémoire
                        creds = data["credentials"]
                        self._client_id = creds["client_id"]
                        self._client_secret = creds["client_secret"]
                        logger.info(f"✅ Application '{self.app_slug}' enregistrée avec succès !")
                        logger.info(f"   Client ID: {self._client_id}")
                        logger.debug(f"   Client Secret: {self._client_secret[:8]}...")
                    else:
                        # App existante - besoin de régénérer le secret
                        logger.warning(f"⚠️ Application '{self.app_slug}' existe déjà. Régénération du secret...")
                        return await self.regenerate_secret()
                    
                    self._registered = True
                    return True
                
                elif response.status_code == 403:
                    logger.error(f"❌ Auto-enregistrement refusé (IP non autorisée)")
                    logger.error(f"   Seules les IPs Docker peuvent s'auto-enregistrer")
                    return False
                
                else:
                    logger.error(f"❌ Erreur d'enregistrement: {response.status_code}")
                    logger.error(f"   {response.text}")
                    return False
                    
        except httpx.ConnectError:
            logger.error(f"❌ Impossible de contacter le Hub à {self.hub_url}")
            logger.error(f"   Vérifiez que HUB_HOST est correctement configuré")
            return False
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'enregistrement: {e}")
            return False
    
    async def regenerate_secret(self) -> bool:
        """
        Régénère le client_secret auprès du Hub via X-Slug.
        
        Utile si :
        - L'application a perdu son secret
        - Rotation de sécurité
        - Premier démarrage d'une app existante
        """
        if not self.app_slug:
            logger.error("❌ APP_SLUG non défini - impossible de régénérer le secret")
            return False
        
        logger.info(f"🔄 Régénération du secret pour '{self.app_slug}'...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.hub_url}/api/apps/me/regenerate-secret",
                    headers={"X-Slug": self.app_slug}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self._client_id = data["client_id"]
                    self._client_secret = data["client_secret"]
                    logger.info(f"✅ Secret régénéré avec succès !")
                    logger.debug(f"   Client ID: {self._client_id}")
                    logger.debug(f"   Nouveau secret: {self._client_secret[:8]}...")
                    self._registered = True
                    # Invalider le token en cache
                    self._token = None
                    self._token_expires_at = None
                    return True
                else:
                    logger.error(f"❌ Erreur de régénération: {response.status_code}")
                    logger.error(f"   {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Erreur lors de la régénération: {e}")
            return False
    
    async def _auto_resync(self) -> bool:
        """
        🆕 Resync automatique avec protection contre les boucles infinies.
        Thread-safe grâce à asyncio.Lock().
        """
        async with self._resync_lock:
            # Vérifier le cooldown
            if self._last_resync_attempt:
                elapsed = (datetime.utcnow() - self._last_resync_attempt).total_seconds()
                if elapsed < RESYNC_COOLDOWN:
                    logger.warning(f"⚠️ Resync ignoré (cooldown {RESYNC_COOLDOWN}s, reste {RESYNC_COOLDOWN - elapsed:.0f}s)")
                    return False
            
            # Vérifier le nombre max de tentatives
            if self._resync_count >= MAX_RESYNC_ATTEMPTS:
                logger.error(f"❌ Nombre max de resyncs atteint ({MAX_RESYNC_ATTEMPTS})")
                return False
            
            self._last_resync_attempt = datetime.utcnow()
            self._resync_count += 1
            
            logger.warning(f"🔄 Secret invalide détecté, resync automatique (tentative {self._resync_count}/{MAX_RESYNC_ATTEMPTS})...")
            
            success = await self.regenerate_secret()
            if success:
                logger.info(f"✅ Credentials régénérés pour '{self.app_slug}' (resync #{self._resync_count})")
            return success
    
    async def get_token(self, _retry_count: int = 0) -> str:
        """
        Obtient un token M2M, avec cache automatique.
        Renouvelle le token 5 minutes avant expiration.
        
        🆕 v2.5.2 : Gère automatiquement les erreurs 401 (secret invalide) avec resync.
        """
        # Vérifier si enregistré
        if not self.has_credentials and not self._registered:
            success = await self.register()
            if not success:
                raise Exception("Impossible de s'enregistrer auprès du Hub")
        
        # Vérifier le cache
        if self._token and self._token_expires_at:
            if datetime.utcnow() < self._token_expires_at - timedelta(minutes=5):
                return self._token
        
        # Demander un nouveau token
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
            )
            
            if response.status_code == 401:
                # 🆕 Secret invalide - resync automatique avec protection
                logger.warning(f"⚠️ Erreur 401 - Secret invalide (tentative {_retry_count + 1}/{MAX_RESYNC_ATTEMPTS})")
                if _retry_count < MAX_RESYNC_ATTEMPTS and await self._auto_resync():
                    logger.info("🔄 Retry après resync...")
                    return await self.get_token(_retry_count=_retry_count + 1)
                raise Exception("Authentification M2M échouée après resync")
            
            if response.status_code != 200:
                logger.error(f"Erreur auth Hub: {response.text}")
                raise Exception(f"Impossible d'obtenir un token: {response.status_code}")
            
            data = response.json()
            self._token = data["access_token"]
            
            # Parser l'expiration
            if "expires_at" in data:
                expires_str = data["expires_at"].replace("Z", "+00:00")
                self._token_expires_at = datetime.fromisoformat(expires_str).replace(tzinfo=None)
            else:
                expires_in = data.get("expires_in", 1800)
                self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            # 🆕 Reset du compteur de resync après succès
            self._resync_count = 0
            logger.debug(f"✅ Token M2M obtenu (expire: {self._token_expires_at})")
            return self._token
    
    async def call_api(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Appelle une API externe avec le token M2M.
        
        Gère automatiquement :
        - L'obtention du token
        - Le renouvellement si expiré
        - La régénération du secret si invalide
        """
        token = await self.get_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.request(method, url, headers=headers, **kwargs)
    
    async def validate_token(self, token: str) -> dict:
        """Valide un token utilisateur auprès du Hub."""
        async with httpx.AsyncClient(timeout=10.0) as client:
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

### Intégration au démarrage de l'application

\`\`\`python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from hub_client import hub_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # === DÉMARRAGE ===
    # Auto-enregistrement auprès du Hub
    success = await hub_client.register()
    if not success:
        import logging
        logging.warning("⚠️ Auto-enregistrement échoué. Mode dégradé activé.")
    
    yield
    
    # === ARRÊT ===
    # Nettoyage si nécessaire


app = FastAPI(lifespan=lifespan)
\`\`\`

### Endpoints d'auto-enregistrement

| Endpoint | Méthode | Description | Sécurité |
|----------|---------|-------------|----------|
| `/api/apps/register` | POST | Auto-enregistrement d'une app | IP Docker uniquement |
| `/api/apps/me/credentials` | GET | Récupère les infos (sans secret) | IP Docker + X-Slug |
| `/api/apps/me/regenerate-secret` | POST | Régénère le client_secret | IP Docker + X-Slug |

#### POST /api/apps/register

**Requête :**
\`\`\`json
{
  "slug": "mon-app",
  "name": "Mon Application",
  "api_port": 18087,
  "frontend_port": 4178
}
\`\`\`

**Réponse (nouvelle app) :**
\`\`\`json
{
  "message": "Application 'mon-app' créée avec succès. Credentials M2M générés.",
  "application": { ... },
  "credentials": {
    "client_id": "sa_abc123...",
    "client_secret": "secret_xyz...",
    "hub_host": "192.0.2.11",
    "hub_api_port": 8000
  },
  "is_new": true
}
\`\`\`

**Réponse (app existante) :**
\`\`\`json
{
  "message": "Application 'mon-app' déjà enregistrée. Utilisez /api/apps/me/credentials...",
  "application": { ... },
  "credentials": { "client_id": "sa_abc123..." },
  "is_new": false
}
\`\`\`

#### POST /api/apps/me/regenerate-secret

**Headers requis :** `X-Slug: mon-app`

**Réponse :**
\`\`\`json
{
  "app_slug": "mon-app",
  "app_name": "Mon Application",
  "client_id": "sa_abc123...",
  "client_secret": "nouveau_secret_xyz...",
  "hub_host": "192.0.2.11",
  "hub_api_port": 8000,
  "message": "⚠️ NOUVEAU SECRET GÉNÉRÉ ! L'ancien secret est maintenant invalide."
}
\`\`\`

---

## 4. Intégration Backend (FastAPI)

### Étape 1 : Dépendances

Ajouter au \`requirements.txt\` :

\`\`\`
pyjwt[crypto]>=2.8.0
cryptography>=42.0.0
httpx>=0.25.0
\`\`\`

### Étape 2 : Récupérer la clé publique du Hub

> 🔑 **À chaque démarrage**, votre application doit récupérer la clé publique depuis le Hub.
> Cela garantit que vous utilisez toujours la clé actuelle, même après une rotation.

#### Méthodes de récupération

| Méthode | Endpoint | Format | Recommandation |
|---------|----------|--------|----------------|
| **JWKS (standard OIDC)** | `GET /api/auth/.well-known/jwks.json` | JSON Web Key Set | ✅ **Recommandé** |
| **PEM brut** | `GET /api/auth/public-key` | PEM string | Compatible legacy |

#### Option A : Via JWKS (recommandé)

Le endpoint JWKS suit le standard OIDC et retourne la clé au format JSON Web Key :

\`\`\`bash
# Récupérer la clé publique au format JWKS
curl http://[IP_HUB]:8000/api/auth/.well-known/jwks.json
\`\`\`

**Réponse :**
\`\`\`json
{
  "keys": [{
    "kty": "RSA",
    "use": "sig",
    "alg": "RS256",
    "kid": "hub-jwt-key-1",
    "n": "...",
    "e": "AQAB"
  }]
}
\`\`\`

**Implémentation Python avec récupération automatique au démarrage :**

\`\`\`python
"""
hub_auth.py - Module d'authentification avec récupération dynamique de la clé
"""
import jwt
import httpx
import logging
from jwt import PyJWKClient
from functools import lru_cache
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Client JWKS pour récupérer automatiquement la clé publique
_jwks_client: PyJWKClient | None = None


def get_jwks_client() -> PyJWKClient:
    """
    Retourne le client JWKS (initialisé au premier appel).
    La clé publique est récupérée automatiquement depuis le Hub.
    """
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.hub_api_url}/api/auth/.well-known/jwks.json"
        logger.info(f"🔑 Récupération de la clé publique depuis {jwks_url}")
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
        logger.info("✅ Clé publique JWT chargée avec succès")
    return _jwks_client


def verify_hub_token(token: str) -> dict:
    """
    Vérifie un token JWT en récupérant dynamiquement la clé depuis le Hub.
    """
    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expiré - Reconnectez-vous via le Hub"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Token invalide: {str(e)}"
        )


# Initialisation au démarrage (dans main.py lifespan)
async def init_auth():
    """Appeler au démarrage pour pré-charger la clé publique"""
    get_jwks_client()
\`\`\`

**Intégration dans le lifespan de votre application :**

\`\`\`python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from hub_auth import init_auth

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Récupérer la clé publique JWT au démarrage
    await init_auth()
    yield

app = FastAPI(lifespan=lifespan)
\`\`\`

#### Option B : Via endpoint PEM (legacy)

\`\`\`python
import httpx
from config import get_settings

async def fetch_public_key() -> str:
    """Récupère la clé publique PEM au démarrage"""
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
        # ⚠️ IMPORTANT: apps peut être un dict {"app-slug": "role"} OU une liste ["app-slug1", "app-slug2"]
        # Pour assurer la compatibilité, on normalise toujours en dict
        raw_apps = payload.get("apps", {})
        if isinstance(raw_apps, list):
            # Si c'est une liste, on convertit en dict avec rôle "service" par défaut
            self.apps: dict = {app: "service" for app in raw_apps}
        else:
            self.apps: dict = raw_apps
        self.exp: int = payload.get("exp", 0)             # Expiration
        # ⚠️ Le champ JWT est "type", mais on accepte aussi "token_type" par rétrocompatibilité
        self.token_type: str = payload.get("type", payload.get("token_type", "user"))
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
        """
        Vérifie si c'est un service account (M2M).
        
        🆕 v2.5.2 : Détection robustifiée pour gérer plusieurs formats :
        - type = "service_account" (standard)
        - type = "service" (legacy)
        - role "service_account" dans roles
        - rôle "service" dans apps
        """
        return (
            self.token_type in ("service", "service_account") or
            "service_account" in self.roles or
            "service" in self.apps.values()
        )


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

## 5. Intégration Frontend

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

### Ouverture depuis le dashboard Hub

Quand un utilisateur clique sur une application dans le dashboard, le Hub prépare le lancement via \`POST /api/auth/apps/{slug}/launch\` :

- il vérifie que l'utilisateur a accès à l'application ;
- il crée un JWT frais contenant \`apps[slug]\` avec le rôle applicatif ;
- il redirige l'onglet app vers \`callback_url\` si elle est configurée, sinon vers \`frontend_url\` ;
- il ajoute le token dans l'URL sous la forme \`?access_token=...\`.

Votre frontend doit donc accepter le token sur sa route \`callback_url\`, généralement \`/callback\`. Cette route est l'entrée recommandée pour initialiser la session applicative.

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
- Doit être réservé à une déconnexion explicite de l'utilisateur, pas à un échec silencieux d'initialisation de l'application

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
     * ⚠️ apps peut être un dict {"app-slug": "role"} ou une liste ["app-slug1", ...]
     */
    getAppRole() {
        const payload = this.getTokenPayload();
        if (!payload || !payload.apps) return null;
        
        // Gérer le cas où apps est une liste (certains tokens M2M)
        if (Array.isArray(payload.apps)) {
            return payload.apps.includes(CONFIG.APP_SLUG) ? 'service' : null;
        }
        
        return payload.apps[CONFIG.APP_SLUG] || null;
    }
    
    /**
     * Vérifie si l'utilisateur a accès à cette application
     * ⚠️ apps peut être un dict ou une liste
     */
    hasAppAccess() {
        const payload = this.getTokenPayload();
        if (!payload || !payload.apps) return false;
        
        if (Array.isArray(payload.apps)) {
            return payload.apps.includes(CONFIG.APP_SLUG);
        }
        
        return CONFIG.APP_SLUG in payload.apps;
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

## 6. Communication Machine-to-Machine (M2M)

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

### 🆕 Découverte des services partagés (v2.5.3)

> 💡 Besoin de connaître les APIs disponibles pour la communication M2M ? Utilisez l'endpoint de découverte `GET /api/apps/discover` pour lister dynamiquement les services partagés. Voir [Section 7 - Services Partagés](#7-services-partagés-apis-de-données) pour plus de détails.

---

## 7. Services Partagés (APIs de données)

> 🆕 **Nouveauté v2.1** : Les applications peuvent désormais accéder automatiquement aux APIs de données partagées (ex: API Salariés) sans configuration manuelle des droits d'accès.

### Concept

Certaines APIs fournissent des données communes à toutes les applications de l'entreprise (données RH, référentiels, etc.). Plutôt que de configurer manuellement les droits d'accès pour chaque nouvelle application, le Hub peut **automatiquement** accorder l'accès à ces APIs partagées.

```
┌─────────────────┐     1. Auto-enregistrement        ┌─────────────┐
│   Nouvelle App  │ ─────────────────────────────────►│     Hub     │
│   (démarrage)   │     { slug, api_port, ... }       │             │
└─────────────────┘                                    └─────────────┘
                                                              │
                          2. Création app + service account   │
                             + liens AUTOMATIQUES vers        │
                             APIs partagées (is_shared_service)│
                                                              ▼
┌─────────────────┐     3. Réponse avec credentials   ┌─────────────┐
│   Nouvelle App  │ ◄─────────────────────────────────│     Hub     │
│                 │     + accès aux APIs partagées    │             │
└─────────────────┘                                    └─────────────┘
        │
        │ 4. Peut immédiatement appeler les APIs partagées !
        ▼
┌─────────────────┐     5. Appel M2M avec token       ┌─────────────┐
│   Nouvelle App  │ ─────────────────────────────────►│ API Salariés│
│                 │     Authorization: Bearer <token> │ (partagée)  │
└─────────────────┘                                    └─────────────┘
```

### Configuration d'une API comme service partagé

#### 1. Modèle de données

Le modèle `Application` inclut un champ `is_shared_service` :

```python
class Application(SQLModel, table=True):
    __tablename__ = "applications"
    
    # ... champs existants ...
    
    is_shared_service: bool = Field(
        default=False,
        description="Si True, cette API est accessible par tous les service accounts (API de données partagées)"
    )
```

#### 2. Marquer une API comme partagée

```sql
-- Via SQL directement
UPDATE applications SET is_shared_service = TRUE WHERE slug = 'salarie';

-- Ou via l'API Hub (admin requis)
PATCH /api/apps/{app_id}
{ "is_shared_service": true }
```

### Accès automatique pour les nouvelles applications

Lors de la création d'un service account (via auto-enregistrement ou création manuelle), le Hub ajoute **automatiquement** les droits d'accès aux APIs partagées :

```python
# Logique interne du Hub lors de la création d'un service account
from sqlmodel import select

# Récupérer toutes les apps partagées
shared_apps = session.exec(
    select(Application).where(Application.is_shared_service == True)
).all()

# Les ajouter aux autorisations du service account
for shared_app in shared_apps:
    if shared_app.id != app.id:  # Ne pas ajouter l'app elle-même
        access = ServiceAccountAppAccess(
            service_account_id=service_account.id,
            app_id=shared_app.id
        )
        session.add(access)

session.commit()
```

### APIs partagées disponibles

| Slug | Nom | Description |
|------|-----|-------------|
| `salarie` | API Salariés | Données RH des employés |

> 💡 Pour ajouter une nouvelle API partagée, utilisez la commande SQL ci-dessus ou l'API d'administration.

### 🆕 Découverte dynamique des services partagés (v2.5.3)

L'endpoint `GET /api/apps/discover` permet à une application de **découvrir dynamiquement** les APIs partagées disponibles, sans connaître leur configuration à l'avance.

#### Endpoint

| Méthode | URL | Authentification |
|---------|-----|------------------|
| `GET` | `/api/apps/discover` | Token M2M (Service Account) |

#### Requête

\`\`\`bash
# Obtenir un token M2M
TOKEN=$(curl -s -X POST "http://[HUB_IP]:8000/api/auth/token" \
  -d "grant_type=client_credentials&client_id=sa_xxx&client_secret=secret_xxx" \
  | jq -r '.access_token')

# Découvrir les services partagés
curl -X GET "http://[HUB_IP]:8000/api/apps/discover" \
  -H "Authorization: Bearer $TOKEN"
\`\`\`

#### Réponse

\`\`\`json
{
  "shared_services": [
    {
      "slug": "salarie",
      "name": "API Salariés",
      "description": "Données RH des employés",
      "api_url": "http://192.0.2.11:18087",
      "is_shared_service": true
    },
    {
      "slug": "referentiel",
      "name": "API Référentiels",
      "description": "Référentiels métier (communes, codes, etc.)",
      "api_url": "http://192.0.2.11:18088",
      "is_shared_service": true
    }
  ],
  "total_shared": 2
}
\`\`\`

#### Exemple Python avec `hub_client`

\`\`\`python
from hub_client import hub_client

async def discover_and_call_services():
    """Découvre les services partagés et les appelle dynamiquement"""
    
    # 1. Découvrir les services disponibles
    response = await hub_client.call_api(
        "GET",
        f"{hub_client.hub_url}/api/apps/discover"
    )
    discovery = response.json()
    
    print(f"📡 {discovery['total_shared']} services partagés disponibles:")
    for service in discovery['shared_services']:
        print(f"   - {service['name']} ({service['slug']}): {service['api_url']}")
    
    # 2. Appeler un service découvert dynamiquement
    salarie_service = next(
        (s for s in discovery['shared_services'] if s['slug'] == 'salarie'),
        None
    )
    
    if salarie_service:
        employees = await hub_client.call_api(
            "GET",
            f"{salarie_service['api_url']}/api/employees"
        )
        print(f"✅ {len(employees.json())} employés récupérés")
\`\`\`

### Exemple d'utilisation

Une fois votre application enregistrée, elle peut **immédiatement** appeler les APIs partagées :

```python
from hub_client import hub_client

# Au démarrage de l'app
await hub_client.register()

# L'API Salariés est automatiquement accessible !
response = await hub_client.call_api(
    "GET",
    "http://192.0.2.11:18087/api/employees"
)
employees = response.json()
```

---

## 8. Communication inter-API

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

## 9. Gestion des rôles

### Structure du token JWT

> ⚠️ **IMPORTANT** : Le champ `apps` peut être soit un **dictionnaire** `{"app-slug": "role"}` soit une **liste** `["app-slug1", "app-slug2"]`.
> Votre code doit gérer les deux formats (voir `TokenPayload` dans la section `hub_auth.py`).

**Format dictionnaire (token utilisateur) :**
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

**Format liste (certains tokens M2M/service account) :**
\`\`\`json
{
  "sub": "sa_mon-app",
  "roles": ["service_account"],
  "apps": ["salarie", "exemple-app", "referentiels"],
  "type": "service_account",
  "exp": 1735650600,
  "iat": 1735648800,
  "jti": "def456"
}
\`\`\`

### Rôles disponibles

| Rôle | Niveau | Description |
|------|--------|-------------|
| \`admin\` | 4 | Tous les droits sur l'application |
| \`manager\` | 3 | Gestion des données, création/modification |
| \`user\` | 2 | Accès standard lecture/écriture |
| \`viewer\` | 1 | Lecture seule |
| \`service\` | 3 | Service Account (M2M) - équivalent à manager |

#### Hiérarchie des rôles

La hiérarchie des rôles suit l'ordre croissant suivant :

```
viewer (1) < user (2) < manager (3) = service (3) < admin (4)
```

Un utilisateur avec un rôle de niveau N peut accéder aux ressources qui nécessitent un rôle de niveau ≤ N.

**Exemple d'implémentation Python :**

\`\`\`python
ROLE_HIERARCHY = {
    "viewer": 1,
    "user": 2,
    "manager": 3,
    "service": 3,  # Équivalent à manager pour les Service Accounts
    "admin": 4
}

def has_minimum_role(user_role: str, required_role: str) -> bool:
    """
    Vérifie si le rôle de l'utilisateur atteint le niveau minimum requis.
    
    Args:
        user_role: Rôle actuel de l'utilisateur (ex: "manager")
        required_role: Rôle minimum requis (ex: "user")
    
    Returns:
        True si user_role >= required_role dans la hiérarchie
    """
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    required_level = ROLE_HIERARCHY.get(required_role, 0)
    return user_level >= required_level

# Exemples d'utilisation
has_minimum_role("admin", "manager")   # True - admin (4) >= manager (3)
has_minimum_role("user", "manager")    # False - user (2) < manager (3)
has_minimum_role("service", "manager") # True - service (3) >= manager (3)
\`\`\`

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

## 10. Endpoints du Hub

### Authentification

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| \`/api/auth/token\` | POST | Login (password), refresh, ou M2M (client_credentials) |
| \`/api/auth/token/refresh\` | POST | Rafraîchir un token (JSON body) |
| \`/api/auth/token/introspect\` | POST | Vérifier si un token est valide |
| \`/api/auth/logout\` | POST | Déconnecter et révoquer le token |
| \`/api/auth/me\` | GET | Infos utilisateur connecté |
| \`/api/auth/public-key\` | GET | Clé publique RSA (format PEM) |
| \`/api/auth/.well-known/jwks.json\` | GET | 🆕 Clé publique RSA (format JWKS - standard OIDC) |
| \`/api/auth/hub-host\` | GET | IP/hostname du Hub |

#### Renouvellement de token (OAuth2)

Le Hub supporte le flux OAuth2 standard pour le renouvellement de tokens via l'endpoint unifié `/api/auth/token` avec `grant_type=refresh_token`.

> ⚠️ **IMPORTANT** : Le renouvellement de token est géré **exclusivement** par le Hub. Les applications clientes doivent utiliser le flux OAuth2 ci-dessous.

**Flux OAuth2 (recommandé) :**

\`\`\`python
import httpx
from datetime import datetime

class TokenManager:
    """Gestionnaire de tokens avec renouvellement automatique."""
    
    def __init__(self, hub_url: str):
        self.hub_url = hub_url
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None
    
    async def refresh_access_token(self) -> str:
        """
        Renouvelle l'access token via le flux OAuth2.
        
        Le Hub utilise la rotation automatique des refresh tokens :
        chaque renouvellement retourne un nouveau refresh_token.
        """
        if not self.refresh_token:
            raise ValueError("Pas de refresh token disponible")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.hub_url}/api/auth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if response.status_code != 200:
                raise Exception(f"Refresh échoué: {response.status_code}")
            
            data = response.json()
            
            # Mettre à jour les tokens (rotation automatique)
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token")  # Nouveau refresh token
            self.expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
            
            return self.access_token
    
    def is_expired(self) -> bool:
        """Vérifie si le token est expiré ou sur le point de l'être."""
        if not self.expires_at:
            return True
        # Considérer expiré 5 minutes avant pour éviter les problèmes de timing
        from datetime import timedelta, timezone
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(minutes=5)
    
    async def get_valid_token(self) -> str:
        """Retourne un token valide, en le renouvelant si nécessaire."""
        if self.is_expired() and self.refresh_token:
            await self.refresh_access_token()
        return self.access_token
\`\`\`

**Réponse du Hub :**

\`\`\`json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_at": "2026-01-03T15:30:00",
  "refresh_token": "new_refresh_token_abc123"
}
\`\`\`

> 💡 **Note** : Le Hub implémente la **rotation des refresh tokens**. Chaque renouvellement invalide l'ancien refresh token et en émet un nouveau. Conservez toujours le dernier refresh token reçu.

### Auto-enregistrement (🆕 v2.0)

| Endpoint | Méthode | Description | Sécurité |
|----------|---------|-------------|----------|
| \`/api/apps/register\` | POST | Auto-enregistrement d'une application | IP Docker uniquement |
| \`/api/apps/me/credentials\` | GET | Récupère les credentials (sans secret) | IP Docker + X-Slug |
| \`/api/apps/me/regenerate-secret\` | POST | Régénère le client_secret | IP Docker + X-Slug |

> 💡 Ces endpoints sont accessibles **uniquement depuis les réseaux Docker** (172.17.x.x - 172.31.x.x, 10.0.x.x, 192.168.x.x).

### 🆕 Découverte M2M (v2.5.3)

| Endpoint | Méthode | Description | Sécurité |
|----------|---------|-------------|----------|
| \`/api/apps/discover\` | GET | Liste les services partagés disponibles | Token M2M (Service Account) |

> 💡 Cet endpoint permet aux applications de découvrir dynamiquement les APIs partagées (`is_shared_service=True`) sans configuration préalable.

#### Header d'identification

Les endpoints `/me/credentials` et `/me/regenerate-secret` utilisent le header `X-Slug` pour identifier l'application :

| Header | Description | Exemple |
|--------|-------------|---------|
| `X-Slug` | Slug de l'application | `X-Slug: mon-app` |

**Exemples :**

\`\`\`bash
# Récupérer les credentials
curl -H "X-Slug: mon-app" http://hub:8000/api/apps/me/credentials

# Régénérer le secret
curl -X POST -H "X-Slug: mon-app" http://hub:8000/api/apps/me/regenerate-secret
\`\`\`

### Documentation et Changelog (🆕 v2.2)

Ces endpoints permettent aux agents IA et aux développeurs de consulter la documentation et de suivre les modifications.

#### Documentation d'intégration

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| \`/api/docs/integration\` | GET | Documentation complète (Markdown) |
| \`/api/docs/integration?section=X\` | GET | Section spécifique (numéro ou alias) |
| \`/api/docs/integration/sections\` | GET | Liste JSON de toutes les sections |
| \`/api/docs/integration/shortcuts\` | GET | Guide ASCII des raccourcis |
| \`/api/docs/integration/version\` | GET | Version et métadonnées |

**Aliases disponibles :** \`principes\`, \`config\`, \`auto-register\`, \`backend\`, \`frontend\`, \`m2m\`, \`shared\`, \`inter-api\`, \`roles\`, \`endpoints\`, \`docker\`, \`resume\`, \`contact\`

#### Changelog (historique des modifications)

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| \`/api/docs/changelog\` | GET | Changelog complet |
| \`/api/docs/changelog?since=X.Y.Z\` | GET | Changements depuis une version |
| \`/api/docs/changelog?latest=true\` | GET | Dernière version uniquement |
| \`/api/docs/changelog?format=json\` | GET | Format JSON structuré |
| \`/api/docs/changelog/versions\` | GET | Liste des versions disponibles |
| \`/api/docs/changelog/diff?from_version=X.Y.Z\` | GET | Différences entre versions |

**Exemples d'utilisation pour agents IA :**

\`\`\`bash
# Quelle est la version actuelle ?
curl http://hub:8000/api/docs/integration/version

# Quels changements depuis ma version 2.0.0 ?
curl "http://hub:8000/api/docs/changelog?since=2.0.0"

# Voir le diff détaillé avec checklist
curl "http://hub:8000/api/docs/changelog/diff?from_version=2.0.0"

# Format JSON pour parsing automatique
curl "http://hub:8000/api/docs/changelog?format=json"
\`\`\`

**Réponse du diff (exemple) :**

\`\`\`json
{
  "from_version": "2.0.0",
  "to_version": "2.2.0",
  "versions_included": ["2.2.0", "2.1.0"],
  "changes_count": 15,
  "summary": {
    "new_features": 5,
    "doc_changes": 3,
    "code_changes": 4,
    "breaking_changes": 0,
    "checklist_items": 3
  },
  "action_required": true,
  "changes": {
    "features": ["[2.1.0] Services Partagés M2M", ...],
    "checklist": ["[2.1.0] Mettre à jour la numérotation des sections", ...]
  }
}
\`\`\`

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

## 11. Troubleshooting M2M (Erreurs courantes)

### 🔴 Erreur 401 - Not Authenticated

**Symptôme** : L'API cible répond `401 Unauthorized`

**Causes possibles** :
- Token absent ou malformé dans le header `Authorization`
- Token expiré
- Clé publique JWT non synchronisée
- **🆕 Secret M2M invalide** (régénéré côté Hub ou expiré)

**Solutions** :
```bash
# 1. Vérifier que le token est bien envoyé
curl -v http://api-cible:8080/endpoint \
  -H "Authorization: Bearer $TOKEN"

# 2. Décoder le token pour vérifier l'expiration
echo $TOKEN | cut -d'.' -f2 | base64 -d | jq .

# 3. Obtenir un nouveau token
curl -X POST http://hub:8000/api/auth/token \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET"
```

### 🆕 Resync Automatique M2M (v2.5.2)

Si vous utilisez le `HubClient` fourni dans ce guide, les erreurs 401 liées à un secret invalide sont gérées **automatiquement** :

```
┌─────────────────────────────────────────────────────────────┐
│  Architecture de résilience M2M                             │
├─────────────────────────────────────────────────────────────┤
│  get_token()                                                │
│       │                                                     │
│       ▼                                                     │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │ Cache Token │────▶│ POST /token │────▶│   Success   │   │
│  │   valide?   │     │             │     │             │   │
│  └─────────────┘     └─────────────┘     └─────────────┘   │
│       │ non               │ 401                             │
│       ▼                   ▼                                 │
│  ┌─────────────┐     ┌─────────────┐                       │
│  │  Obtenir    │     │ _auto_resync│◀─── asyncio.Lock()    │
│  │ new token   │     │             │     (thread-safe)     │
│  └─────────────┘     └─────────────┘                       │
│                           │                                 │
│                           ▼                                 │
│                      ┌─────────────┐                       │
│                      │ regenerate- │                       │
│                      │   secret    │                       │
│                      └─────────────┘                       │
│                           │                                 │
│                           ▼                                 │
│                      ┌─────────────┐                       │
│                      │   Retry     │ (max 3 tentatives)    │
│                      │ get_token() │                       │
│                      └─────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

**Protections intégrées :**

| Protection | Description |
|------------|-------------|
| `MAX_RESYNC_ATTEMPTS = 3` | Nombre max de tentatives avant abandon |
| `RESYNC_COOLDOWN = 30` | Secondes minimum entre deux resyncs |
| `asyncio.Lock()` | Protection contre les accès concurrents |

**Exemple de logs lors d'un resync :**
```
⚠️ Erreur 401 - Secret invalide (tentative 1/3)
🔄 Secret invalide détecté, resync automatique (tentative 1/3)...
✅ Credentials régénérés pour 'mon-app' (resync #1)
🔄 Retry après resync...
✅ Token M2M obtenu (expire: 2026-01-03 20:19:42)
```

> 💡 **Conseil** : Si vous voyez des resyncs fréquents dans les logs, vérifiez si le Hub n'est pas en cours de redémarrage ou si le secret n'est pas régénéré manuellement.

---

### 🔴 Erreur 403 - Forbidden (Token reconnu mais accès refusé)

**Symptôme** : L'API cible répond `403 Forbidden` alors que le token est valide

**Cause** : Le Service Account n'a **pas les droits d'accès** à l'API cible.

Le token M2M contient un champ `apps` listant les applications autorisées. Si l'API cible n'est pas dans cette liste, l'accès est refusé.

**Vérification** :
```bash
# Décoder le token pour voir les apps autorisées
echo $TOKEN | cut -d'.' -f2 | base64 -d | jq '.apps'

# Exemple de sortie :
# {
#   "exemple-app": "service",
#   "salarie": "service"       # ← L'API salarie doit être présente !
# }
```

**Solutions** :

#### Option A : Marquer l'API comme service partagé (recommandé pour APIs de données)

Si l'API cible est une API de données commune (ex: API Salariés, API Référentiels), elle devrait être accessible à toutes les applications :

```sql
-- Via SQL sur la base Hub
UPDATE applications SET is_shared_service = TRUE WHERE slug = 'salarie';
```

Ou via l'API Hub (admin requis) :
```bash
# 1. Obtenir un token admin
ADMIN_TOKEN=$(curl -s -X POST http://hub:8000/api/auth/token \
  -d "grant_type=password&username=admin&password=admin123" | jq -r '.access_token')

# 2. Récupérer l'ID de l'app
APP_ID=$(curl -s http://hub:8000/api/apps/ \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[] | select(.slug=="salarie") | .id')

# 3. Activer is_shared_service
curl -X PATCH http://hub:8000/api/apps/$APP_ID \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_shared_service": true}'
```

> ✅ **Depuis v2.5.4** : Les Service Accounts existants sont **automatiquement resynchronisés** lors d'un `regenerate_secret`. Le simple fait de redémarrer une application (qui appelle `regenerate_secret` via `hub_client.py`) synchronise ses accès aux APIs partagées.

#### Option B : Ajouter l'accès manuellement au Service Account

```bash
# 1. Lister les service accounts pour trouver l'ID
curl http://hub:8000/api/service-accounts/ \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[] | {id, name}'

# 2. Voir les apps actuellement autorisées
curl http://hub:8000/api/service-accounts/{sa_id} \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.applications'

# 3. Mettre à jour les apps autorisées (liste complète des IDs)
curl -X PUT http://hub:8000/api/service-accounts/{sa_id}/apps \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[1, 5, 12]'  # IDs des apps: salarie(1), exemple-app(5), autre-app(12)
```

#### Option C : Redémarrer l'application pour resynchroniser (v2.5.4+)

Depuis la v2.5.4, le `regenerate_secret` resynchronise automatiquement les accès aux APIs partagées :

```bash
# Simple redémarrage de l'application
docker restart mon-app

# Le hub_client.py appelle automatiquement regenerate_secret au démarrage
# → Les APIs is_shared_service=True sont ajoutées aux accès du SA
```

> 💡 **Avantage** : Aucune modification du code client n'est nécessaire. Le Hub gère tout côté serveur.

---

### 🔴 Erreur 403 - IP non autorisée (Auto-enregistrement)

**Symptôme** : `POST /api/apps/register` répond `403 Forbidden`

**Cause** : L'auto-enregistrement est réservé aux IPs du réseau Docker.

**Solution** : Vérifiez que votre application s'exécute dans le même réseau Docker que le Hub, ou configurez manuellement l'application via l'API d'administration.

---

### 📊 Tableau récapitulatif des erreurs M2M

| Code | Message | Cause | Solution |
|------|---------|-------|----------|
| **401** | Not authenticated | Token absent/expiré/invalide | Régénérer le token |
| **403** | Forbidden | Service Account sans accès à l'API | Activer `is_shared_service` OU ajouter l'app aux accès du SA |
| **403** | IP non autorisée | Auto-enregistrement hors Docker | Créer l'app manuellement |
| **500** | Internal error | Erreur côté Hub | Vérifier les logs du Hub |

---

## 12. Configuration du Dockerfile

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

## 13. Résumé

### Ce que l'application DOIT faire

✅ Valider les tokens JWT avec la clé publique du Hub  
✅ Vérifier les rôles et accès dans le token  
✅ Rediriger vers le Hub si token expiré (401)  
✅ Utiliser des IP (pas localhost) pour les URLs  
✅ Stocker les préférences UI localement si besoin  
✅ Utiliser l'auto-enregistrement pour accéder automatiquement aux APIs partagées  

### Ce que l'application NE DOIT PAS faire

❌ Créer des tokens JWT  
❌ Définir la durée des tokens  
❌ Gérer les utilisateurs (création, modification)  
❌ Stocker des mots de passe  
❌ Implémenter son propre système de login  
❌ Utiliser \`localhost\` dans les URLs de redirection  

---

## 14. Contact Hub

- **URL Hub API** : \`http://[IP_SERVEUR]:8000\`
- **URL Hub Frontend** : \`http://[IP_SERVEUR]:80\`
- **Documentation API** : \`http://[IP_SERVEUR]:8000/docs\`
