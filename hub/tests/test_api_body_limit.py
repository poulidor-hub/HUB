"""
Tests du middleware de limite de taille du body (protection DoS).

Contrat :
- Une limite GLOBALE (settings.max_body_size_mb, défaut 10 MB) s'applique à
  toutes les routes.
- Les uploads de sauvegarde/import bénéficient d'une limite ÉLARGIE
  (settings.max_upload_body_size_mb, défaut 512 MB) :
    * POST /api/admin/restore
    * POST /api/apps/{slug}/backup/import
    * POST /api/apps/{slug}/backup/restore/upload
- Les limites sont lues dans Settings À CHAQUE requête (ajustables via .env
  sans rebuild).

Les tests réduisent les limites via la fixture `settings` pour rester légers :
le body doit dépasser la constante 10 MB d'avant refactor pour prouver que la
route d'upload n'est plus soumise à la limite globale.
"""
import pytest
from httpx import AsyncClient

from app.config import get_settings

GLOBAL_LIMIT_MB = 1
UPLOAD_LIMIT_MB = 25  # > 10 MB : permet de distinguer l'ancienne constante des settings

# Routes éligibles à la limite élargie
UPLOAD_PATHS = [
    "/api/admin/restore",
    "/api/apps/monapp/backup/import",
    "/api/apps/monapp/backup/restore/upload",
]


@pytest.fixture
def tiny_limits(settings):
    """Limites réduites pour tester le mécanisme sans manipuler de gros fichiers."""
    settings.max_body_size_mb = GLOBAL_LIMIT_MB
    settings.max_upload_body_size_mb = UPLOAD_LIMIT_MB
    return settings


def _payload_over_global_limit() -> bytes:
    """Body entre la limite globale réduite et la limite upload (2 MB > 1 MB)."""
    return b"x" * ((GLOBAL_LIMIT_MB + 1) * 1024 * 1024)


def _payload_over_upload_limit() -> bytes:
    """Body au-delà de la limite élargie (26 MB > 25 MB)."""
    return b"x" * ((UPLOAD_LIMIT_MB + 1) * 1024 * 1024)


class TestBodySizeLimitMiddleware:

    @pytest.mark.asyncio
    async def test_body_over_global_limit_rejected(
        self, client: AsyncClient, tiny_limits
    ):
        """Toute route rejette en 413 un body au-delà de la limite globale."""
        response = await client.post("/api/auth/login", content=_payload_over_global_limit())
        assert response.status_code == 413
        assert str(GLOBAL_LIMIT_MB) in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_body_under_global_limit_reaches_routing(
        self, client: AsyncClient, tiny_limits
    ):
        """Un body sous la limite globale n'est pas intercepté (413 absente)."""
        response = await client.post("/api/auth/login", content=b"x")
        assert response.status_code != 413

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", UPLOAD_PATHS)
    async def test_upload_paths_accept_body_over_global_limit(
        self, client: AsyncClient, tiny_limits, path: str
    ):
        """Sur une route de backup/import, un body entre la limite globale et la
        limite élargie n'est PAS rejeté en 413 (il échoue en 401/422 faute
        d'identifiants — jamais en 413)."""
        response = await client.post(path, content=_payload_over_global_limit())
        assert response.status_code != 413

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", UPLOAD_PATHS)
    async def test_upload_paths_reject_body_over_upload_limit(
        self, client: AsyncClient, tiny_limits, path: str
    ):
        """Même élargie, la limite upload s'applique (413 avec le bon message)."""
        response = await client.post(path, content=_payload_over_upload_limit())
        assert response.status_code == 413
        assert str(UPLOAD_LIMIT_MB) in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_non_backup_route_keeps_global_limit(
        self, client: AsyncClient, tiny_limits
    ):
        """Une route métier quelconque ne bénéficie pas de la limite élargie."""
        response = await client.post(
            "/api/tags/categories", content=_payload_over_global_limit()
        )
        assert response.status_code == 413
