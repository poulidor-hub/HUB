# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

from datetime import timedelta
import pytest
from app.models import AuditLog, utcnow


@pytest.mark.asyncio
async def test_audit_logs_filter_by_ip(client, async_session, admin_token, test_user):
    now = utcnow()
    async_session.add_all([
        AuditLog(user_id=test_user.id, action="login", target="hub",
                 ip_address="10.0.0.1", created_at=now - timedelta(hours=1)),
        AuditLog(user_id=test_user.id, action="login", target="hub",
                 ip_address="10.0.0.2", created_at=now - timedelta(hours=2)),
    ])
    await async_session.commit()

    resp = await client.get(
        "/api/audit/logs?days=7&ip=10.0.0.1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["logs"][0]["ip_address"] == "10.0.0.1"
