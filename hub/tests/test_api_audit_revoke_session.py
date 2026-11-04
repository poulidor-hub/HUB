# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pytest
from sqlalchemy import select
from app.models import UserSession, utcnow


@pytest.mark.asyncio
async def test_revoke_single_session(client, async_session, admin_token, test_user):
    now = utcnow()
    s = UserSession(user_id=test_user.id, login_at=now, last_activity_at=now,
                    ip_address="10.0.0.7")
    async_session.add(s)
    await async_session.commit()
    await async_session.refresh(s)

    resp = await client.post(
        f"/api/audit/sessions/{s.id}/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == s.id
    assert body["user_id"] == test_user.id

    refreshed = (await async_session.execute(
        select(UserSession).where(UserSession.id == s.id)
    )).scalar_one()
    assert refreshed.logout_at is not None
    assert refreshed.logout_reason == "forced"


@pytest.mark.asyncio
async def test_revoke_unknown_session_404(client, admin_token):
    resp = await client.post(
        "/api/audit/sessions/999999/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
