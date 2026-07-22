# HUB_base/hub/tests/test_oidc_models.py
import pytest
from app.models import OAuthClient, OAuthAuthorizationCode, OAuthRefreshToken
from app.auth import get_password_hash, hash_token


class TestOAuthClientModel:

    @pytest.mark.asyncio
    async def test_create_oauth_client(self, async_session):
        client = OAuthClient(
            client_id="vikunja",
            client_secret_hash=get_password_hash("super-secret"),
            name="Vikunja",
            redirect_uris=["https://vikunja.example.com/auth/openid/auth"],
            scopes=["openid", "profile", "email"],
        )
        async_session.add(client)
        await async_session.commit()
        await async_session.refresh(client)

        assert client.id is not None
        assert client.client_id == "vikunja"
        assert client.is_active is True
        assert client.auto_approve is True
        assert client.created_at is not None

    @pytest.mark.asyncio
    async def test_oauth_client_check_secret(self, async_session):
        client = OAuthClient(
            client_id="test",
            client_secret_hash=get_password_hash("s3cret"),
            name="Test",
        )
        async_session.add(client)
        await async_session.commit()

        assert client.check_client_secret("s3cret") is True
        assert client.check_client_secret("wrong") is False

    @pytest.mark.asyncio
    async def test_oauth_client_check_redirect_uri(self, async_session):
        client = OAuthClient(
            client_id="test",
            client_secret_hash=get_password_hash("x"),
            name="Test",
            redirect_uris=["https://app1.example.com/cb", "https://app2.example.com/cb"],
        )
        async_session.add(client)
        await async_session.commit()

        assert client.check_redirect_uri("https://app1.example.com/cb") is True
        assert client.check_redirect_uri("https://evil.example.com/cb") is False


class TestOAuthAuthorizationCodeModel:

    @pytest.mark.asyncio
    async def test_create_code(self, async_session, test_user):
        # Need a client first
        client = OAuthClient(client_id="t", client_secret_hash="x", name="T")
        async_session.add(client)
        await async_session.flush()

        from datetime import timedelta
        from app.models import utcnow
        code = OAuthAuthorizationCode(
            code_hash=hash_token("the-code"),
            client_id="t",
            user_id=test_user.id,
            redirect_uri="https://app.example.com/cb",
            scope="openid profile",
            expires_at=utcnow() + timedelta(seconds=30),
        )
        async_session.add(code)
        await async_session.commit()

        assert code.id is not None
        assert code.used_at is None
        assert code.is_expired is False  # créé à l'instant


class TestOAuthRefreshTokenModel:

    @pytest.mark.asyncio
    async def test_refresh_token_is_valid(self, async_session, test_user):
        client = OAuthClient(client_id="t", client_secret_hash="x", name="T")
        async_session.add(client)
        await async_session.flush()

        from app.models import utcnow
        from datetime import timedelta
        rt = OAuthRefreshToken(
            token_hash=hash_token("rt-value"),
            client_id="t",
            user_id=test_user.id,
            scope="openid",
            expires_at=utcnow() + timedelta(days=30),
        )
        async_session.add(rt)
        await async_session.commit()

        assert rt.is_valid is True

        rt.revoked_at = utcnow()
        assert rt.is_valid is False
