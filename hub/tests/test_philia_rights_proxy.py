"""
Tests du proxy « Droits Philia » (Hub → app philia-proxy).

Transport httpx injecté (MockTransport) : aucun réseau réel.
Fixtures conftest : client, async_session, admin_user, admin_token.
"""
import jwt as pyjwt
import pytest
import httpx
from sqlalchemy import select

from app.models import Application, AuditLog, User
from app.routes import philia_rights as mod

AUTH = None  # posé par chaque test via admin_token


def _register_app(async_session):
    """Enregistre l'app philia-proxy avec une api_url de test."""
    application = Application(
        name="exemple-app", slug="philia-proxy", api_url="http://philia.test:8000",
        is_active=True, requires_auth=True,
    )
    async_session.add(application)
    return async_session.commit()


def _handler(expected_path: str, response_json: dict, seen: list, expected_method: str = "GET"):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == expected_path
        assert request.method == expected_method
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json=response_json)
    return handler


@pytest.mark.asyncio
async def test_get_users_proxies_with_service_token(
        client, async_session, admin_user, admin_token):
    await _register_app(async_session)

    seen: list = []
    mod._http_transport = httpx.MockTransport(_handler(
        "/api/admin/philia-rights/users",
        {"users": [{"philia_user_id": "232157", "hub_username": "jdupont"}]},
        seen))
    try:
        resp = await client.get("/api/philia-rights/users",
                                headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        mod._http_transport = None

    assert resp.status_code == 200
    assert resp.json()["users"][0]["philia_user_id"] == "232157"
    # Le token sortant est un token de SERVICE Hub avec rôle admin sur l'app
    token = seen[0].headers["Authorization"].split(" ", 1)[1]
    payload = pyjwt.decode(token, options={"verify_signature": False})
    assert payload["type"] == "service_account"
    assert payload["sub"] == "hub-m2m"
    assert payload["apps"]["philia-proxy"] == "admin"


@pytest.mark.asyncio
async def test_get_perimeters_passes_ptype(client, async_session, admin_user, admin_token):
    await _register_app(async_session)
    seen: list = []
    mod._http_transport = httpx.MockTransport(_handler(
        "/api/admin/philia-rights/users/232157/perimeters",
        {"type": "mo", "total": 1, "entries": []}, seen))
    try:
        resp = await client.get("/api/philia-rights/users/232157/perimeters?ptype=mo",
                                headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        mod._http_transport = None

    assert resp.status_code == 200
    assert seen[0].url.params["ptype"] == "mo"


@pytest.mark.asyncio
async def test_app_missing_returns_404(client, async_session, admin_user, admin_token):
    resp = await client.get("/api/philia-rights/users",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_app_unreachable_returns_502(client, async_session, admin_user, admin_token):
    await _register_app(async_session)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    mod._http_transport = httpx.MockTransport(boom)
    try:
        resp = await client.get("/api/philia-rights/users",
                                headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        mod._http_transport = None
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_app_5xx_maps_to_502(client, async_session, admin_user, admin_token):
    await _register_app(async_session)

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    mod._http_transport = httpx.MockTransport(fail)
    try:
        resp = await client.get("/api/philia-rights/users",
                                headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        mod._http_transport = None
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_app_4xx_is_forwarded(client, async_session, admin_user, admin_token):
    await _register_app(async_session)

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Rôle insuffisant"})

    mod._http_transport = httpx.MockTransport(forbidden)
    try:
        resp = await client.get("/api/philia-rights/users",
                                headers={"Authorization": f"Bearer {admin_token}"})
    finally:
        mod._http_transport = None
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Rôle insuffisant"


@pytest.mark.asyncio
async def test_delete_right_is_audited(client, async_session, admin_user, admin_token):
    await _register_app(async_session)

    seen: list = []
    mod._http_transport = httpx.MockTransport(_handler(
        "/api/admin/philia-rights/users/232157/perimeters/1964/groups/2",
        {"success": True, "removed_count": 2}, seen, expected_method="DELETE"))
    try:
        resp = await client.request(
            "DELETE", "/api/philia-rights/users/232157/perimeters/1964/groups/2",
            json={"label": "Gestion Association"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    finally:
        mod._http_transport = None

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "removed_count": 2}

    logs = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "philia_right_removed")
    )).scalars().all()
    assert len(logs) == 1
    assert logs[0].user_id == admin_user.id
    assert "1964" in (logs[0].target or "")


@pytest.mark.asyncio
async def test_requires_admin(client, admin_token, async_session):
    """Un token user non-admin est refusé (get_current_admin)."""
    from app.auth import create_access_token
    # token sans user admin connu → get_current_user échoue OU is_admin False
    simple = create_access_token(username="basic", apps={})
    resp = await client.get("/api/philia-rights/users",
                            headers={"Authorization": f"Bearer {simple}"})
    assert resp.status_code in (401, 403)
