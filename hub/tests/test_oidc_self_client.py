"""Self-service OIDC : l'app (SA M2M) provisionne son propre client OIDC."""
import pytest
from httpx import AsyncClient

from app.auth import create_access_token, get_password_hash
from app.models import ServiceAccount, service_account_app_access
from tests.conftest import get_auth_header


async def _make_sa_for_app(async_session, app, name="sa_selfclient"):
    """Insère un SA dédié à `app` (une seule application accessible)."""
    sa = ServiceAccount(
        name=name,
        client_id=ServiceAccount.generate_client_id(),
        client_secret_hash=get_password_hash("secret123"),
        is_active=True,
    )
    async_session.add(sa)
    await async_session.flush()
    await async_session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id, application_id=app.id
        )
    )
    await async_session.commit()
    return sa


def _sa_headers(sa, app) -> dict:
    token, _, _ = create_access_token(
        username=sa.client_id,
        apps={app.slug: "service"},
        is_service_account=True,
    )
    return get_auth_header(token)


@pytest.mark.asyncio
async def test_dependency_rejects_user_token(client: AsyncClient):
    """Un JWT utilisateur (non-SA) est rejeté sur /self-client (403)."""
    token, _, _ = create_access_token(username="admin", apps={})
    resp = await client.get("/api/oidc/self-client", headers=get_auth_header(token))
    assert resp.status_code == 403
