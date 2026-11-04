# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import pytest
from app.models import UserSession, utcnow


@pytest.mark.asyncio
async def test_active_sessions_include_id(client, async_session, admin_token, test_user):
    now = utcnow()
    s = UserSession(user_id=test_user.id, login_at=now, last_activity_at=now,
                    ip_address="10.0.0.5")
    async_session.add(s)
    await async_session.commit()
    await async_session.refresh(s)

    resp = await client.get(
        "/api/audit/sessions/active",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_count"] >= 1
    ids = [row["id"] for row in data["sessions"]]
    assert s.id in ids
