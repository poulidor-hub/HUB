# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests des endpoints de paramétrage applicatif (app_settings).

Couvre :
- GET /api/settings/{key} : lecture publique (sans auth)
- GET avec clé hors whitelist → 404
- GET avec clé absente de la BDD → 404
- PUT /api/settings/{key} : modification admin (upsert — crée si absent)
- PUT non-admin → 403
- PUT chaîne quelconque acceptée (booléens normalisés, strings tels quels)
- PUT avec valeur numérique → 400
- PUT d'une clé sensible (token, secret, password…) : valeur jamais journalisée
  ni diffusée par SSE ; les clés ordinaires gardent le comportement d'origine
"""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, AppSetting
from app.auth import get_password_hash
from app.routes import settings as settings_routes
from tests.conftest import get_auth_header


# =============================================================================
# Helpers
# =============================================================================

async def ensure_setting(db: AsyncSession, key: str = "account_requests_enabled", value: str = "true"):
    """Insère un setting en base (idempotent)."""
    from sqlalchemy import select
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.commit()


# =============================================================================
# GET /api/settings/{key}
# =============================================================================

class TestGetSetting:
    """Tests de l'endpoint GET /api/settings/{key}"""

    async def test_get_public_setting(self, client: AsyncClient, async_session: AsyncSession):
        """Un setting public est accessible sans authentification."""
        await ensure_setting(async_session)

        resp = await client.get("/api/settings/account_requests_enabled")
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "account_requests_enabled"
        assert data["value"] == "true"

    async def test_get_setting_value_false(self, client: AsyncClient, async_session: AsyncSession):
        """Retourne correctement la valeur 'false'."""
        await ensure_setting(async_session, value="false")

        resp = await client.get("/api/settings/account_requests_enabled")
        assert resp.status_code == 200
        assert resp.json()["value"] == "false"

    async def test_get_unknown_key_returns_404(self, client: AsyncClient):
        """Une clé hors whitelist retourne 404."""
        resp = await client.get("/api/settings/secret_admin_key")
        assert resp.status_code == 404

    async def test_get_missing_row_returns_404(self, client: AsyncClient):
        """Une clé dans la whitelist mais absente de la BDD retourne 404."""
        # Pas d'ensure_setting → la table est vide
        resp = await client.get("/api/settings/account_requests_enabled")
        assert resp.status_code == 404


# =============================================================================
# PUT /api/settings/{key}
# =============================================================================

class TestUpdateSetting:
    """Tests de l'endpoint PUT /api/settings/{key}"""

    async def test_update_setting_admin(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Un admin peut modifier un setting."""
        await ensure_setting(async_session, value="true")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "false"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "false"

        # Vérifier en BDD
        from sqlalchemy import select
        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "account_requests_enabled")
        )
        setting = result.scalar_one()
        assert setting.value == "false"
        assert setting.updated_by_id == admin_user.id

    async def test_update_setting_non_admin_forbidden(
        self,
        client: AsyncClient,
        test_user: User,
        user_token: str,
        async_session: AsyncSession,
    ):
        """Un utilisateur non-admin ne peut pas modifier un setting."""
        await ensure_setting(async_session)

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "false"},
            headers=get_auth_header(user_token),
        )
        assert resp.status_code == 403

    async def test_update_nonexistent_key_creates(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """Modifier une clé inexistante la crée (upsert)."""
        resp = await client.put(
            "/api/settings/new_feature_flag",
            json={"value": "true"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "new_feature_flag"
        assert resp.json()["value"] == "true"

    async def test_update_missing_value_field(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Body sans champ 'value' retourne 400."""
        await ensure_setting(async_session)

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 400

    async def test_update_toggle_back_and_forth(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """On peut basculer true → false → true."""
        await ensure_setting(async_session, value="true")

        r1 = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "false"},
            headers=get_auth_header(admin_token),
        )
        assert r1.json()["value"] == "false"

        r2 = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "true"},
            headers=get_auth_header(admin_token),
        )
        assert r2.json()["value"] == "true"

    async def test_update_with_json_boolean_true(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Un booléen JSON true est normalisé en chaîne 'true'."""
        await ensure_setting(async_session, value="false")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": True},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "true"

    async def test_update_with_json_boolean_false(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Un booléen JSON false est normalisé en chaîne 'false'."""
        await ensure_setting(async_session, value="true")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": False},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "false"

    async def test_update_with_uppercase_string(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Les variantes de casse ('TRUE', 'False') sont normalisées."""
        await ensure_setting(async_session, value="false")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "TRUE"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "true"

    async def test_update_accepts_arbitrary_string(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Les chaînes quelconques sont acceptées (ex: token GitHub)."""
        await ensure_setting(async_session, value="true")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "ghp_exampleToken123"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "ghp_exampleToken123"

    async def test_update_github_token_upsert(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """La sauvegarde d'un token GitHub fonctionne (clé créée si absente)."""
        resp = await client.put(
            "/api/settings/github_token",
            json={"value": "ghp_testTokenXYZ"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "github_token"
        assert resp.json()["value"] == "ghp_testTokenXYZ"

    async def test_update_with_numeric_value_rejected(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Une valeur numérique est rejetée en 400."""
        await ensure_setting(async_session)

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": 1},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 400


# =============================================================================
# PUT /api/settings/{key} — réglages à valeur secrète
# =============================================================================
# La mise à jour de `github_token` journalisait le PAT en clair puis le
# diffusait par SSE à tous les utilisateurs connectés (admin ou non). Une clé
# « sensible » (nom contenant token, secret, password…) est désormais tracée
# sans sa valeur et diffusée sans champ `value`.

# Valeur sentinelle : ne ressemble pas à un vrai secret
SENTINELLE = "SENTINELLE-JETON-TEST-1234"


@pytest.fixture
def broadcasts(monkeypatch):
    """Capture les événements passés à broadcast_event — liste de (type, data).

    update_setting fait un import LOCAL de broadcast_event (dans le try) :
    c'est l'attribut du module app.routes.events qui est résolu à chaque
    appel, la fonction doit donc être remplacée là-bas (un patch côté
    app.routes.settings ne prendrait aucun effet).
    """
    captured = []

    async def fake_broadcast_event(event_type, data=None):
        captured.append((event_type, data))

    monkeypatch.setattr("app.routes.events.broadcast_event", fake_broadcast_event)
    return captured


class TestUpdateSettingSecretValue:
    """PUT d'une clé sensible : la valeur n'est ni journalisée ni diffusée."""

    async def test_secret_value_not_logged(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        caplog,
    ):
        """PUT github_token : la valeur n'apparaît dans aucun enregistrement de journal."""
        with caplog.at_level(logging.INFO, logger="app.routes.settings"):
            resp = await client.put(
                "/api/settings/github_token",
                json={"value": SENTINELLE},
                headers=get_auth_header(admin_token),
            )
        assert resp.status_code == 200

        # La mise à jour reste tracée (clé visible), seule la valeur est masquée
        settings_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "app.routes.settings"
        ]
        assert any("github_token" in message for message in settings_messages)

        for record in caplog.records:
            assert SENTINELLE not in record.getMessage(), (
                f"valeur secrète journalisée par {record.name} : {record.getMessage()}"
            )

    async def test_secret_value_not_broadcast(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        broadcasts,
    ):
        """PUT github_token : l'événement SSE part avec la clé, SANS champ value."""
        resp = await client.put(
            "/api/settings/github_token",
            json={"value": SENTINELLE},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200

        assert len(broadcasts) == 1, "l'événement settings_updated doit être diffusé"
        event_type, data = broadcasts[0]
        assert event_type == "settings_updated"
        assert data == {"key": "github_token"}

    async def test_ordinary_value_still_broadcast(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
        broadcasts,
    ):
        """PUT account_requests_enabled : l'événement SSE garde la valeur.

        Le frontend s'en sert pour le toggle (sse.js, case 'settings_updated').
        """
        await ensure_setting(async_session, value="true")

        resp = await client.put(
            "/api/settings/account_requests_enabled",
            json={"value": "false"},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200

        assert broadcasts == [
            ("settings_updated", {"key": "account_requests_enabled", "value": "false"}),
        ]

    async def test_ordinary_value_still_logged(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
        caplog,
    ):
        """PUT account_requests_enabled : la valeur reste journalisée (inchangé)."""
        await ensure_setting(async_session, value="true")

        with caplog.at_level(logging.INFO, logger="app.routes.settings"):
            resp = await client.put(
                "/api/settings/account_requests_enabled",
                json={"value": "false"},
                headers=get_auth_header(admin_token),
            )
        assert resp.status_code == 200

        settings_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "app.routes.settings"
        ]
        assert any(
            "account_requests_enabled" in message and "false" in message
            for message in settings_messages
        )


class TestIsSecretSettingKey:
    """Détection des clés sensibles : fragment du nom, insensible à la casse."""

    @pytest.mark.parametrize("key", [
        "github_token",
        "GITHUB_TOKEN",
        "smtp_password",
        "Backup_PassPhrase",
        "signing_private_key",
        "openai_api_key",
        "hub_credential",
        "hub_credentials",
    ])
    def test_secret_keys(self, key):
        """Un nom contenant un fragment sensible désigne un réglage secret."""
        assert settings_routes._is_secret_setting_key(key) is True

    @pytest.mark.parametrize("key", [
        "account_requests_enabled",
        "messages_badge_priorities",
        "hub_host",
        "new_feature_flag",
        "",
    ])
    def test_ordinary_keys(self, key):
        """Les autres noms (et le nom vide) ne sont pas des réglages secrets."""
        assert settings_routes._is_secret_setting_key(key) is False
