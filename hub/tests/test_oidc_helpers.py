# HUB_base/hub/tests/test_oidc_helpers.py
import pytest
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from app.oidc_helpers import (
    create_id_token,
    validate_pkce,
    generate_authorization_code,
    generate_logout_token,
    build_oidc_metadata,
    compute_email_claim,
)


class TestComputeEmailClaim:

    def test_template_substitution(self, settings):
        settings.oidc_email_template = "{username}@hub.internal"
        assert compute_email_claim("alice", settings) == "alice@hub.internal"

    def test_template_without_placeholder(self, settings):
        settings.oidc_email_template = "noreply@example.com"
        assert compute_email_claim("alice", settings) == "noreply@example.com"


class TestPkce:

    def test_s256_valid(self):
        import hashlib, base64, secrets
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert validate_pkce(verifier, challenge, "S256") is True

    def test_s256_invalid(self):
        assert validate_pkce("wrong-verifier", "different-challenge", "S256") is False

    def test_plain_method_not_supported(self):
        assert validate_pkce("v", "v", "plain") is False

    def test_no_challenge_skipped(self):
        # Si pas de code_challenge stocké, on accepte (PKCE optionnel côté nous)
        assert validate_pkce("verifier", None, None) is True


class TestCreateIdToken:

    def test_id_token_signed_with_hub_key(self, settings):
        settings.oidc_issuer = "https://hub.example.com"
        settings.oidc_id_token_ttl_minutes = 10
        token = create_id_token(
            username="alice",
            client_id="vikunja",
            nonce="nonce-123",
            scope="openid profile email",
            settings=settings,
        )
        # Décoder sans vérifier la signature (test de structure)
        claims = pyjwt.decode(
            token, options={"verify_signature": False}, audience="vikunja"
        )
        assert claims["iss"] == "https://hub.example.com"
        assert claims["sub"] == "alice"
        assert claims["aud"] == "vikunja"
        assert claims["nonce"] == "nonce-123"
        assert claims["email"] == "alice@hub.internal"
        assert claims["email_verified"] is True
        assert claims["preferred_username"] == "alice"
        assert claims["name"] == "alice"

    def test_id_token_has_correct_header_kid(self, settings):
        settings.oidc_issuer = "https://hub.example.com"
        token = create_id_token("bob", "client", None, "openid", settings)
        unverified_header = pyjwt.get_unverified_header(token)
        assert unverified_header["kid"] == "hub-jwt-key-1"
        assert unverified_header["alg"] == "RS256"

    def test_id_token_signature_validates_with_public_key(self, settings):
        settings.oidc_issuer = "https://hub.example.com"
        token = create_id_token("bob", "client", None, "openid", settings)
        claims = pyjwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience="client",
        )
        assert claims["sub"] == "bob"


class TestGenerateAuthorizationCode:

    def test_code_is_urlsafe_random(self):
        c1 = generate_authorization_code()
        c2 = generate_authorization_code()
        assert c1 != c2
        assert len(c1) >= 40


class TestGenerateLogoutToken:

    def test_logout_token_structure(self, settings):
        settings.oidc_issuer = "https://hub.example.com"
        token = generate_logout_token(
            client_id="vikunja",
            sub="alice",
            settings=settings,
        )
        claims = pyjwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience="vikunja",
        )
        assert claims["iss"] == "https://hub.example.com"
        assert claims["aud"] == "vikunja"
        assert claims["sub"] == "alice"
        assert "jti" in claims
        assert "iat" in claims
        assert claims["events"] == {
            "http://schemas.openid.net/event/backchannel-logout": {}
        }
        assert "nonce" not in claims


class TestBuildOidcMetadata:

    def test_metadata_contains_required_fields(self, settings):
        settings.oidc_issuer = "https://hub.example.com"
        meta = build_oidc_metadata(settings)
        assert meta["issuer"] == "https://hub.example.com"
        assert meta["authorization_endpoint"] == "https://hub.example.com/api/oidc/launch/{client_id}"
        assert meta["token_endpoint"] == "https://hub.example.com/api/oidc/token"
        assert meta["userinfo_endpoint"] == "https://hub.example.com/api/oidc/userinfo"
        assert meta["jwks_uri"] == "https://hub.example.com/api/auth/.well-known/jwks.json"
        assert "RS256" in meta["id_token_signing_alg_values_supported"]
        assert "code" in meta["response_types_supported"]
        assert "S256" in meta["code_challenge_methods_supported"]
        assert meta["backchannel_logout_supported"] is True
