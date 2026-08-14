"""
Tests API — Groupes d'applications (/api/app-groups)
"""
import pytest
from sqlalchemy import select

from app.models import AppGroup, Application
from tests.conftest import get_auth_header


class TestAppGroupsAPI:
    """CRUD admin des groupes d'applications"""

    async def test_create_and_list_group(self, client, admin_token):
        r = await client.post(
            "/api/app-groups/",
            json={"name": "Bureautique", "description": "Outils de bureau", "icon": "bi-collection", "color": "#6f42c1"},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Bureautique"
        assert body["app_count"] == 0

        r = await client.get("/api/app-groups/", headers=get_auth_header(admin_token))
        assert r.status_code == 200
        groups = r.json()
        assert any(g["name"] == "Bureautique" for g in groups)

    async def test_duplicate_name_conflict(self, client, admin_token):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 201
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 409

    async def test_update_group(self, client, admin_token):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        group_id = r.json()["id"]
        r = await client.patch(
            f"/api/app-groups/{group_id}",
            json={"color": "#fd7e14", "icon": "bi-boxes"},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["color"] == "#fd7e14"
        assert r.json()["icon"] == "bi-boxes"

    async def test_update_group_duplicate_name(self, client, admin_token):
        g1 = (await client.post("/api/app-groups/", json={"name": "Groupe A"}, headers=get_auth_header(admin_token))).json()
        g2 = (await client.post("/api/app-groups/", json={"name": "Groupe B"}, headers=get_auth_header(admin_token))).json()
        r = await client.patch(
            f"/api/app-groups/{g2['id']}", json={"name": "Groupe A"}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 409

    async def test_delete_group_ungroups_apps(self, client, admin_token, async_session, test_app):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        group_id = r.json()["id"]
        # Ranger une app dans le groupe
        test_app.group_id = group_id
        async_session.add(test_app)
        await async_session.commit()

        r = await client.delete(f"/api/app-groups/{group_id}", headers=get_auth_header(admin_token))
        assert r.status_code == 204

        await async_session.refresh(test_app)
        assert test_app.group_id is None  # app dégrouppée, pas supprimée

    async def test_delete_unknown_group_404(self, client, admin_token):
        r = await client.delete("/api/app-groups/99999", headers=get_auth_header(admin_token))
        assert r.status_code == 404

    async def test_non_admin_forbidden(self, client, user_token):
        r = await client.post(
            "/api/app-groups/", json={"name": "Interdit"}, headers=get_auth_header(user_token)
        )
        assert r.status_code == 403
        r = await client.get("/api/app-groups/", headers=get_auth_header(user_token))
        assert r.status_code == 403


class TestAppGroupsIntegration:
    """Intégration avec /api/apps/ (hydratation group + PATCH group_id)"""

    async def test_apps_list_hydrates_group(self, client, admin_token, async_session, test_app):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique", "icon": "bi-boxes", "color": "#6f42c1"},
            headers=get_auth_header(admin_token),
        )
        group_id = r.json()["id"]
        await async_session.execute(
            Application.__table__.update().where(Application.id == test_app.id).values(group_id=group_id)
        )
        await async_session.commit()
        async_session.expire_all()  # vider l'identity map (expire_on_commit=False)

        r = await client.get("/api/apps/", headers=get_auth_header(admin_token))
        assert r.status_code == 200
        apps = {a["slug"]: a for a in r.json()}
        assert apps["test-app"]["group"] == {
            "id": group_id, "name": "Bureautique", "icon": "bi-boxes", "color": "#6f42c1"
        }

    async def test_patch_app_group_id(self, client, admin_token, async_session, test_app):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        group_id = r.json()["id"]

        r = await client.patch(
            f"/api/apps/{test_app.id}", json={"group_id": group_id}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 200, r.text

        result = await async_session.execute(
            select(Application.group_id).where(Application.id == test_app.id)
        )
        assert result.scalar_one() == group_id

    async def test_patch_app_unknown_group_404(self, client, admin_token, test_app):
        r = await client.patch(
            f"/api/apps/{test_app.id}", json={"group_id": 99999}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 404

    async def test_patch_app_ungroup(self, client, admin_token, async_session, test_app):
        r = await client.post(
            "/api/app-groups/", json={"name": "Bureautique"}, headers=get_auth_header(admin_token)
        )
        group_id = r.json()["id"]
        await client.patch(
            f"/api/apps/{test_app.id}", json={"group_id": group_id}, headers=get_auth_header(admin_token)
        )
        r = await client.patch(
            f"/api/apps/{test_app.id}", json={"group_id": None}, headers=get_auth_header(admin_token)
        )
        assert r.status_code == 200
        result = await async_session.execute(
            select(Application.group_id).where(Application.id == test_app.id)
        )
        assert result.scalar_one() is None
