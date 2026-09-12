"""Tests de la route POST /api/apps/{slug}/socle-check.

La route mint deux tokens éphémères côté hub (JWT user admin sur l'app +
token service account au périmètre minimal) puis délègue l'exécution des
7 contrôles à app.socle_check.run_socle_check avec l'api_url de la DB.

Ici run_socle_check est remplacé dans le namespace de la route (pas de
réseau) : ce sont les tokens mintés qu'on audite (type, périmètre, TTL
court) et le contrat du rapport retourné.
"""
import jwt as pyjwt
import pytest

from app.auth import create_access_token
from app.config import get_settings
from app.models import Application
from tests.conftest import get_auth_header

SOCLE_TOKEN_TTL_SECONDS = 300  # contrat : tokens éphémères ≤ 5 minutes

NOMINAL_REPORT = {
    "slug": "test-app",
    "hub_version": "0.0.0-test",
    "app_version": "1.2.3",
    "score": "7/7",
    "passed": 7,
    "total": 7,
    "total_ms": 12,
    "date": "2026-09-12T00:00:00+00:00",
    "controls": [],
}


@pytest.fixture
def socle_call(monkeypatch):
    """Remplace run_socle_check dans la route et capture l'appel."""
    captured: dict = {}

    async def fake_run_socle_check(slug, api_url, *, user_token, service_token,
                                   hub_version, transport=None):
        captured.update(slug=slug, api_url=api_url, user_token=user_token,
                        service_token=service_token, hub_version=hub_version,
                        transport=transport)
        return {**NOMINAL_REPORT, "slug": slug, "hub_version": hub_version}

    monkeypatch.setattr("app.routes.app_socle_check.run_socle_check",
                        fake_run_socle_check)
    return captured


def _decode(token: str) -> dict:
    settings = get_settings()
    return pyjwt.decode(token, settings.jwt_public_key,
                        algorithms=[settings.jwt_algorithm])


class TestSocleCheckRoute:

    async def test_404_si_slug_inconnu(self, client, admin_user):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        resp = await client.post("/api/apps/app-inconnue/socle-check",
                                 headers=get_auth_header(token))
        assert resp.status_code == 404

    async def test_403_si_non_admin(self, client, test_user, test_app):
        token, _, _ = create_access_token(username=test_user.username, apps={})
        resp = await client.post(f"/api/apps/{test_app.slug}/socle-check",
                                 headers=get_auth_header(token))
        assert resp.status_code == 403

    async def test_409_si_api_url_absente(self, client, admin_user, async_session):
        app_row = Application(
            name="Sans URL", slug="no-url-app", description="app sans api_url",
            icon="bi-box", color="#000000", api_url="",
            frontend_url="http://localhost:3001",
            callback_url="http://localhost:3001/callback",
            is_active=True,
        )
        async_session.add(app_row)
        await async_session.commit()
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        resp = await client.post("/api/apps/no-url-app/socle-check",
                                 headers=get_auth_header(token))
        assert resp.status_code == 409

    async def test_tokens_ephemeres_et_rapport_nominal(
            self, client, admin_user, test_app, socle_call):
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        resp = await client.post(f"/api/apps/{test_app.slug}/socle-check",
                                 headers=get_auth_header(token))

        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == test_app.slug
        assert body["score"] == "7/7"
        assert body["app_version"] == "1.2.3"
        # Le module reçoit l'api_url de la DB et la version du hub courant
        assert socle_call["api_url"] == test_app.api_url
        assert socle_call["hub_version"]
        assert socle_call["transport"] is None  # réseau réel hors tests

        # JWT user éphémère : type user, admin sur CETTE app uniquement, TTL court
        user_payload = _decode(socle_call["user_token"])
        assert user_payload["type"] == "user"
        assert user_payload["sub"] == admin_user.username
        assert user_payload["apps"] == {test_app.slug: "admin"}
        assert 0 < user_payload["exp"] - user_payload["iat"] <= SOCLE_TOKEN_TTL_SECONDS

        # JWT service éphémère : type service_account, périmètre M2M minimal
        svc_payload = _decode(socle_call["service_token"])
        assert svc_payload["type"] == "service_account"
        assert svc_payload["apps"] == {test_app.slug: "service"}
        assert 0 < svc_payload["exp"] - svc_payload["iat"] <= SOCLE_TOKEN_TTL_SECONDS

    async def test_rapport_en_echec_renvoye_200(self, client, admin_user,
                                                test_app, monkeypatch):
        """Un contrôle qui échoue est documenté dans le rapport, la route reste 200."""
        async def failing_run(slug, api_url, *, user_token, service_token,
                              hub_version, transport=None):
            return {**NOMINAL_REPORT, "slug": slug, "hub_version": hub_version,
                    "score": "4/7", "passed": 4, "app_version": None}

        monkeypatch.setattr("app.routes.app_socle_check.run_socle_check",
                            failing_run)
        token, _, _ = create_access_token(username=admin_user.username, apps={})
        resp = await client.post(f"/api/apps/{test_app.slug}/socle-check",
                                 headers=get_auth_header(token))

        assert resp.status_code == 200
        body = resp.json()
        assert body["score"] == "4/7"
        assert body["passed"] == 4
