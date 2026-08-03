"""
Tests d'intégration pour les endpoints API de tags utilisateur
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import create_access_token
from app.models import User, UserTag
from tests.conftest import get_auth_header


def _make_admin_headers(admin_user: User):
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return get_auth_header(token)


def _make_user_headers(test_user: User):
    token, _, _ = create_access_token(username=test_user.username, apps={})
    return get_auth_header(token)


class TestTagCategoriesAdmin:

    @pytest.mark.asyncio
    async def test_create_category(self, client: AsyncClient, admin_user: User):
        headers = _make_admin_headers(admin_user)
        response = await client.post("/api/tags/categories", json={
            "slug": "secteur", "label": "Secteur géographique",
            "is_multiple": True, "source": "csv", "values": ["Nord", "Sud", "Est"]
        }, headers=headers)
        assert response.status_code == 201
        data = response.json()
        assert data["slug"] == "secteur"
        assert data["values"] == ["Nord", "Sud", "Est"]

    @pytest.mark.asyncio
    async def test_create_duplicate_slug(self, client: AsyncClient, admin_user: User):
        headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "dup", "label": "First", "source": "csv", "values": ["A"]
        }, headers=headers)
        response = await client.post("/api/tags/categories", json={
            "slug": "dup", "label": "Second", "source": "csv", "values": ["B"]
        }, headers=headers)
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_non_admin_forbidden(self, client: AsyncClient, test_user: User):
        headers = _make_user_headers(test_user)
        response = await client.post("/api/tags/categories", json={
            "slug": "s", "label": "S", "source": "csv", "values": []
        }, headers=headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_categories(self, client: AsyncClient, admin_user: User):
        headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "c1", "label": "C1", "source": "csv", "values": ["A"]
        }, headers=headers)
        await client.post("/api/tags/categories", json={
            "slug": "c2", "label": "C2", "source": "csv", "values": ["B"]
        }, headers=headers)
        response = await client.get("/api/tags/categories", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_update_category(self, client: AsyncClient, admin_user: User):
        headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "up", "label": "Orig", "source": "csv", "values": ["X"]
        }, headers=headers)
        response = await client.patch("/api/tags/categories/up", json={
            "label": "Modifié", "values": ["X", "Y"]
        }, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["label"] == "Modifié"
        assert data["values"] == ["X", "Y"]

    @pytest.mark.asyncio
    async def test_delete_category_cascades(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        headers = _make_admin_headers(admin_user)
        r = await client.post("/api/tags/categories", json={
            "slug": "del", "label": "Del", "source": "csv", "values": ["V"]
        }, headers=headers)
        cat_id = r.json()["id"]
        async_session.add(UserTag(user_id=test_user.id, category_id=cat_id, value="V"))
        await async_session.commit()
        response = await client.delete("/api/tags/categories/del", headers=headers)
        assert response.status_code == 204
        result = await async_session.execute(
            select(UserTag).where(UserTag.user_id == test_user.id)
        )
        assert result.scalar_one_or_none() is None


class TestUserTags:

    @pytest.mark.asyncio
    async def test_get_my_tags_empty(self, client: AsyncClient, test_user: User):
        headers = _make_user_headers(test_user)
        response = await client.get("/api/tags/my", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"tags": {}}

    @pytest.mark.asyncio
    async def test_update_my_tags(
        self, client: AsyncClient, admin_user: User, test_user: User
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await client.post("/api/tags/categories", json={
            "slug": "sec", "label": "Secteur", "source": "csv",
            "is_multiple": True, "values": ["Nord", "Sud"]
        }, headers=admin_headers)
        response = await client.put("/api/tags/my", json={
            "tags": {"sec": ["Nord"]}
        }, headers=user_headers)
        assert response.status_code == 200
        assert response.json() == {"tags": {"sec": ["Nord"]}}

    @pytest.mark.asyncio
    async def test_update_my_tags_invalid_value(
        self, client: AsyncClient, admin_user: User, test_user: User
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await client.post("/api/tags/categories", json={
            "slug": "sec", "label": "S", "source": "csv", "values": ["Nord"]
        }, headers=admin_headers)
        response = await client.put("/api/tags/my", json={
            "tags": {"sec": ["Ouest"]}
        }, headers=user_headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_update_my_tags_single_select_enforcement(
        self, client: AsyncClient, admin_user: User, test_user: User
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await client.post("/api/tags/categories", json={
            "slug": "lang", "label": "Langue", "source": "csv",
            "is_multiple": False, "values": ["fr", "en"]
        }, headers=admin_headers)
        response = await client.put("/api/tags/my", json={
            "tags": {"lang": ["fr", "en"]}
        }, headers=user_headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_my_tags_excludes_inactive(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        r = await client.post("/api/tags/categories", json={
            "slug": "act", "label": "Act", "source": "csv", "values": ["A"]
        }, headers=admin_headers)
        cat_id = r.json()["id"]
        async_session.add(UserTag(user_id=test_user.id, category_id=cat_id, value="A"))
        await async_session.commit()
        response = await client.get("/api/tags/my", headers=user_headers)
        assert "act" in response.json()["tags"]
        await client.patch("/api/tags/categories/act", json={
            "is_active": False
        }, headers=admin_headers)
        response = await client.get("/api/tags/my", headers=user_headers)
        assert "act" not in response.json()["tags"]


class TestJWTTagsClaim:

    @pytest.mark.asyncio
    async def test_jwt_includes_tags(self, admin_user: User, test_user: User):
        from app.config import get_settings
        import jwt as pyjwt
        settings = get_settings()
        token, _, _ = create_access_token(
            username=test_user.username, apps={},
            tags={"secteur": ["Nord"]}
        )
        payload = pyjwt.decode(token, settings.jwt_public_key, algorithms=[settings.jwt_algorithm])
        assert payload.get("tags") == {"secteur": ["Nord"]}

    @pytest.mark.asyncio
    async def test_jwt_no_tags_when_empty(self):
        from app.config import get_settings
        import jwt as pyjwt
        settings = get_settings()
        token, _, _ = create_access_token(username="testuser", apps={})
        payload = pyjwt.decode(token, settings.jwt_public_key, algorithms=[settings.jwt_algorithm])
        assert "tags" not in payload
