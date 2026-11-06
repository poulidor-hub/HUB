# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Rôle par défaut admin Hub = viewer + héritage atelier (spec 2026-11-06).

Règles :
1. Le rôle explicite (user_app_roles) reste toujours gagnant (setdefault).
2. Un admin Hub sans rôle explicite obtient « viewer » sur toute app
   non-système (JWT, /launch, /sso, list_apps) — plus de rôle « admin »
   implicite, plus d'absence de rôle (« aucun »).
3. Un atelier {X}-ws hérite du rôle résolu de l'app de base X :
   - admin : couvert par la règle 2 (aucune barrière d'accès atelier) ;
   - non-admin : uniquement avec l'activation user_workshop_access sur X,
     défaut « user » s'il n'a aucun rôle explicite sur X.
"""
import base64
import json

from app.auth import create_access_token
from app.models import (
    Application,
    User,
    UserAppRole,
    UserWorkshopAccess,
    get_apps_with_roles_and_workshops,
    workshop_role_for_app,
)
from tests.conftest import get_auth_header


def decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split('.')[1]
    payload_b64 += '=' * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


async def _mk_ws_pair(async_session, slug="canari"):
    """Paire base + atelier actif."""
    base = Application(name=slug.capitalize(), slug=slug, api_url="http://x:1")
    ws = Application(name=f"{slug.capitalize()} ws", slug=f"{slug}-ws",
                     api_url="http://x:2", frontend_url="http://x:5200",
                     is_workshop=True)
    async_session.add_all([base, ws])
    await async_session.commit()
    await async_session.refresh(base)
    await async_session.refresh(ws)
    return base, ws


# ── Règle 2 : fallback viewer admin (helper JWT central) ──


async def test_admin_viewer_defaut_sur_app_sans_role(async_session, admin_user, test_app):
    apps = await get_apps_with_roles_and_workshops(admin_user, async_session)
    assert apps["test-app"] == "viewer"


async def test_admin_garde_son_role_explicite(async_session, admin_with_app):
    admin, app = admin_with_app
    apps = await get_apps_with_roles_and_workshops(admin, async_session)
    assert apps[app.slug] == "admin"


async def test_admin_pas_de_fallback_sur_app_systeme(async_session, admin_user):
    sys_app = Application(name="Hub interne", slug="hub-interne",
                          api_url="http://x:9", is_system_app=True)
    async_session.add(sys_app)
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(admin_user, async_session)
    assert "hub-interne" not in apps


# ── Règle 3 : héritage atelier (helper JWT central) ──


async def test_admin_atelier_herite_role_base(async_session, admin_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=admin_user.id, app_id=base.id, role="manager"))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(admin_user, async_session)
    assert apps["canari"] == "manager"
    assert apps["canari-ws"] == "manager"


async def test_admin_atelier_sans_role_nulle_part_viewer(async_session, admin_user):
    base, ws = await _mk_ws_pair(async_session)
    apps = await get_apps_with_roles_and_workshops(admin_user, async_session)
    assert apps["canari"] == "viewer"
    assert apps["canari-ws"] == "viewer"


async def test_admin_role_explicite_atelier_gagne(async_session, admin_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=admin_user.id, app_id=ws.id, role="manager"))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(admin_user, async_session)
    assert apps["canari-ws"] == "manager"   # explicite sur l'atelier : gagne
    assert apps["canari"] == "viewer"       # fallback sur la base


async def test_non_admin_atelier_herite_role_base_avec_acces(async_session, test_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=test_user.id, app_id=base.id, role="manager"))
    async_session.add(UserWorkshopAccess(user_id=test_user.id, app_id=base.id))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(test_user, async_session)
    assert apps["canari-ws"] == "manager"
    assert await workshop_role_for_app(async_session, test_user.id, "canari-ws") == "manager"


async def test_non_admin_atelier_sans_role_base_defaut_user(async_session, test_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserWorkshopAccess(user_id=test_user.id, app_id=base.id))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(test_user, async_session)
    assert apps["canari-ws"] == "user"


async def test_non_admin_atelier_sans_acces_pas_d_heritage(async_session, test_user):
    """Sans activation user_workshop_access, pas d'héritage (droit d'accès requis)."""
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=test_user.id, app_id=base.id, role="manager"))
    await async_session.commit()
    apps = await get_apps_with_roles_and_workshops(test_user, async_session)
    assert "canari-ws" not in apps


# ── API : /launch ──


async def test_launch_admin_sans_role_viewer(async_session, client, admin_user, test_app):
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    r = await client.post(f"/api/auth/apps/{test_app.slug}/launch",
                          headers=get_auth_header(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["role"] == "viewer"
    payload = decode_jwt_payload(data["access_token"])
    assert payload["apps"][test_app.slug] == "viewer"


async def test_launch_admin_atelier_herite_role_base(async_session, client, admin_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=admin_user.id, app_id=base.id, role="manager"))
    await async_session.commit()
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    r = await client.post("/api/auth/apps/canari-ws/launch",
                          headers=get_auth_header(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["role"] == "manager"
    payload = decode_jwt_payload(data["access_token"])
    assert payload["apps"]["canari-ws"] == "manager"


# ── API : /sso ──


async def test_sso_admin_sans_role_viewer(async_session, client, admin_user, test_app):
    response = await client.post(
        "/api/auth/sso",
        data={
            "username": "admin",
            "password": "admin123",
            "app_slug": test_app.slug,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location = response.headers["Location"]
    assert "access_token=" in location
    token = location.split("access_token=")[1].split("&")[0]
    payload = decode_jwt_payload(token)
    assert payload["apps"][test_app.slug] == "viewer"


# ── API : list_apps (affichage dashboard) ──


async def test_list_apps_admin_my_role_viewer_defaut(async_session, client, admin_token, test_app):
    r = await client.get("/api/apps/", headers=get_auth_header(admin_token))
    assert r.status_code == 200
    entries = {a["slug"]: a for a in r.json()}
    assert entries["test-app"]["my_role"] == "viewer"


async def test_list_apps_admin_atelier_my_role_herite(async_session, client, admin_token, admin_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=admin_user.id, app_id=base.id, role="manager"))
    await async_session.commit()
    r = await client.get("/api/apps/", params={"workshop_filter": "all"},
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    entries = {a["slug"]: a for a in r.json()}
    assert entries["canari"]["my_role"] == "manager"
    assert entries["canari-ws"]["my_role"] == "manager"


async def test_list_apps_user_atelier_my_role_herite(async_session, client, test_user):
    base, ws = await _mk_ws_pair(async_session)
    async_session.add(UserAppRole(user_id=test_user.id, app_id=base.id, role="manager"))
    async_session.add(UserWorkshopAccess(user_id=test_user.id, app_id=base.id))
    await async_session.commit()
    token, _, _ = create_access_token(username=test_user.username, apps={})
    r = await client.get("/api/apps/", headers=get_auth_header(token))
    assert r.status_code == 200
    entries = {a["slug"]: a for a in r.json()}
    assert entries["canari-ws"]["my_role"] == "manager"
