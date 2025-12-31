# Guide d'Intégration - Hub d'Authentification

Ce document décrit comment intégrer la validation des tokens JWT du Hub dans une API FastAPI existante.

---

## Prérequis

- API FastAPI existante
- Python 3.10+
- Accès au Hub d'Authentification

---

## Étape 1 : Installer les dépendances

Ajouter ces dépendances au fichier `requirements.txt` du projet :

```
pyjwt[crypto]>=2.8.0
cryptography>=42.0.0
```

Ou installer via pip :

```bash
pip install "pyjwt[crypto]>=2.8.0" "cryptography>=42.0.0"
```

---

## Étape 2 : Copier la clé publique

Créer un fichier `jwt_public_key.pem` à la racine du projet (ou dans un dossier `secrets/`) avec le contenu suivant :

```
-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAo5T9ZVFK9SOyEuUKnwo+
HrzpYAFJb2KiVqU/MO3J5XVmB5tuIgWXjud6GnImgCklt1p2rQ/66OXkW414GwXJ
+euzOaJvXwSOjG3byjVerXKh3VBIR6wjUrK6BE02Z7t0ZC/t+aVUp0Bxv9h15OtE
aLGc5zdmnVLu2bi+tgQbUIFkd+ncXn4/+1MuHtNHo+NYHgVx8sUPY2yDBx/ue5jr
BezHl9S4Zvu7yePzHCUz2yY9lZ3NTcuO6iYUTfefVSvP/NM5p+f1sDTpIIkGiF1A
CTcwWYLkoAuvWm2cxRF2TbmtrCTeNJL2AMPQQugVTIYu5DIfROEuG0rghZjl3hhs
ZPAo4MlO4089uHq7yTmbD8kQ6oiscFgwaCF5sCUWpq1jNpVthKIetnTl4t4Sv/mW
+A6kXWQ7IMWZQ3/8It1cVhBSDzBqD3zDjPZu7VXaWFTHw0CIIC06ToARhPR2kJ7c
J1zyKzFm8PfVS7Ujv3gwOn40URt8u/MurPZDcUmzCHtdD9wxCkxg5q0P67lOGLRX
sCbgLU1/gBfmvdhF2faHihv5WPCp1gro+YJXeLec3lFXQmkhOASbmOA4luyi6AcJ
HBVOVoqweV0iHI9sAftWrHjnt9Io3nTklGoi4cUU5fobYwCoun8h8JZuEyvl0p40
tPxUKpsU7DYLoXf33iBqDScCAwEAAQ==
-----END PUBLIC KEY-----
```

> **Note** : Cette clé publique est spécifique à cette instance du Hub. Elle peut aussi être récupérée via `GET http://[HUB_URL]:8000/api/auth/public-key`.

---

## Étape 3 : Créer le module de validation JWT

Créer un fichier `hub_auth.py` dans le projet :

```python
"""
Module d'authentification Hub
Valide les tokens JWT émis par le Hub central d'authentification.
"""

import jwt
from pathlib import Path
from typing import Optional, List
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# Configuration
JWT_ALGORITHM = "RS256"
JWT_PUBLIC_KEY_PATH = "jwt_public_key.pem"  # Adapter le chemin si nécessaire

# Charger la clé publique
def load_public_key() -> str:
    """Charge la clé publique RSA pour vérifier les tokens du Hub."""
    key_path = Path(JWT_PUBLIC_KEY_PATH)
    if not key_path.exists():
        # Essayer dans le dossier secrets/
        key_path = Path("secrets/jwt_public_key.pem")
    if not key_path.exists():
        raise FileNotFoundError(
            f"Clé publique JWT non trouvée. "
            f"Placer le fichier jwt_public_key.pem à la racine du projet."
        )
    return key_path.read_text().strip()

PUBLIC_KEY = load_public_key()

# OAuth2 scheme - pointe vers le Hub pour la documentation OpenAPI
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="http://localhost:8000/api/auth/token",  # URL du Hub
    auto_error=True
)


class TokenPayload:
    """Représente le contenu d'un token JWT validé."""
    def __init__(self, payload: dict):
        self.sub: str = payload.get("sub", "")  # Username
        self.roles: List[str] = payload.get("roles", [])  # ["admin"] ou ["user"]
        self.apps: List[str] = payload.get("apps", [])  # Slugs des apps autorisées
        self.exp: int = payload.get("exp", 0)  # Timestamp d'expiration
        self.raw: dict = payload  # Payload complet


def verify_hub_token(token: str) -> TokenPayload:
    """
    Vérifie un token JWT émis par le Hub.
    
    Args:
        token: Le token JWT à vérifier
        
    Returns:
        TokenPayload: Les informations contenues dans le token
        
    Raises:
        HTTPException: Si le token est invalide ou expiré
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
            detail="Token expiré - Veuillez vous reconnecter via le Hub",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalide: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"}
        )


async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    """
    Dépendance FastAPI pour récupérer l'utilisateur courant.
    
    Usage:
        @app.get("/protected")
        async def protected_route(user: TokenPayload = Depends(get_current_user)):
            return {"user": user.sub}
    """
    return verify_hub_token(token)


async def get_current_admin(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """
    Dépendance FastAPI pour vérifier que l'utilisateur est admin.
    
    Usage:
        @app.get("/admin-only")
        async def admin_route(user: TokenPayload = Depends(get_current_admin)):
            return {"admin": user.sub}
    """
    if "admin" not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    return user


def require_app_access(app_slug: str):
    """
    Crée une dépendance pour vérifier l'accès à une application spécifique.
    
    Usage:
        @app.get("/data")
        async def get_data(user: TokenPayload = Depends(require_app_access("mon-app"))):
            return {"data": "..."}
    """
    async def check_access(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        if "admin" in user.roles:
            return user  # Les admins ont accès à tout
        if app_slug not in user.apps:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Accès non autorisé à l'application '{app_slug}'"
            )
        return user
    return check_access
```

---

## Étape 4 : Intégrer dans les routes existantes

### Option A : Remplacer l'authentification existante

Si le projet a déjà un système d'authentification avec `Depends(get_current_user)`, remplacer l'import :

```python
# AVANT
from app.auth import get_current_user

# APRÈS
from hub_auth import get_current_user, get_current_admin, TokenPayload
```

### Option B : Ajouter l'authentification Hub à des routes existantes

```python
from fastapi import FastAPI, Depends
from hub_auth import get_current_user, get_current_admin, require_app_access, TokenPayload

app = FastAPI()

# Route protégée - tout utilisateur authentifié
@app.get("/employees")
async def list_employees(user: TokenPayload = Depends(get_current_user)):
    # user.sub contient le username
    # user.roles contient ["admin"] ou ["user"]
    return {"message": f"Bienvenue {user.sub}"}

# Route admin uniquement
@app.delete("/employees/{id}")
async def delete_employee(id: int, user: TokenPayload = Depends(get_current_admin)):
    return {"deleted": id}

# Route avec vérification d'accès à l'app
@app.get("/sensitive-data")
async def get_sensitive_data(user: TokenPayload = Depends(require_app_access("salarie"))):
    return {"data": "..."}
```

---

## Étape 5 : Configurer CORS (si frontend séparé)

Si l'API est appelée depuis un frontend, configurer CORS pour accepter le header Authorization :

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production, spécifier les domaines autorisés
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # Inclut Authorization
)
```

---

## Étape 6 : Supprimer l'ancien système d'authentification (optionnel)

Si le projet avait son propre système de login (`/token`, `/login`), vous pouvez :

1. **Supprimer** les routes de login locales
2. **Supprimer** la gestion des utilisateurs locale (si tout est centralisé dans le Hub)
3. **Garder** les routes de login si vous voulez un mode hybride (Hub + auth locale)

---

## Structure finale du projet

```
mon-api/
├── main.py                 # Point d'entrée FastAPI
├── hub_auth.py             # Module d'authentification Hub (nouveau)
├── jwt_public_key.pem      # Clé publique du Hub (nouveau)
├── requirements.txt        # Avec pyjwt et cryptography
└── ...                     # Reste du projet
```

---

## Test de l'intégration

### 1. Obtenir un token depuis le Hub

```bash
curl -X POST "http://localhost:8000/api/auth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"
```

Réponse :
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_at": "2025-12-31T20:00:00"
}
```

### 2. Appeler l'API protégée avec le token

```bash
curl "http://localhost:18087/employees" \
  -H "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## Récupération du token via le frontend

Quand l'utilisateur est redirigé depuis le Hub vers votre application, le token est passé dans l'URL :

```
http://votre-app.local/callback?access_token=eyJhbG...
```

Le frontend doit :
1. Extraire le token de l'URL
2. Le stocker (localStorage ou sessionStorage)
3. L'envoyer dans le header `Authorization: Bearer <token>` à chaque requête API

Exemple JavaScript :
```javascript
// Récupérer le token de l'URL
const params = new URLSearchParams(window.location.search);
const token = params.get('access_token');
if (token) {
    localStorage.setItem('hub_token', token);
    // Nettoyer l'URL
    window.history.replaceState({}, '', window.location.pathname);
}

// Utiliser le token pour les appels API
async function fetchAPI(endpoint) {
    const token = localStorage.getItem('hub_token');
    const response = await fetch(`http://localhost:18087${endpoint}`, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });
    return response.json();
}
```

---

## Informations disponibles dans le token

| Champ | Type | Description |
|-------|------|-------------|
| `sub` | string | Nom d'utilisateur |
| `roles` | array | `["admin"]` ou `["user"]` |
| `apps` | array | Liste des slugs d'applications autorisées |
| `exp` | int | Timestamp d'expiration (Unix) |
| `iat` | int | Timestamp de création (Unix) |

---

## Dépannage

### Erreur "Token invalide"
- Vérifier que la clé publique est bien celle du Hub
- Vérifier que le token n'a pas été modifié

### Erreur "Token expiré"
- Les tokens expirent après 8 heures par défaut
- L'utilisateur doit se reconnecter via le Hub

### Erreur "Clé publique non trouvée"
- Vérifier que `jwt_public_key.pem` existe à la racine ou dans `secrets/`
- Vérifier les permissions de lecture du fichier

---

## Résumé des modifications requises

1. ✅ Ajouter `pyjwt[crypto]` et `cryptography` aux dépendances
2. ✅ Copier `jwt_public_key.pem` du Hub vers le projet
3. ✅ Créer le fichier `hub_auth.py` avec le code fourni
4. ✅ Remplacer/ajouter `Depends(get_current_user)` dans les routes protégées
5. ✅ Configurer CORS si nécessaire
6. ✅ Tester avec un token obtenu du Hub

---

## Contact Hub

- **URL Hub API** : http://localhost:8000
- **URL Hub Frontend** : http://localhost:80
- **Documentation API** : http://localhost:8000/docs
- **Endpoint clé publique** : GET http://localhost:8000/api/auth/public-key
