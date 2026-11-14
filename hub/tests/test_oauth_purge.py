# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Purge des tables OAuth par `cleanup_expired_tokens` (perf heure de pointe).

Avant la migration 067 : les codes d'autorisation OIDC (TTL 30 s) et les
refresh tokens OAuth n'étaient JAMAIS purgés — croissance non bornée des
tables `oauth_authorization_codes` / `oauth_refresh_tokens`. Garanties :

- un code d'autorisation expiré depuis > 1 h (marge vs TTL 30 s) est
  supprimé ; un code expiré il y a moins d'1 h est conservé ;
- un refresh token OAuth expiré est supprimé ; un token valide (ou sans
  expiration, NULL = pas d'expiration) est conservé — on ne purge JAMAIS
  un token encore utilisable ;
- la signature est inchangée : retour int = total des lignes nettoyées.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth import cleanup_expired_tokens
from app.models import (
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
    utcnow,
)


@pytest.fixture
async def oauth_client(async_session, test_user):
    """Client OIDC parent (FK oauth_refresh_tokens/oauth_authorization_codes)."""
    client = OAuthClient(
        client_id="oc_test_purge",
        client_secret_hash="not-a-real-hash",
        name="Client purge",
        redirect_uris=["https://app.test/callback"],
    )
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)
    return client


async def _count(session, model) -> int:
    result = await session.execute(
        select(model).where(model.client_id == "oc_test_purge")
    )
    return len(result.scalars().all())


class TestOAuthAuthorizationCodePurge:
    @pytest.mark.asyncio
    async def test_removes_code_expired_over_one_hour(
        self, async_session, oauth_client, test_user
    ):
        """Un code expiré depuis > 1 h est purgé (marge vs TTL 30 s)."""
        async_session.add(
            OAuthAuthorizationCode(
                code_hash="oauth_code_expired_over_1h",
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                redirect_uri="https://app.test/callback",
                scope="openid",
                expires_at=utcnow() - timedelta(hours=1, minutes=5),
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthAuthorizationCode) == 0

    @pytest.mark.asyncio
    async def test_keeps_code_recently_expired(self, async_session, oauth_client, test_user):
        """Un code expiré depuis < 1 h (encore dans la marge) est conservé."""
        async_session.add(
            OAuthAuthorizationCode(
                code_hash="oauth_code_expired_10s_ago",
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                redirect_uri="https://app.test/callback",
                scope="openid",
                # Expiré depuis 10 s : dans la marge anti-course (TTL 30 s)
                expires_at=utcnow() - timedelta(seconds=10),
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthAuthorizationCode) == 1

    @pytest.mark.asyncio
    async def test_keeps_valid_code(self, async_session, oauth_client, test_user):
        """Un code non encore expiré (expire dans 10 s) est conservé."""
        async_session.add(
            OAuthAuthorizationCode(
                code_hash="oauth_code_valid_10s_left",
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                redirect_uri="https://app.test/callback",
                scope="openid",
                expires_at=utcnow() + timedelta(seconds=10),
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthAuthorizationCode) == 1


class TestOAuthRefreshTokenPurge:
    @pytest.mark.asyncio
    async def test_removes_expired_oauth_refresh_token(
        self, async_session, oauth_client, test_user
    ):
        """Un refresh token OAuth expiré est purgé."""
        async_session.add(
            OAuthRefreshToken(
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                scope="openid",
                token_hash="expired_oauth_rt",
                expires_at=utcnow() - timedelta(days=1),
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthRefreshToken) == 0

    @pytest.mark.asyncio
    async def test_keeps_valid_oauth_refresh_token(
        self, async_session, oauth_client, test_user
    ):
        """Un refresh token OAuth encore valide n'est JAMAIS purgé."""
        async_session.add(
            OAuthRefreshToken(
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                scope="openid",
                token_hash="valid_oauth_rt",
                expires_at=utcnow() + timedelta(days=7),
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthRefreshToken) == 1

    @pytest.mark.asyncio
    async def test_keeps_token_without_expiry(self, async_session, oauth_client, test_user):
        """expires_at NULL = pas d'expiration : le token doit survivre.

        `DELETE ... WHERE expires_at < now` ne touche pas les NULL (comparaison
        SQL à trois états) — test garde-fou sur la sémantique exacte.
        """
        async_session.add(
            OAuthRefreshToken(
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                scope="openid",
                token_hash="no_expiry_oauth_rt",
                expires_at=None,
            )
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        assert await _count(async_session, OAuthRefreshToken) == 1


class TestSignature:
    @pytest.mark.asyncio
    async def test_returns_int_total(self, async_session, oauth_client, test_user):
        """La signature est inchangée : retour int = total des purges."""
        async_session.add(
            OAuthAuthorizationCode(
                code_hash="oauth_code_total_check",
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                redirect_uri="https://app.test/callback",
                scope="openid",
                expires_at=utcnow() - timedelta(hours=2),
            )
        )
        async_session.add(
            OAuthRefreshToken(
                client_id=oauth_client.client_id,
                user_id=test_user.id,
                scope="openid",
                token_hash="expired_oauth_rt_total",
                expires_at=utcnow() - timedelta(days=2),
            )
        )
        await async_session.commit()

        count = await cleanup_expired_tokens(async_session)

        assert isinstance(count, int)
        assert count >= 2
