"""Le refresh token (preuve d'utilisateur actif) recrée la session si perdue."""
import pytest
from sqlalchemy import select

from app.models import UserSession


@pytest.mark.asyncio
async def test_refresh_recreates_closed_session(client, async_session, test_user):
    # 1. Login -> refresh_token
    resp = await client.post("/api/auth/token", data={
        "username": "testuser", "password": "testpass123", "grant_type": "password",
    })
    assert resp.status_code == 200
    refresh_token = resp.json().get("refresh_token")
    assert refresh_token

    # 2. Fermer toutes les sessions (simule la race sse_disconnect)
    result = await async_session.execute(select(UserSession))
    for s in result.scalars().all():
        s.logout_at = s.login_at
        s.logout_reason = "sse_disconnect"
    await async_session.commit()

    # 3. Refresh -> la session doit être recréée
    resp = await client.post("/api/auth/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    })
    assert resp.status_code == 200

    result = await async_session.execute(
        select(UserSession).where(UserSession.logout_at == None)  # noqa: E711
    )
    open_sessions = result.scalars().all()
    assert len(open_sessions) == 1, "Le refresh doit recréer exactement une session ouverte"
