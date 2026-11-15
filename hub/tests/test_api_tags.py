# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests d'intégration pour les endpoints API de tags utilisateur
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import create_access_token
from app.models import User, UserTag
from tests.conftest import get_auth_header


def _make_admin_headers(admin_user: User):
    token, _, _ = create_access_token(username=admin_user.username, apps={})
    return get_auth_header(token)


def _make_user_headers(test_user: User):
    token, _, _ = create_access_token(username=test_user.username, apps={})
    return get_auth_header(token)


def _make_sa_headers(service_account):
    """Headers M2M pour /api/tags/user/{username} (get_current_entity)."""
    token, _, _ = create_access_token(
        username=service_account.client_id, apps={}, is_service_account=True
    )
    return get_auth_header(token)


@pytest.fixture(autouse=True)
def _clear_tags_values_cache():
    """Le cache du résolveur live est un dict module-level : vidé entre chaque
    test pour éviter toute fuite (les ids de catégories se répètent entre les
    bases SQLite en mémoire)."""
    from app.routes import tags as tags_module
    tags_module._values_cache.clear()
    yield
    tags_module._values_cache.clear()


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
        assert response.json() == {
            "tags": {}, "primary": {}, "ignored": {},
            "onboarding_reason": None, "seen_categories": [],
        }

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
        assert response.json() == {
            "tags": {"sec": ["Nord"]}, "primary": {}, "ignored": {},
            "onboarding_reason": None, "seen_categories": [],
        }

    @pytest.mark.asyncio
    async def test_update_my_tags_invalid_value(
        self, client: AsyncClient, admin_user: User, test_user: User
    ):
        """Valeur hors snapshot → filtrée dans `ignored` (200), jamais 400 (spec §4.2)."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await client.post("/api/tags/categories", json={
            "slug": "sec", "label": "S", "source": "csv", "values": ["Nord"]
        }, headers=admin_headers)
        response = await client.put("/api/tags/my", json={
            "tags": {"sec": ["Ouest"]}
        }, headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["ignored"] == {"sec": ["Ouest"]}
        assert data["tags"] == {}

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

    @pytest.mark.asyncio
    async def test_login_produces_jwt_with_tags(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        """Login via password grant produces a JWT containing the user's tags."""
        from app.models import TagCategory, UserTag

        # Create a category
        cat = TagCategory(slug="zone", label="Zone", source="csv",
                          is_multiple=True, values=["Nord", "Sud"])
        async_session.add(cat)
        await async_session.commit()
        await async_session.refresh(cat)

        # Assign tags to test_user
        async_session.add(UserTag(user_id=test_user.id, category_id=cat.id, value="Nord"))
        await async_session.commit()

        # Login via password grant
        response = await client.post("/api/auth/token", data={
            "grant_type": "password",
            "username": test_user.username,
            "password": "testpass123",
        })
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Decode JWT and verify tags
        import jwt as pyjwt
        from app.config import get_settings
        settings = get_settings()
        payload = pyjwt.decode(token, settings.jwt_public_key, algorithms=[settings.jwt_algorithm])
        assert "tags" in payload, f"tags claim missing from JWT. Payload keys: {list(payload.keys())}"
        assert payload["tags"] == {"zone": ["Nord"]}

    @pytest.mark.asyncio
    async def test_get_my_tags_includes_seen_categories(
        self, client: AsyncClient, test_user: User
    ):
        headers = _make_user_headers(test_user)
        response = await client.get("/api/tags/my", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["seen_categories"] == []

    @pytest.mark.asyncio
    async def test_seen_categories_includes_acknowledged(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        from app.models import TagCategory, UserTagSeen
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)

        r1 = await client.post("/api/tags/categories", json={
            "slug": "vu", "label": "Déjà vu", "source": "csv", "values": ["A"]
        }, headers=admin_headers)
        cat1_id = r1.json()["id"]
        r2 = await client.post("/api/tags/categories", json={
            "slug": "pasvu", "label": "Pas vu", "source": "csv", "values": ["B"]
        }, headers=admin_headers)

        async_session.add(UserTagSeen(user_id=test_user.id, category_id=cat1_id))
        await async_session.commit()

        response = await client.get("/api/tags/my", headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert "vu" in data["seen_categories"]
        assert "pasvu" not in data["seen_categories"]

    @pytest.mark.asyncio
    async def test_seen_categories_excludes_inactive(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        from app.models import TagCategory, UserTagSeen
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)

        r = await client.post("/api/tags/categories", json={
            "slug": "oldcat", "label": "Old", "source": "csv", "values": ["X"]
        }, headers=admin_headers)
        cat_id = r.json()["id"]

        async_session.add(UserTagSeen(user_id=test_user.id, category_id=cat_id))
        await async_session.commit()

        resp = await client.get("/api/tags/my", headers=user_headers)
        assert "oldcat" in resp.json()["seen_categories"]

        await client.patch("/api/tags/categories/oldcat", json={"is_active": False}, headers=admin_headers)

        resp2 = await client.get("/api/tags/my", headers=user_headers)
        assert "oldcat" not in resp2.json()["seen_categories"]


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


class TestLiveValues:
    """Résolveur _resolve_active_values : live M2M → cache TTL 60 s → snapshot."""

    @pytest.fixture(autouse=True)
    def _reset_stub_state(self):
        """État classe-partagé du stub : remis à zéro pour chaque test
        (le cache du résolveur est vidé par la fixture module-level)."""
        type(self)._StubAsyncClient.calls = 0
        type(self)._StubAsyncClient.response = None
        type(self)._StubAsyncClient.exc = None
        yield

    class _StubResponse:
        def __init__(self, status_code=200, payload=None):
            self.status_code = status_code
            self._payload = payload if payload is not None else []

        def json(self):
            return self._payload

    class _StubAsyncClient:
        """Remplace httpx.AsyncClient (pas de respx dans le projet)."""
        response = None
        exc = None
        calls = 0

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, **kwargs):
            type(self).calls += 1
            if type(self).exc is not None:
                raise type(self).exc
            return type(self).response

    def _patch_httpx(self, monkeypatch):
        monkeypatch.setattr(
            "app.routes.tags.httpx.AsyncClient", self._StubAsyncClient
        )

    async def _make_m2m_category(
        self, async_session, test_app, slug="secteurs", values=None, **kwargs
    ):
        from app.models import TagCategory
        cat = TagCategory(slug=slug, label=slug.title(), source="m2m",
                          source_app_id=test_app.id,
                          values=values if values is not None else [], **kwargs)
        async_session.add(cat)
        await async_session.commit()
        await async_session.refresh(cat)
        return cat

    @pytest.mark.asyncio
    async def test_resolve_live_updates_snapshot(
        self, monkeypatch, async_session, test_app
    ):
        """Source répond → valeurs fraîches retournées + snapshot rafraîchi + 'live'."""
        from app.routes.tags import _resolve_active_values
        from app.models import TagCategory
        from sqlalchemy import select

        cat = await self._make_m2m_category(async_session, test_app, values=["Old"])
        self._patch_httpx(monkeypatch)
        self._StubAsyncClient.response = self._StubResponse(
            200, {"values": ["Nord", "Sud"]}
        )

        values, flag = await _resolve_active_values(async_session, cat)

        assert values == ["Nord", "Sud"]
        assert flag == "live"
        assert self._StubAsyncClient.calls == 1
        result = await async_session.execute(
            select(TagCategory).where(TagCategory.slug == "secteurs")
        )
        assert result.scalar_one().values == ["Nord", "Sud"]

    @pytest.mark.asyncio
    async def test_resolve_timeout_falls_back_to_cache(
        self, monkeypatch, async_session, test_app
    ):
        """Source injoignable → snapshot renvoyé + 'cache'."""
        import httpx
        from app.routes.tags import _resolve_active_values

        cat = await self._make_m2m_category(async_session, test_app, values=["S1", "S2"])
        self._patch_httpx(monkeypatch)
        self._StubAsyncClient.exc = httpx.RequestError("connection refused")

        values, flag = await _resolve_active_values(async_session, cat)

        assert values == ["S1", "S2"]
        assert flag == "cache"
        assert self._StubAsyncClient.calls == 1

    @pytest.mark.asyncio
    async def test_resolve_timeout_negative_cache(
        self, monkeypatch, async_session, test_app
    ):
        """Panne source : deux résolutions rapprochées → un seul fetch
        (negative-cache à TTL court), les deux retournent le snapshot."""
        import httpx
        from app.routes.tags import _resolve_active_values

        cat = await self._make_m2m_category(
            async_session, test_app, values=["S1", "S2"]
        )
        self._patch_httpx(monkeypatch)
        self._StubAsyncClient.exc = httpx.RequestError("connection refused")

        values1, flag1 = await _resolve_active_values(async_session, cat)
        values2, flag2 = await _resolve_active_values(async_session, cat)

        assert values1 == ["S1", "S2"]
        assert values2 == ["S1", "S2"]
        assert flag1 == "cache"
        assert flag2 == "cache"
        assert self._StubAsyncClient.calls == 1

    @pytest.mark.asyncio
    async def test_resolve_ttl_no_second_fetch(
        self, monkeypatch, async_session, test_app
    ):
        """Deux résolutions < 60 s → un seul fetch (cache mémoire)."""
        from app.routes.tags import _resolve_active_values

        cat = await self._make_m2m_category(async_session, test_app, values=[])
        self._patch_httpx(monkeypatch)
        self._StubAsyncClient.response = self._StubResponse(200, ["V1", "V2"])

        values1, flag1 = await _resolve_active_values(async_session, cat)
        values2, flag2 = await _resolve_active_values(async_session, cat)

        assert values1 == ["V1", "V2"]
        assert flag1 == "live"
        assert values2 == ["V1", "V2"]
        assert flag2 == "cache"
        assert self._StubAsyncClient.calls == 1

    @pytest.mark.asyncio
    async def test_resolve_values_dedup(self, monkeypatch, async_session, test_app):
        """Réponse source avec doublons → dédoublonnage ordre-préservé (Review Focus #2)."""
        from app.routes.tags import _resolve_active_values

        cat = await self._make_m2m_category(async_session, test_app, values=[])
        self._patch_httpx(monkeypatch)
        self._StubAsyncClient.response = self._StubResponse(
            200, {"values": ["B", "A", "B", "C", "A"]}
        )

        values, flag = await _resolve_active_values(async_session, cat)

        assert values == ["B", "A", "C"]
        assert flag == "live"


class TestPutMy:
    """PUT /my : upsert diff + secteur principal, valeurs obsolètes ignorées."""

    async def _create_category(
        self, client, admin_headers, slug="secteurs", values=("Nord", "Sud", "Est"),
        primary_enabled=False, is_multiple=True,
    ):
        from app.models import TagCategory
        response = await client.post("/api/tags/categories", json={
            "slug": slug, "label": slug.title(), "source": "csv",
            "is_multiple": is_multiple, "values": list(values),
            "primary_enabled": primary_enabled,
        }, headers=admin_headers)
        assert response.status_code == 201
        result = await client.get("/api/tags/categories", headers=admin_headers)
        return response.json()

    async def _fetch_rows(self, async_session, user_id, slug):
        from app.models import TagCategory, UserTag
        from sqlalchemy import select
        result = await async_session.execute(
            select(UserTag)
            .options(selectinload(UserTag.category))
            .join(UserTag.category)
            .where(UserTag.user_id == user_id, TagCategory.slug == slug)
        )
        return result.scalars().all()

    @pytest.mark.asyncio
    async def test_put_my_upsert_diff_preserves_created_at(
        self, client, async_session, admin_user, test_user
    ):
        """Upsert diff : la ligne inchangée garde id + created_at (fini le delete-all)."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers)

        r1 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}
        }, headers=user_headers)
        assert r1.status_code == 200

        rows1 = await self._fetch_rows(async_session, test_user.id, "secteurs")
        nord1 = next(r for r in rows1 if r.value == "Nord")

        r2 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Est"]}
        }, headers=user_headers)
        assert r2.status_code == 200

        rows2 = await self._fetch_rows(async_session, test_user.id, "secteurs")
        nord2 = next(r for r in rows2 if r.value == "Nord")
        assert r2.json()["tags"]["secteurs"] == ["Nord", "Est"]
        # Ligne inchangée : même ligne physique (id + created_at préservés)
        assert nord2.id == nord1.id
        assert nord2.created_at == nord1.created_at
        # Sud retirée, Est ajoutée
        assert {r.value for r in rows2} == {"Nord", "Est"}

    @pytest.mark.asyncio
    async def test_put_my_unknown_value_ignored_200(
        self, client, async_session, admin_user, test_user
    ):
        """Valeur obsolète (retirée côté source) → 200 + ignored, jamais 400."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers)

        response = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "RenommeeAilleurs"]}
        }, headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["ignored"] == {"secteurs": ["RenommeeAilleurs"]}
        assert data["tags"]["secteurs"] == ["Nord"]

    @pytest.mark.asyncio
    async def test_put_my_primary_on_filtered_value_drops_primary(
        self, client, async_session, admin_user, test_user
    ):
        """Primaire visant une valeur filtrée (absente des valeurs actives) →
        200, ignored rempli, primary={}, aucune ligne is_primary.
        Chaînon direct du Review Focus #3 (gel de contrat — comportement
        vérifié par la review, verrouillé ici)."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        response = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Production"]},
            "primary": {"secteurs": "Production"},
        }, headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["ignored"] == {"secteurs": ["Production"]}
        assert data["primary"] == {}
        assert data["tags"].get("secteurs", []) == []

        rows = await self._fetch_rows(async_session, test_user.id, "secteurs")
        assert rows == []

    @pytest.mark.asyncio
    async def test_put_my_primary_ok(
        self, client, async_session, admin_user, test_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        response = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Sud"}
        }, headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["primary"] == {"secteurs": "Sud"}
        assert data["tags"]["secteurs"] == ["Nord", "Sud"]

        rows = await self._fetch_rows(async_session, test_user.id, "secteurs")
        primaries = [r for r in rows if r.is_primary]
        assert len(primaries) == 1
        assert primaries[0].value == "Sud"

    @pytest.mark.asyncio
    async def test_put_my_primary_not_in_tags_400(
        self, client, admin_user, test_user
    ):
        """Primaire hors tags[slug] du même payload → seul cas 400 métier."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        response = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord"]}, "primary": {"secteurs": "Sud"}
        }, headers=user_headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_put_my_primary_category_not_enabled_400(
        self, client, admin_user, test_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, slug="lang",
                                    values=("fr", "en"), primary_enabled=False)

        response = await client.put("/api/tags/my", json={
            "tags": {"lang": ["fr"]}, "primary": {"lang": "fr"}
        }, headers=user_headers)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_put_my_primary_null_clears(
        self, client, async_session, admin_user, test_user
    ):
        """primary[slug]=null → suppression explicite de la primaire."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        r1 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord"]}, "primary": {"secteurs": "Nord"}
        }, headers=user_headers)
        assert r1.json()["primary"] == {"secteurs": "Nord"}

        r2 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord"]}, "primary": {"secteurs": None}
        }, headers=user_headers)
        assert r2.status_code == 200
        assert r2.json()["primary"] == {}

        rows = await self._fetch_rows(async_session, test_user.id, "secteurs")
        assert all(not r.is_primary for r in rows)

    @pytest.mark.asyncio
    async def test_put_my_primary_resets_existing(
        self, client, async_session, admin_user, test_user
    ):
        """Primaire A puis PUT avec primaire B → une seule ligne is_primary (B).

        Review Focus #5 : deux PUT concurrents ne doivent pas lever
        d'IntegrityError sur l'index partiel (reset avant pose)."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        r1 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Nord"}
        }, headers=user_headers)
        assert r1.status_code == 200

        r2 = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Sud"}
        }, headers=user_headers)
        assert r2.status_code == 200

        rows = await self._fetch_rows(async_session, test_user.id, "secteurs")
        primaries = [r for r in rows if r.is_primary]
        assert len(primaries) == 1
        assert primaries[0].value == "Sud"

    @pytest.mark.asyncio
    async def test_get_my_returns_primary(
        self, client, admin_user, test_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)

        await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Nord"}
        }, headers=user_headers)

        response = await client.get("/api/tags/my", headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["primary"] == {"secteurs": "Nord"}
        assert data["tags"]["secteurs"] == ["Nord", "Sud"]


class TestAdminAndM2M:
    """Admin PUT /user/{username} + M2M enrichi + catégories actives live."""

    @pytest.mark.asyncio
    async def test_admin_put_user_primary(
        self, client, async_session, admin_user, test_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "secteurs", "label": "Secteurs", "source": "csv",
            "is_multiple": True, "values": ["Nord", "Sud"],
            "primary_enabled": True,
        }, headers=admin_headers)

        response = await client.put(
            f"/api/tags/user/{test_user.username}",
            json={"tags": {"secteurs": ["Nord", "Sud"]},
                  "primary": {"secteurs": "Sud"}},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["primary"] == {"secteurs": "Sud"}
        assert data["tags"]["secteurs"] == ["Nord", "Sud"]

        # Le user voit aussi sa primaire
        my = await client.get("/api/tags/my", headers=_make_user_headers(test_user))
        assert my.json()["primary"] == {"secteurs": "Sud"}

    @pytest.mark.asyncio
    async def test_admin_put_user_ignored_values(
        self, client, admin_user, test_user
    ):
        """Admin : mêmes règles que PUT /my — valeurs obsolètes → ignored (200)."""
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "secteurs", "label": "Secteurs", "source": "csv",
            "is_multiple": True, "values": ["Nord", "Sud"],
        }, headers=admin_headers)

        response = await client.put(
            f"/api/tags/user/{test_user.username}",
            json={"tags": {"secteurs": ["Nord", "AncienneValeur"]}},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ignored"] == {"secteurs": ["AncienneValeur"]}
        assert data["tags"]["secteurs"] == ["Nord"]

    @pytest.mark.asyncio
    async def test_m2m_user_endpoint_includes_primary(
        self, client, async_session, admin_user, test_user, service_account
    ):
        """GET /api/tags/user/{username} (M2M) expose primary."""
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "secteurs", "label": "Secteurs", "source": "csv",
            "is_multiple": True, "values": ["Nord", "Sud"],
            "primary_enabled": True,
        }, headers=admin_headers)
        await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Nord"}
        }, headers=_make_user_headers(test_user))

        response = await client.get(
            f"/api/tags/user/{test_user.username}",
            headers=_make_sa_headers(service_account),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tags"] == {"secteurs": ["Nord", "Sud"]}
        assert data["primary"] == {"secteurs": "Nord"}

    @pytest.mark.asyncio
    async def test_active_categories_source_flag_live(
        self, client, async_session, monkeypatch, test_app, test_user
    ):
        """Source M2M répond → source_flag 'live' + valeurs fraîches."""
        from app.models import TagCategory
        cat = TagCategory(slug="secteurs", label="Secteurs", source="m2m",
                          source_app_id=test_app.id, is_multiple=True,
                          values=["Snapshot"])
        async_session.add(cat)
        await async_session.commit()

        monkeypatch.setattr(
            "app.routes.tags.httpx.AsyncClient", TestLiveValues._StubAsyncClient
        )
        TestLiveValues._StubAsyncClient.response = TestLiveValues._StubResponse(
            200, {"values": ["FraicheA", "FraicheB"]}
        )

        response = await client.get(
            "/api/tags/categories/active", headers=_make_user_headers(test_user)
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        entry = data["categories"][0]
        assert entry["source_flag"] == "live"
        assert entry["values"] == ["FraicheA", "FraicheB"]

    @pytest.mark.asyncio
    async def test_active_categories_source_flag_cache(
        self, client, async_session, monkeypatch, test_app, test_user
    ):
        """Source KO → source_flag 'cache' + valeurs du snapshot."""
        import httpx
        from app.models import TagCategory
        cat = TagCategory(slug="secteurs", label="Secteurs", source="m2m",
                          source_app_id=test_app.id, is_multiple=True,
                          values=["Snapshot1", "Snapshot2"])
        async_session.add(cat)
        await async_session.commit()

        monkeypatch.setattr(
            "app.routes.tags.httpx.AsyncClient", TestLiveValues._StubAsyncClient
        )
        TestLiveValues._StubAsyncClient.exc = httpx.RequestError("down")

        response = await client.get(
            "/api/tags/categories/active", headers=_make_user_headers(test_user)
        )
        assert response.status_code == 200
        entry = response.json()["categories"][0]
        assert entry["source_flag"] == "cache"
        assert entry["values"] == ["Snapshot1", "Snapshot2"]

    @pytest.mark.asyncio
    async def test_active_categories_primary_enabled_flag(
        self, client, admin_user, test_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "secteurs", "label": "Secteurs", "source": "csv",
            "is_multiple": True, "values": ["Nord"],
            "primary_enabled": True,
        }, headers=admin_headers)

        response = await client.get(
            "/api/tags/categories/active", headers=_make_user_headers(test_user)
        )
        entry = response.json()["categories"][0]
        assert entry["primary_enabled"] is True
        assert entry["source_flag"] == "csv"

    @pytest.mark.asyncio
    async def test_patch_category_primary_enabled(
        self, client, admin_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "lang", "label": "Langues", "source": "csv",
            "is_multiple": True, "values": ["fr", "en"],
        }, headers=admin_headers)

        response = await client.patch("/api/tags/categories/lang", json={
            "primary_enabled": True
        }, headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["primary_enabled"] is True

        # Bascule retour à False
        response = await client.patch("/api/tags/categories/lang", json={
            "primary_enabled": False
        }, headers=admin_headers)
        assert response.json()["primary_enabled"] is False


class TestOnboardingReason:
    """GET /my?with_reason=1 — raison d'onboarding calculée serveur (spéc §5).

    Priorités : newcat → primary_lost → primary_missing → onboarding → None.
    """

    @pytest.fixture(autouse=True)
    def _clear_values_cache(self):
        from app.routes import tags as tags_module
        tags_module._values_cache.clear()
        type(self)._StubAsyncClient = TestLiveValues._StubAsyncClient
        type(self)._StubAsyncClient.calls = 0
        type(self)._StubAsyncClient.response = None
        type(self)._StubAsyncClient.exc = None
        yield
        tags_module._values_cache.clear()

    async def _create_category(self, client, admin_headers, slug="secteurs",
                               values=("Nord", "Sud"), primary_enabled=False,
                               is_multiple=True):
        response = await client.post("/api/tags/categories", json={
            "slug": slug, "label": slug.title(), "source": "csv",
            "is_multiple": is_multiple, "values": list(values),
            "primary_enabled": primary_enabled,
        }, headers=admin_headers)
        assert response.status_code == 201
        return response.json()

    async def _setup_complete_choice(self, client, async_session, admin_user,
                                     test_user, slug="secteurs",
                                     primary_enabled=True, primary_value="Nord"):
        """Catégorie vue (ack) + tags + primaire valide = choix complet."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, slug=slug,
                                    primary_enabled=primary_enabled)
        await client.post("/api/tags/my/acknowledge", json={
            "categories": [slug]
        }, headers=user_headers)
        payload = {"tags": {slug: ["Nord", "Sud"]}}
        if primary_enabled:
            payload["primary"] = {slug: primary_value}
        r = await client.put("/api/tags/my", json=payload, headers=user_headers)
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_reason_none_when_choice_complete(
        self, client, async_session, admin_user, test_user
    ):
        """Choix fait + primaire valide + rien de nouveau → None (plus jamais de modale)."""
        user_headers = _make_user_headers(test_user)
        await self._setup_complete_choice(client, async_session, admin_user, test_user)

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["onboarding_reason"] is None

    @pytest.mark.asyncio
    async def test_reason_onboarding_no_tags(
        self, client, async_session, admin_user, test_user
    ):
        """Aucune valeur dans une catégorie active (tout vu) → 'onboarding'."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers)
        await client.post("/api/tags/my/acknowledge", json={
            "categories": ["secteurs"]
        }, headers=user_headers)

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.json()["onboarding_reason"] == "onboarding"

    @pytest.mark.asyncio
    async def test_reason_primary_missing(
        self, client, async_session, admin_user, test_user
    ):
        """Catégorie primary_enabled, tags posés, aucune primaire → 'primary_missing'."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers, primary_enabled=True)
        await client.post("/api/tags/my/acknowledge", json={
            "categories": ["secteurs"]
        }, headers=user_headers)
        r = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}
        }, headers=user_headers)
        assert r.status_code == 200

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.json()["onboarding_reason"] == "primary_missing"

    @pytest.mark.asyncio
    async def test_reason_primary_lost_after_source_rename(
        self, client, async_session, monkeypatch, test_app, admin_user, test_user
    ):
        """Primaire retirée des valeurs actives de la source → 'primary_lost'."""
        from app.models import TagCategory
        from sqlalchemy import select

        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        cat = TagCategory(slug="secteurs", label="Secteurs", source="m2m",
                          source_app_id=test_app.id, is_multiple=True,
                          values=["Nord", "Sud"], primary_enabled=True)
        async_session.add(cat)
        await async_session.commit()

        await client.post("/api/tags/my/acknowledge", json={
            "categories": ["secteurs"]
        }, headers=user_headers)
        r = await client.put("/api/tags/my", json={
            "tags": {"secteurs": ["Nord", "Sud"]}, "primary": {"secteurs": "Nord"}
        }, headers=user_headers)
        assert r.status_code == 200

        # La source ne connaît plus « Nord » (renommée côté exemple-app)
        monkeypatch.setattr(
            "app.routes.tags.httpx.AsyncClient",
            TestLiveValues._StubAsyncClient,
        )
        TestLiveValues._StubAsyncClient.response = TestLiveValues._StubResponse(
            200, {"values": ["Sud", "Est"]}
        )

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.json()["onboarding_reason"] == "primary_lost"

    @pytest.mark.asyncio
    async def test_reason_newcat_unseen_category(
        self, client, async_session, admin_user, test_user
    ):
        """Catégorie active jamais vue → 'newcat' (priorité 1)."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers)

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.json()["onboarding_reason"] == "newcat"

    @pytest.mark.asyncio
    async def test_get_my_reason_category_inactive(
        self, client, async_session, admin_user, test_user
    ):
        """Review Focus #1 : catégorie passée inactive → primaire filtrée,
        raison recalculée sans crash."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._setup_complete_choice(client, async_session, admin_user,
                                          test_user, primary_value="Nord")

        await client.patch("/api/tags/categories/secteurs", json={
            "is_active": False
        }, headers=admin_headers)

        response = await client.get("/api/tags/my?with_reason=1", headers=user_headers)
        assert response.status_code == 200
        data = response.json()
        # Primaire filtrée (catégorie inactive) et raison recalculée : plus de
        # tags actifs → 'onboarding', sans erreur.
        assert data["primary"] == {}
        assert data["onboarding_reason"] == "onboarding"

    @pytest.mark.asyncio
    async def test_get_my_without_reason_flag_skips_computation(
        self, client, async_session, admin_user, test_user
    ):
        """Sans with_reason=1 : onboarding_reason absent (null), pas de fetch live."""
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await self._create_category(client, admin_headers)

        response = await client.get("/api/tags/my", headers=user_headers)
        assert response.status_code == 200
        assert response.json()["onboarding_reason"] is None


class TestUserTagsAcknowledge:

    @pytest.mark.asyncio
    async def test_acknowledge_inserts_seen(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        from app.models import UserTagSeen
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        r = await client.post("/api/tags/categories", json={
            "slug": "zone", "label": "Zone", "source": "csv", "values": ["Nord", "Sud"]
        }, headers=admin_headers)
        zone_id = r.json()["id"]

        response = await client.post("/api/tags/my/acknowledge", json={
            "categories": ["zone"]
        }, headers=user_headers)
        assert response.status_code == 200
        assert response.json() == {"acknowledged": ["zone"]}

        result = await async_session.execute(
            select(UserTagSeen).where(UserTagSeen.user_id == test_user.id)
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].category_id == zone_id

    @pytest.mark.asyncio
    async def test_acknowledge_is_idempotent(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_user: User, test_user: User
    ):
        from app.models import UserTagSeen
        admin_headers = _make_admin_headers(admin_user)
        user_headers = _make_user_headers(test_user)
        await client.post("/api/tags/categories", json={
            "slug": "lang", "label": "Langue", "source": "csv", "values": ["fr", "en"]
        }, headers=admin_headers)

        await client.post("/api/tags/my/acknowledge", json={"categories": ["lang"]}, headers=user_headers)
        response = await client.post("/api/tags/my/acknowledge", json={"categories": ["lang"]}, headers=user_headers)
        assert response.status_code == 200
        assert response.json() == {"acknowledged": ["lang"]}

        result = await async_session.execute(
            select(UserTagSeen).where(UserTagSeen.user_id == test_user.id)
        )
        assert len(result.scalars().all()) == 1  # pas de doublon

    @pytest.mark.asyncio
    async def test_acknowledge_ignores_unknown_slugs(
        self, client: AsyncClient, test_user: User
    ):
        headers = _make_user_headers(test_user)
        response = await client.post("/api/tags/my/acknowledge", json={
            "categories": ["introuvable"]
        }, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"acknowledged": []}


class TestCategorySourceUpdate:
    """PATCH catégorie : bascule csv → m2m (fix sync depuis la modale admin)."""

    async def test_update_category_csv_to_m2m(self, client, admin_user, test_app):
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "basculable", "label": "Basculable", "source": "csv",
            "values": ["X"], "primary_enabled": False,
        }, headers=admin_headers)

        resp = await client.patch("/api/tags/categories/basculable", json={
            "source": "m2m", "source_app_id": test_app.id,
            "m2m_endpoint": "/api/hub/data/values?table=secteurs&column=domaine_intervention",
        }, headers=admin_headers)

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "m2m"
        assert data["source_app_id"] == test_app.id

    async def test_update_category_m2m_without_source_app_rejected(
        self, client, admin_user
    ):
        admin_headers = _make_admin_headers(admin_user)
        await client.post("/api/tags/categories", json={
            "slug": "basculable2", "label": "Basculable 2", "source": "csv",
            "values": [],
        }, headers=admin_headers)

        resp = await client.patch("/api/tags/categories/basculable2", json={
            "source": "m2m",
        }, headers=admin_headers)

        assert resp.status_code == 400, resp.text


class TestDiscoverErrorMessages:
    """502 discovery : message actionnable mentionnant l'URL source."""

    async def test_discover_tables_error_mentions_source_url(
        self, monkeypatch, client, admin_user, test_app
    ):
        import httpx
        admin_headers = _make_admin_headers(admin_user)
        stub_type = type("Stub", (), {})
        calls = {"n": 0}

        class _StubClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def get(self, url, **kwargs):
                calls["n"] += 1
                raise httpx.RequestError("All connection attempts failed")

        monkeypatch.setattr("app.routes.tags.httpx.AsyncClient", _StubClient)

        resp = await client.get(
            "/api/tags/discover/tables", params={"app_id": test_app.id},
            headers=admin_headers,
        )

        assert resp.status_code == 502
        assert test_app.api_url in resp.json()["detail"]
        assert "démarrée" in resp.json()["detail"] or "arrêtée" in resp.json()["detail"]
