from datetime import timedelta
import pytest
from app.models import ErrorLog, utcnow


@pytest.mark.asyncio
async def test_overview_alerts(client, async_session, admin_token):
    now = utcnow()
    rows = []
    for _ in range(3):
        rows.append(ErrorLog(error_code=500, error_type="internal",
                             request_path="/api/account-requests",
                             source_app_slug="hub", created_at=now - timedelta(minutes=5)))
    for _ in range(6):
        rows.append(ErrorLog(error_code=401, error_type="authentication",
                             request_path="/api/auth/token",
                             ip_address="192.0.2.12", created_at=now - timedelta(minutes=10)))
    rows.append(ErrorLog(error_code=403, request_path="/api/x",
                         ip_address="10.0.0.9", created_at=now))
    async_session.add_all(rows)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/overview/alerts?days=7&auth_threshold=5",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    alerts = resp.json()["alerts"]

    http = [a for a in alerts if a["type"] == "http_error"]
    assert any(a["request_path"] == "/api/account-requests" and a["occurrences"] == 3 for a in http)

    auth = [a for a in alerts if a["type"] == "auth_failures"]
    assert any(a["ip_address"] == "192.0.2.12" and a["occurrences"] == 6 for a in auth)
    assert all(a["ip_address"] != "10.0.0.9" for a in auth)
