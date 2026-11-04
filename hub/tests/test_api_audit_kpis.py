# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from datetime import timedelta
import pytest
from app.models import UserSession, AuditLog, ErrorLog, utcnow


@pytest.mark.asyncio
async def test_overview_kpis(client, async_session, admin_token, test_user):
    now = utcnow()
    cur = now - timedelta(days=1)       # période courante (7j)
    prev = now - timedelta(days=10)     # période précédente (7..14j)

    async_session.add_all([
        UserSession(user_id=test_user.id, login_at=now, last_activity_at=now),
        ErrorLog(error_code=500, request_path="/api/x", created_at=cur),
        ErrorLog(error_code=500, request_path="/api/x", created_at=prev),
        ErrorLog(error_code=401, request_path="/api/login", created_at=cur),
        AuditLog(user_id=test_user.id, action="access_app", target="app-a", created_at=cur),
        AuditLog(user_id=test_user.id, action="access_app", target="app-b", created_at=cur),
        AuditLog(user_id=test_user.id, action="access_app", target="app-a", created_at=prev),
    ])
    await async_session.commit()

    resp = await client.get(
        "/api/audit/overview/kpis?days=7",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["active_now"] >= 1
    assert d["errors_critical"] == {"current": 1, "previous": 1}
    assert d["auth_failures"] == {"current": 1, "previous": 0}
    assert d["apps_active"] == {"current": 2, "previous": 1}
