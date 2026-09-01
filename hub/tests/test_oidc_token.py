# HUB_base/hub/tests/test_oidc_token.py
import base64
import hashlib
from datetime import timedelta

import pytest
import jwt as pyjwt
from httpx import AsyncClient

from app.models import OAuthAuthorizationCode, OAuthClient
from app.auth import get_password_hash, hash_token, utcnow


@pytest.fixture
async def setup_code(async_session, test_user):
    """Crée un client OIDC + un code valide en DB, retourne le raw code."""
    client_obj = OAuthClient(
        client_id="vikunja",
        client_secret_hash=get_password_hash("vike-secret"),
        name="Vikunja",
        redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
        scopes=["openid", "profile", "email"],
    )
    async_session.add(client_obj)
    await async_session.flush()

    raw_code = "raw-code-123"
    code = OAuthAuthorizationCode(
        code_hash=hash_token(raw_code),
        client_id="vikunja",
        user_id=test_user.id,
        redirect_uri="https://vikunja.example.com/auth/openid/auth",
        scope="openid profile email",
        nonce="nonce-xyz",
        expires_at=utcnow() + timedelta(minutes=10),
    )
    async_session.add(code)
    await async_session.commit()
    return raw_code


class TestTokenEndpoint:

    @pytest.mark.asyncio
    async def test_exchange_code_for_tokens(
        self, client: AsyncClient, setup_code
    ):
        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["token_type"] == "Bearer"
        assert "access_token" in data
        assert "id_token" in data
        assert "refresh_token" in data
        assert data["expires_in"] > 0

        # Vérifier l'id_token
        claims = pyjwt.decode(
            data["id_token"],
            options={"verify_signature": False},
            audience="vikunja",
        )
        assert claims["aud"] == "vikunja"
        assert claims["nonce"] == "nonce-xyz"

    @pytest.mark.asyncio
    async def test_code_is_one_shot(
        self, client: AsyncClient, setup_code
    ):
        # Premier échange : OK
        r1 = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert r1.status_code == 200
        # Second échange avec même code : doit échouer
        r2 = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert r2.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_client_secret(self, client: AsyncClient, setup_code):
        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "wrong-secret",
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token_rotation(
        self, client: AsyncClient, setup_code
    ):
        # 1. Échange code → tokens
        r1 = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        rt = r1.json()["refresh_token"]

        # 2. Refresh → nouveaux tokens
        r2 = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert r2.status_code == 200
        assert "access_token" in r2.json()
        assert r2.json()["refresh_token"] != rt  # rotation


class TestTokenEndpointSecurityBranches:
    """Branches de sécurité du /token endpoint — must be test-covered."""

    @pytest.mark.asyncio
    async def test_client_secret_basic_auth(self, client, setup_code):
        """Auth via Authorization: Basic header (au lieu de form body)."""
        creds = base64.b64encode(b"vikunja:vike-secret").decode("ascii")
        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                # PAS de client_secret dans le form — il est dans le header
            },
            headers={"Authorization": f"Basic {creds}"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    @pytest.mark.asyncio
    async def test_expired_code_rejected(self, client, async_session, test_user):
        """Code expiré → 400."""
        c = OAuthClient(
            client_id="vikunja",
            client_secret_hash=get_password_hash("vike-secret"),
            name="Vikunja",
            redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
        )
        async_session.add(c)
        await async_session.flush()
        raw_code = "expired-code-1"
        code = OAuthAuthorizationCode(
            code_hash=hash_token(raw_code),
            client_id="vikunja",
            user_id=test_user.id,
            redirect_uri="https://vikunja.example.com/auth/openid/auth",
            scope="openid",
            expires_at=utcnow() - timedelta(minutes=1),  # EXPIRÉ
        )
        async_session.add(code)
        await async_session.commit()

        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": raw_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_redirect_uri_mismatch(self, client, setup_code):
        """redirect_uri différent de celui stocké dans le code → 400."""
        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": setup_code,
                "redirect_uri": "https://evil.example.com/cb",  # MISMATCH
                "client_id": "vikunja",
                "client_secret": "vike-secret",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_pkce_failure_rejected(self, client, async_session, test_user):
        """PKCE : mauvais code_verifier → 400."""
        # Calculer un challenge S256 valide pour "correct-verifier"
        verifier = "correct-verifier"
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")

        c = OAuthClient(
            client_id="vikunja",
            client_secret_hash=get_password_hash("vike-secret"),
            name="Vikunja",
            redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
        )
        async_session.add(c)
        await async_session.flush()
        raw_code = "pkce-code-1"
        code = OAuthAuthorizationCode(
            code_hash=hash_token(raw_code),
            client_id="vikunja",
            user_id=test_user.id,
            redirect_uri="https://vikunja.example.com/auth/openid/auth",
            scope="openid",
            code_challenge=challenge,
            code_challenge_method="S256",
            expires_at=utcnow() + timedelta(minutes=10),
        )
        async_session.add(code)
        await async_session.commit()

        # Exchange avec le MAUVAIS verifier
        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": raw_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
                "code_verifier": "wrong-verifier",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_pkce_optional_when_no_challenge(
        self, client, async_session, test_user
    ):
        """Code sans code_challenge → exchange réussit sans code_verifier (PKCE optionnel)."""
        c = OAuthClient(
            client_id="vikunja",
            client_secret_hash=get_password_hash("vike-secret"),
            name="Vikunja",
            redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
        )
        async_session.add(c)
        await async_session.flush()
        raw_code = "no-pkce-code-1"
        code = OAuthAuthorizationCode(
            code_hash=hash_token(raw_code),
            client_id="vikunja",
            user_id=test_user.id,
            redirect_uri="https://vikunja.example.com/auth/openid/auth",
            scope="openid",
            # PAS de code_challenge
            expires_at=utcnow() + timedelta(minutes=10),
        )
        async_session.add(code)
        await async_session.commit()

        response = await client.post(
            "/api/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": raw_code,
                "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                "client_id": "vikunja",
                "client_secret": "vike-secret",
                # PAS de code_verifier
            },
        )
        assert response.status_code == 200
        assert "access_token" in response.json()


class TestTokenEndpointNoRateLimit:
    @pytest.mark.asyncio
    async def test_no_rate_limit_on_repeated_attempts(self, client, setup_code):
        """Aucun 429 même après de nombreuses requêtes.

        Le rate-limit IP (10 req/min) a été retiré : en SSO hub-only, l'échange
        code→token est fait server-to-server par le backend de l'app, donc tous
        les utilisateurs partagent la même IP source — le plafond pénalisait le
        trafic légitime (429 « Too Many Requests » côté Vikunja). La sécurité
        repose sur le client secret (client confidentiel), les codes one-shot,
        PKCE et l'expiration courte des codes.
        """
        for i in range(15):
            response = await client.post(
                "/api/oidc/token",
                data={
                    "grant_type": "authorization_code",
                    "code": f"invalid-{i}",
                    "redirect_uri": "https://vikunja.example.com/auth/openid/auth",
                    "client_id": "vikunja",
                    "client_secret": "vike-secret",
                },
            )
            # Échec d'authentification attendu (client/code invalide), jamais 429
            assert response.status_code in (400, 401)
