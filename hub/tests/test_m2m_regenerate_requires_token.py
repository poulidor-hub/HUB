"""H9: regenerate-secret MUST require a valid M2M token (no IP+header fallback)."""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_regenerate_secret_without_token_returns_401(client: AsyncClient, test_app):
    """No token → 401, even from localhost."""
    response = await client.post(
        "/api/apps/me/regenerate-secret",
        headers={"X-Slug": "test-app"},
    )
    assert response.status_code == 401, f"Expected 401, got {response.status_code}: {response.text}"


async def test_regenerate_secret_with_sa_token_succeeds(
    client: AsyncClient, test_app, service_account
):
    """A valid M2M token from the app's SA MUST succeed."""
    from app.auth import create_access_token
    token, _, _ = create_access_token(
        username=service_account.client_id,
        apps={test_app.slug: "service"},
        is_service_account=True,
    )
    response = await client.post(
        "/api/apps/me/regenerate-secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code != 401, f"Token M2M valide ne doit pas être rejeté: {response.status_code}"
