"""
Tests unitaires pour le module d'authentification
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.auth import (
    verify_password,
    get_password_hash,
    hash_token,
    generate_jti,
    create_access_token,
    verify_token,
    create_refresh_token,
    verify_refresh_token,
    is_token_revoked,
    cleanup_expired_tokens
)
from app.models import User, RefreshToken, RevokedToken, utcnow
from app.config import get_settings

settings = get_settings()


class TestPasswordHashing:
    """Tests pour le hashing des mots de passe"""
    
    def test_password_hash_is_different_from_plain(self):
        """Le hash doit être différent du mot de passe en clair"""
        password = "test123"
        hashed = get_password_hash(password)
        assert hashed != password
    
    def test_password_hash_is_unique(self):
        """Chaque hash doit être unique (salt différent)"""
        password = "test123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        assert hash1 != hash2
    
    def test_verify_password_correct(self):
        """Vérification du bon mot de passe"""
        password = "mysecurepassword"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self):
        """Vérification d'un mauvais mot de passe"""
        password = "mysecurepassword"
        hashed = get_password_hash(password)
        assert verify_password("wrongpassword", hashed) is False
    
    def test_hash_token_consistency(self):
        """Le hash d'un token doit être consistant"""
        token = "my_refresh_token_123"
        hash1 = hash_token(token)
        hash2 = hash_token(token)
        assert hash1 == hash2
    
    def test_hash_token_different_for_different_tokens(self):
        """Tokens différents = hashes différents"""
        hash1 = hash_token("token1")
        hash2 = hash_token("token2")
        assert hash1 != hash2


class TestJWTCreation:
    """Tests pour la création de tokens JWT"""
    
    def test_generate_jti_uniqueness(self):
        """Chaque JTI doit être unique"""
        jti1 = generate_jti()
        jti2 = generate_jti()
        assert jti1 != jti2
    
    def test_generate_jti_format(self):
        """Le JTI doit être un hex de 32 caractères"""
        jti = generate_jti()
        assert len(jti) == 32
        assert all(c in '0123456789abcdef' for c in jti)
    
    def test_create_access_token_returns_tuple(self):
        """create_access_token retourne un tuple (token, expiry, jti)"""
        token, expire, jti = create_access_token(
            username="testuser",
            apps={"app1": "user"}
        )
        
        assert isinstance(token, str)
        assert isinstance(expire, datetime)
        assert isinstance(jti, str)
    
    def test_create_access_token_default_expiry(self):
        """Le token expire selon la config par défaut"""
        _, expire, _ = create_access_token(
            username="testuser",
            apps={}
        )
        
        expected_expire = utcnow() + timedelta(
            minutes=settings.access_token_expire_minutes
        )
        # Tolérance de 5 secondes
        assert abs((expire - expected_expire).total_seconds()) < 5
    
    def test_create_access_token_custom_expiry(self):
        """Le token peut avoir une expiration personnalisée"""
        custom_delta = timedelta(hours=2)
        _, expire, _ = create_access_token(
            username="testuser",
            apps={},
            expires_delta=custom_delta
        )
        
        expected_expire = utcnow() + custom_delta
        assert abs((expire - expected_expire).total_seconds()) < 5
    
    def test_create_service_account_token(self):
        """Token pour service account"""
        token, _, _ = create_access_token(
            username="sa_client123",
            apps={"app1": "service"},
            is_service_account=True
        )
        
        payload = verify_token(token)
        assert payload["type"] == "service_account"
        assert payload["sub"] == "sa_client123"


class TestJWTVerification:
    """Tests pour la vérification de tokens JWT"""
    
    def test_verify_valid_token(self):
        """Vérification d'un token valide"""
        token, _, _ = create_access_token(
            username="testuser",
            apps={"app1": "admin"}
        )
        
        payload = verify_token(token)
        
        assert payload["sub"] == "testuser"
        assert payload["apps"] == {"app1": "admin"}
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload
    
    def test_verify_token_with_expired_token(self):
        """Un token expiré doit lever une exception"""
        from fastapi import HTTPException
        
        # Créer un token déjà expiré
        token, _, _ = create_access_token(
            username="testuser",
            apps={},
            expires_delta=timedelta(seconds=-10)  # Déjà expiré
        )
        
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        
        assert exc_info.value.status_code == 401
        assert "expiré" in exc_info.value.detail.lower()
    
    def test_verify_invalid_token(self):
        """Un token invalide doit lever une exception"""
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            verify_token("invalid.token.here")
        
        assert exc_info.value.status_code == 401
    
    def test_token_payload_structure(self):
        """Vérifier la structure complète du payload"""
        token, _, jti = create_access_token(
            username="testuser",
            apps={"app1": "admin", "app2": "user"},
            is_service_account=False
        )
        
        payload = verify_token(token)
        
        assert payload["sub"] == "testuser"
        assert payload["apps"] == {"app1": "admin", "app2": "user"}
        assert payload["jti"] == jti
        assert payload["type"] == "user"


class TestRefreshToken:
    """Tests pour les refresh tokens"""
    
    @pytest.mark.asyncio
    async def test_create_refresh_token(self, async_session, test_user):
        """Création d'un refresh token"""
        token = await create_refresh_token(
            db=async_session,
            user_id=test_user.id,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0"
        )
        
        assert token is not None
        assert len(token) > 50  # Token assez long
    
    @pytest.mark.asyncio
    async def test_verify_valid_refresh_token(self, async_session, test_user):
        """Vérification d'un refresh token valide"""
        token = await create_refresh_token(
            db=async_session,
            user_id=test_user.id
        )
        
        refresh_obj = await verify_refresh_token(async_session, token)
        
        assert refresh_obj is not None
        assert refresh_obj.user_id == test_user.id
        assert refresh_obj.is_valid is True
    
    @pytest.mark.asyncio
    async def test_verify_invalid_refresh_token(self, async_session):
        """Vérification d'un token inexistant"""
        result = await verify_refresh_token(async_session, "invalid_token")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_refresh_token_for_service_account(self, async_session, service_account):
        """Refresh token pour un service account"""
        token = await create_refresh_token(
            db=async_session,
            service_account_id=service_account.id
        )
        
        refresh_obj = await verify_refresh_token(async_session, token)
        
        assert refresh_obj is not None
        assert refresh_obj.service_account_id == service_account.id
        assert refresh_obj.user_id is None


class TestTokenRevocation:
    """Tests pour la révocation de tokens"""
    
    @pytest.mark.asyncio
    async def test_token_not_revoked_by_default(self, async_session):
        """Un JTI non révoqué retourne False"""
        result = await is_token_revoked(async_session, "random_jti_123")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_token_revoked_after_revocation(self, async_session):
        """Un JTI révoqué retourne True"""
        from app.auth import revoke_token
        
        jti = "test_jti_to_revoke"
        expires_at = utcnow() + timedelta(hours=1)
        
        await revoke_token(async_session, jti, expires_at, "test")
        
        result = await is_token_revoked(async_session, jti)
        assert result is True


class TestTokenCleanup:
    """Tests pour le nettoyage des tokens expirés"""
    
    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_tokens(self, async_session, test_user):
        """Le nettoyage supprime les tokens expirés"""
        # Créer un refresh token déjà expiré
        expired_token = RefreshToken(
            token_hash="expired_hash_123",
            user_id=test_user.id,
            expires_at=utcnow() - timedelta(days=1)  # Expiré hier
        )
        async_session.add(expired_token)
        
        # Créer un token révoqué expiré
        revoked = RevokedToken(
            jti="expired_jti",
            expires_at=utcnow() - timedelta(days=1),
            reason="test"
        )
        async_session.add(revoked)
        await async_session.commit()
        
        # Nettoyer
        count = await cleanup_expired_tokens(async_session)
        
        assert count >= 2
    
    @pytest.mark.asyncio
    async def test_cleanup_keeps_valid_tokens(self, async_session, test_user):
        """Le nettoyage garde les tokens valides"""
        # Créer un refresh token valide
        token = await create_refresh_token(
            db=async_session,
            user_id=test_user.id
        )
        
        # Nettoyer
        await cleanup_expired_tokens(async_session)
        
        # Le token doit encore être valide
        refresh_obj = await verify_refresh_token(async_session, token)
        assert refresh_obj is not None


def test_verify_token_rejects_reset_scope_token():
    """P1: un reset token (scope=pwd_reset) ne doit pas servir d'access token."""
    from app.auth import create_reset_token, verify_token
    from fastapi import HTTPException

    reset_token = create_reset_token("alice", "$2b$12$fakehashfor-testing-only")
    with pytest.raises(HTTPException) as exc_info:
        verify_token(reset_token)
    assert exc_info.value.status_code == 401
    assert "invalide" in exc_info.value.detail.lower()


def test_verify_token_still_accepts_user_access_token():
    """Garde-regression: un access token user normal reste accepté."""
    from app.auth import verify_token

    token, _, _ = create_access_token(username="alice", apps={"app": "user"})
    payload = verify_token(token)
    assert payload["sub"] == "alice"
    assert payload.get("scope") is None


class TestRefreshTokenGrace:
    """Période de grâce sur la rotation (course inter-onglets).

    Scénario : 2 onglets partagent le refresh token (localStorage). L'onglet A
    rotate → ancien révoqué. L'onglet B re-présente l'ancien → sans grâce :
    401 → logout → onglet fermé. Avec grâce : re-délivrance du remplaçant.
    """

    @pytest.mark.asyncio
    async def test_rotate_stores_replaced_by_token(self, async_session, test_user):
        from sqlalchemy import select

        from app.auth import rotate_refresh_token

        old = await create_refresh_token(async_session, user_id=test_user.id)
        result = await rotate_refresh_token(async_session, old)
        assert result is not None
        new_token, _new_refresh = result

        res = await async_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(old))
        )
        old_row = res.scalar_one()
        assert old_row.revoked_at is not None
        assert old_row.replaced_by_token == new_token
        # Fenêtre de grâce : le token reste éligible à la re-délivrance
        assert old_row.revoked_at > utcnow() - timedelta(
            seconds=settings.refresh_token_grace_seconds + 1
        )

    @pytest.mark.asyncio
    async def test_reuse_within_grace_redelivers_same_token(self, async_session, test_user):
        from app.auth import rotate_refresh_token

        old = await create_refresh_token(async_session, user_id=test_user.id)
        first = await rotate_refresh_token(async_session, old)
        assert first is not None

        # Onglet B : re-présente l'ancien token pendant la grâce
        second = await rotate_refresh_token(async_session, old)
        assert second is not None
        # Re-délivrance idempotente : le MÊME refresh token que la 1re rotation
        assert second[0] == first[0]

    @pytest.mark.asyncio
    async def test_reuse_after_grace_returns_none(self, async_session, test_user):
        from sqlalchemy import select

        from app.auth import rotate_refresh_token

        old = await create_refresh_token(async_session, user_id=test_user.id)
        assert await rotate_refresh_token(async_session, old) is not None

        # Simuler une réutilisation tardive (> grâce)
        res = await async_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(old))
        )
        old_row = res.scalar_one()
        old_row.revoked_at = utcnow() - timedelta(
            seconds=settings.refresh_token_grace_seconds + 5
        )
        await async_session.commit()

        assert await rotate_refresh_token(async_session, old) is None

    @pytest.mark.asyncio
    async def test_cleanup_nullifies_replaced_by_token(self, async_session, test_user):
        from sqlalchemy import select

        from app.auth import rotate_refresh_token

        old = await create_refresh_token(async_session, user_id=test_user.id)
        assert await rotate_refresh_token(async_session, old) is not None

        # Faire vieillir la révocation au-delà de la grâce
        res = await async_session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(old))
        )
        old_row = res.scalar_one()
        old_row.revoked_at = utcnow() - timedelta(
            seconds=settings.refresh_token_grace_seconds + 5
        )
        await async_session.commit()

        await cleanup_expired_tokens(async_session)

        await async_session.refresh(old_row)
        assert old_row.replaced_by_token is None
