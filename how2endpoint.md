# Comment déclarer ses endpoints métier au Hub

> **Version** : 2.6 — 18 février 2026

---

## Pourquoi ?

Le **Discovery Explorer** du Hub permet aux administrateurs d'explorer les APIs de toutes les applications enregistrées. Pour que vos endpoints métier apparaissent dans l'explorateur, votre application doit transmettre sa spécification OpenAPI au Hub.

**Sans déclaration**, le Hub tentera de récupérer votre spec en appelant `/openapi.json` sur votre app (fallback réseau). C'est moins fiable et plus lent qu'un push explicite.

---

## Méthode 1 — Push au démarrage (recommandé)

Votre application pousse sa spec OpenAPI au Hub via `POST /api/apps/me/openapi` lors de son démarrage.

### FastAPI (Python)

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
import httpx

app = FastAPI(title="Mon Application", version="1.0.0")

# ... vos routes ...

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Au démarrage : pousser la spec OpenAPI au Hub
    try:
        spec = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://hub:8000/api/apps/me/openapi",
                json={"openapi_spec": spec},
                headers={"X-Slug": "mon-app"},  # ← votre slug
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"✅ Spec OpenAPI poussée : {data['endpoints_count']} endpoints métier")
            else:
                print(f"⚠️ Erreur push OpenAPI : {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"⚠️ Impossible de pousser la spec OpenAPI : {e}")

    yield  # l'app tourne

app = FastAPI(title="Mon Application", version="1.0.0", lifespan=lifespan)
```

### Requête brute (curl)

```bash
# Générer la spec et la pousser
curl -X POST http://hub:8000/api/apps/me/openapi \
  -H "Content-Type: application/json" \
  -H "X-Slug: mon-app" \
  -d '{"openapi_spec": {"openapi":"3.0.0","info":{"title":"Mon App","version":"1.0"},"paths":{"/api/items":{"get":{"summary":"Liste les items","tags":["Items"]}}}}}'
```

### Réponse

```json
{
  "message": "Spécification OpenAPI enregistrée pour 'mon-app'",
  "app_slug": "mon-app",
  "endpoints_count": 5,
  "filtered_count": 3,
  "updated_at": "2026-02-18T10:30:00"
}
```

| Champ | Description |
|-------|-------------|
| `endpoints_count` | Nombre d'endpoints **métier** conservés |
| `filtered_count` | Nombre d'endpoints **techniques** exclus automatiquement |

---

## Méthode 2 — Lors de l'auto-registration

Si votre application utilise l'auto-registration (`POST /api/apps/register`), vous pouvez inclure la spec OpenAPI directement dans la requête d'enregistrement via le champ optionnel `api_endpoints`.

```python
import httpx
from fastapi.openapi.utils import get_openapi

spec = get_openapi(title=app.title, version=app.version, routes=app.routes)

httpx.post("http://hub:8000/api/apps/register", json={
    "slug": "mon-app",
    "name": "Mon Application",
    "description": "Description de mon app",
    "api_port": 8001,
    "is_shared": False,
    "api_endpoints": spec,  # ← spec OpenAPI complète
})
```

Le Hub filtre automatiquement la spec reçue (voir section suivante).

> **💡 Astuce** : Si vous utilisez déjà l'auto-registration, c'est la méthode la plus simple — un seul appel pour tout déclarer.

---

## Filtrage automatique des endpoints techniques

Le Hub exclut automatiquement les endpoints non-métier pour ne garder que les routes utiles dans l'explorateur Discovery.

### Exclusion par path

Les paths suivants sont filtrés automatiquement :

| Pattern | Exemple |
|---------|---------|
| `/health`, `/healthz` | Health check standard |
| `/ready`, `/readyz`, `/live`, `/livez` | Probes Kubernetes |
| `/ping` | Ping basique |
| `/status` | Statut de l'app |
| `/metrics` | Métriques Prometheus |
| `/openapi.json` | Spec OpenAPI elle-même |
| `/docs`, `/docs/*` | Swagger UI |
| `/redoc`, `/redoc/*` | ReDoc |
| `/swagger*` | Swagger legacy |
| `/api/health`, `/api/ping`, `/api/status` | Versions préfixées |
| `/api/docs*`, `/api/openapi.json`, `/api/redoc*` | Doc préfixée |
| `*/_debug*` | Routes de debug |
| `*/__test*` | Routes de test |

### Exclusion par tag OpenAPI

Les endpoints ayant **uniquement** un des tags suivants sont exclus :

`health` · `healthcheck` · `debug` · `internal` · `test` · `monitoring` · `default`

> **Exemple** : Un endpoint tagué `["health"]` sera exclu. Un endpoint tagué `["Items", "health"]` sera aussi exclu car il contient un tag technique.

### Ce qui est conservé

Tous les autres endpoints sont considérés comme **métier** et apparaissent dans le Discovery Explorer :

```
✅ /api/employees          GET     Liste les employés
✅ /api/employees/{id}     GET     Détail d'un employé
✅ /api/employees          POST    Créer un employé
✅ /api/departments        GET     Liste les départements
❌ /health                 GET     (exclu - technique)
❌ /docs                   GET     (exclu - technique)
❌ /openapi.json           GET     (exclu - technique)
```

---

## Priorité de résolution de la spec

Quand un administrateur explore une app dans le Discovery, le Hub résout la spec dans cet ordre :

```
1. Spec stockée en base  ──→  servie immédiatement (pas de réseau)
   (poussée via /me/openapi ou auto-registration)

2. Fetch live (fallback)  ──→  GET {api_url}/openapi.json
                               GET {api_url}/api/openapi.json
                               GET {api_url}/docs/openapi.json
                               GET {api_url}/swagger.json
```

La spec **stockée** est toujours prioritaire car :
- Pas de latence réseau
- Fonctionne même si l'app est temporairement indisponible
- Déjà filtrée (endpoints techniques exclus)

> **💡 Mise à jour** : Poussez à nouveau la spec via `POST /me/openapi` à chaque redémarrage ou déploiement pour la garder synchronisée.

---

## Bonnes pratiques

### 1. Taguez vos routes

Utilisez des tags OpenAPI significatifs pour que le Discovery regroupe vos endpoints logiquement :

```python
@app.get("/api/employees", tags=["Employés"])
async def list_employees(): ...

@app.get("/api/departments", tags=["Départements"])
async def list_departments(): ...
```

### 2. Ajoutez des descriptions

Les champs `summary` et `description` apparaissent dans l'explorateur :

```python
@app.get("/api/employees/{id}", tags=["Employés"],
    summary="Détail d'un employé",
    description="Retourne toutes les informations d'un employé par son ID")
async def get_employee(id: int): ...
```

### 3. Documentez les paramètres

Les paramètres avec `Query`, `Path` et les modèles Pydantic sont automatiquement inclus dans la spec :

```python
from pydantic import BaseModel, Field

class EmployeeCreate(BaseModel):
    nom: str = Field(..., description="Nom de famille")
    prenom: str = Field(..., description="Prénom")
    matricule: str = Field(..., description="Matricule RH")

@app.post("/api/employees", tags=["Employés"])
async def create_employee(data: EmployeeCreate): ...
```

### 4. Poussez au démarrage

Intégrez le push dans le `lifespan` de votre app pour que la spec soit toujours à jour après un redéploiement.

---

## Contraintes

| Contrainte | Détail |
|------------|--------|
| **Réseau** | Le push `POST /me/openapi` est accessible uniquement depuis le réseau Docker |
| **Identification** | Le header `X-Slug` est obligatoire et doit correspondre au slug enregistré |
| **Format** | La spec doit être une spécification OpenAPI valide (3.0 ou 3.1) en JSON |
| **Taille** | Pas de limite stricte, mais la spec est stockée en JSONB PostgreSQL |

---

## Référence des endpoints

| Endpoint | Méthode | Usage |
|----------|---------|-------|
| `/api/apps/me/openapi` | `POST` | Pousser la spec OpenAPI (header `X-Slug` requis) |
| `/api/apps/register` | `POST` | Auto-registration avec champ optionnel `api_endpoints` |
| `/api/discovery/apps/{slug}/openapi` | `GET` | Lire la spec d'une app (admin, utilisé par le Discovery Explorer) |
