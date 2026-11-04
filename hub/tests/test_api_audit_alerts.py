# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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


@pytest.mark.asyncio
async def test_overview_alerts_error_loop(client, async_session, admin_token):
    """≥ loop_threshold requêtes identiques (méthode+chemin+code+IP) dans la
    fenêtre glissante → alerte `error_loop` (le cas des 422 en boucle)."""
    now = utcnow()
    rows = [ErrorLog(error_code=422, error_type="validation",
                     request_method="POST", request_path="/api/apps/register",
                     ip_address="10.42.0.1",
                     created_at=now - timedelta(seconds=i * 15)) for i in range(12)]
    # Bruit sous le seuil : ne doit PAS générer d'alerte
    rows += [ErrorLog(error_code=500, error_type="internal",
                      request_method="GET", request_path="/api/other",
                      ip_address="10.42.0.2",
                      created_at=now - timedelta(minutes=2)) for _ in range(3)]
    async_session.add_all(rows)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/overview/alerts?loop_threshold=10&loop_window_minutes=15",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    loops = [a for a in resp.json()["alerts"] if a["type"] == "error_loop"]
    assert len(loops) == 1
    loop = loops[0]
    assert loop["severity"] == "danger"
    assert loop["request_method"] == "POST"
    assert loop["request_path"] == "/api/apps/register"
    assert loop["error_code"] == 422
    assert loop["ip_address"] == "10.42.0.1"
    assert loop["occurrences"] == 12
    assert loop["deeplink"]["tab"] == "errors"
    assert loop["deeplink"]["filters"]["code"] == "422"
    assert loop["deeplink"]["filters"]["path"] == "/api/apps/register"


@pytest.mark.asyncio
async def test_error_loop_window_excludes_old_occurrences(client, async_session, admin_token):
    """12 erreurs étalées sur 35 min avec une fenêtre de 15 min : seules les
    dernières tombent dans la fenêtre → sous le seuil, pas d'alerte."""
    now = utcnow()
    rows = [ErrorLog(error_code=422, error_type="validation",
                     request_method="POST", request_path="/api/apps/register",
                     ip_address="10.42.0.1",
                     created_at=now - timedelta(minutes=3 * i)) for i in range(12)]
    async_session.add_all(rows)
    await async_session.commit()

    resp = await client.get(
        "/api/audit/overview/alerts?loop_threshold=10&loop_window_minutes=15",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    loops = [a for a in resp.json()["alerts"] if a["type"] == "error_loop"]
    assert loops == []
