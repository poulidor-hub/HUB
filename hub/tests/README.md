# Tests et Testeur d'Application - Hub d'Authentification

## 📋 Vue d'ensemble

Ce dossier contient la suite de tests et le testeur d'application pour valider le fonctionnement du Hub d'authentification.

## 🧪 Tests Pytest

### Structure des tests

```
tests/
├── __init__.py
├── conftest.py          # Fixtures et configuration
├── test_auth.py         # Tests unitaires authentification
├── test_models.py       # Tests des modèles SQLAlchemy
├── test_api_auth.py     # Tests d'intégration API auth
├── test_api_apps.py     # Tests d'intégration API apps
└── test_api_users.py    # Tests d'intégration API users
```

### Exécution des tests

```bash
# Dans le conteneur Docker
docker compose exec hub-api pytest tests/ -v

# Tests spécifiques
docker compose exec hub-api pytest tests/test_auth.py -v

# Avec couverture
docker compose exec hub-api pytest tests/ --cov=app --cov-report=html
```

### Catégories de tests

1. **Tests unitaires** (`test_auth.py`, `test_models.py`)
   - Hashing des mots de passe
   - Création et validation de tokens JWT
   - Refresh tokens
   - Révocation de tokens
   - Modèles de données

2. **Tests d'intégration API** (`test_api_*.py`)
   - Endpoints d'authentification
   - CRUD applications
   - CRUD utilisateurs
   - SSO flow

## 🔍 Testeur d'Application

Le fichier `app_tester.py` est un outil autonome pour valider l'intégration d'une application avec le Hub.

### Utilisation en ligne de commande

```bash
# Test basique
python app_tester.py --hub-url http://localhost:8000 --app-slug mon-app

# Avec credentials personnalisés
python app_tester.py --hub-url http://192.0.2.12:8000 \
    --app-slug exemple-app \
    --username admin \
    --password admin123

# Test M2M (service account)
python app_tester.py --hub-url http://localhost:8000 \
    --app-slug mon-app \
    --client-id sa_xxxx \
    --client-secret secret

# Sortie JSON
python app_tester.py --hub-url http://localhost:8000 --app-slug mon-app --json

# Mode silencieux
python app_tester.py --hub-url http://localhost:8000 --app-slug mon-app --quiet
```

### Tests effectués

| Catégorie | Test | Description |
|-----------|------|-------------|
| **Connectivité** | Hub Connectivity | Vérifie l'accessibilité du Hub |
| | Public Key | Récupère la clé publique RSA |
| **Auth** | Login | Authentification utilisateur |
| | Token Validation | Vérifie la signature JWT |
| | Me Endpoint | Récupère les infos utilisateur |
| | Refresh Token | Renouvellement du token |
| | Introspection | Vérifie l'état du token |
| **Application** | App Info | Récupère les détails de l'app |
| | API Connectivity | Vérifie l'accès à l'API cible |
| | Auth Request | Requête authentifiée |
| **SSO** | SSO Flow | Test du flow de redirection |
| **M2M** | Service Account | Auth machine-to-machine |

### Utilisation programmatique

```python
import asyncio
from app_tester import AppTester

async def test_integration():
    async with AppTester(hub_url="http://localhost:8000") as tester:
        # Tests complets
        results = await tester.run_all_tests(
            app_slug="mon-app",
            username="admin",
            password="admin123"
        )
        
        # Tests individuels
        connectivity = await tester.run_connectivity_tests()
        auth = await tester.run_auth_tests("admin", "admin123")
        app_tests = await tester.run_app_tests("mon-app")
        
        return results

results = asyncio.run(test_integration())
```

### Codes de sortie

- `0` : Tous les tests passent
- `1` : Au moins un test a échoué

## 📊 Interprétation des résultats

| Statut | Signification |
|--------|---------------|
| ✅ PASSED | Test réussi |
| ❌ FAILED | Test échoué - problème à corriger |
| ⚠️ WARNING | Comportement inattendu mais non bloquant |
| ⏭️ SKIPPED | Test ignoré (dépendance manquante) |

## 🔧 Dépendances

```txt
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=4.1.0
httpx>=0.25.0
aiosqlite>=0.19.0
```

## 📝 Ajout de nouveaux tests

### Test unitaire

```python
# tests/test_new_feature.py
import pytest
from app.your_module import your_function

class TestYourFeature:
    def test_basic_case(self):
        result = your_function("input")
        assert result == "expected"
    
    @pytest.mark.asyncio
    async def test_async_case(self, async_session):
        # Test avec base de données
        pass
```

### Test d'intégration API

```python
@pytest.mark.asyncio
async def test_new_endpoint(self, client: AsyncClient, admin_user: User):
    token, _, _ = create_access_token(
        username=admin_user.username,
        roles=["admin"],
        apps={}
    )
    
    response = await client.get(
        "/api/your/endpoint",
        headers={"Authorization": f"Bearer {token}"}
    )
    
    assert response.status_code == 200
```

## 🐛 Résolution des problèmes courants

### Tests qui échouent avec SQLAlchemy async

Utilisez `selectinload` pour charger les relations :

```python
result = await db.execute(
    select(User)
    .options(selectinload(User.app_roles))
    .where(User.id == user_id)
)
```

### Token introspection "inactive"

Normal si le token a été rafraîchi - l'ancien token est révoqué.

### Application non accessible

Vérifiez que l'application cible est démarrée et que l'URL est correcte dans le Hub.
