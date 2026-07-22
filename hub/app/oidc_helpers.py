# HUB_base/hub/app/oidc_helpers.py
"""Helpers OIDC provider : id_token, PKCE, codes, logout_token, discovery metadata.

Approche C (from scratch, pas authlib) : réutilise pyjwt[crypto] + settings.jwt_private_key.
"""
import base64
import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Optional

import jwt as pyjwt

from app.models import utcnow  # réutilise le utcnow existant (naïf UTC)


def compute_email_claim(username: str, settings) -> str:
    """Calcule l'email synthétique selon le template OIDC_EMAIL_TEMPLATE."""
    return settings.oidc_email_template.format(username=username)


def validate_pkce(code_verifier: Optional[str], code_challenge: Optional[str],
                  code_challenge_method: Optional[str]) -> bool:
    """Valide un verifier PKCE contre le challenge stocké. Méthode S256 obligatoire."""
    if code_challenge is None or code_challenge_method is None:
        # Pas de PKCE stocké → on accepte (PKCE optionnel côté nous, mais recommandé)
        return True
    if code_challenge_method != "S256":
        return False
    if not code_verifier:
        return False
    computed = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


def generate_authorization_code() -> str:
    """Génère un code OIDC aléatoire URL-safe."""
    return secrets.token_urlsafe(48)


def _add_claim_if_scope(claims: dict, scope: str, claim_name: str, value):
    """Ajoute un claim seulement si le scope correspondant est demandé."""
    scope_parts = (scope or "").split()
    mapping = {
        "preferred_username": "profile",
        "name": "profile",
        "email": "email",
        "email_verified": "email",
    }
    required_scope = mapping.get(claim_name)
    if required_scope is None or required_scope in scope_parts:
        claims[claim_name] = value


def create_id_token(username: str, client_id: str, nonce: Optional[str],
                    scope: str, settings) -> str:
    """Génère un id_token OIDC JWT signé RS256 avec la clé privée du HUB.

    Claims selon les scopes :
      - openid (toujours) : sub, iss, aud, exp, iat
      - profile : preferred_username, name
      - email : email, email_verified
    """
    now = utcnow()
    expire = now + timedelta(minutes=settings.oidc_id_token_ttl_minutes)

    claims = {
        "iss": settings.oidc_issuer,
        "sub": username,
        "aud": client_id,
        "exp": expire,
        "iat": now,
    }
    if nonce:
        claims["nonce"] = nonce

    # Claims conditionnels selon scopes
    _add_claim_if_scope(claims, scope, "preferred_username", username)
    _add_claim_if_scope(claims, scope, "name", username)
    _add_claim_if_scope(claims, scope, "email", compute_email_claim(username, settings))
    _add_claim_if_scope(claims, scope, "email_verified", True)

    headers = {"kid": "hub-jwt-key-1"}  # même kid que JWKS existant

    return pyjwt.encode(
        claims,
        settings.jwt_private_key,
        algorithm="RS256",
        headers=headers,
    )


def create_access_token_jwt(username: str, client_id: str, scope: str,
                            settings) -> str:
    """Génère un access_token JWT signé RS256 (court TTL, stateless).

    Cet access_token est validé via /userinfo (signature JWKS HUB).
    """
    now = utcnow()
    expire = now + timedelta(minutes=settings.oidc_access_token_ttl_minutes)

    claims = {
        "iss": settings.oidc_issuer,
        "sub": username,
        "aud": client_id,
        "scope": scope,
        "exp": expire,
        "iat": now,
        "token_type": "OIDC_ACCESS",
    }
    headers = {"kid": "hub-jwt-key-1"}
    return pyjwt.encode(
        claims, settings.jwt_private_key, algorithm="RS256", headers=headers
    )


def generate_refresh_token() -> str:
    """Génère un refresh token OIDC opaque (hash SHA256 côté DB)."""
    return secrets.token_urlsafe(48)


def generate_logout_token(client_id: str, sub: Optional[str],
                          sid: Optional[str] = None, settings=None) -> str:
    """Génère un logout_token JWT pour Back-Channel Logout (OIDC Back-Channel 1.0).

    Spécification : claims obligatoires iss, aud, iat, jti, events + (sub OU sid).
    Pas de nonce ni at_hash.
    """
    if sub is None and sid is None:
        raise ValueError("Back-Channel Logout: sub ou sid requis")

    now = utcnow()
    import time
    claims = {
        "iss": settings.oidc_issuer,
        "aud": client_id,
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
    }
    if sub is not None:
        claims["sub"] = sub
    if sid is not None:
        claims["sid"] = sid

    headers = {"kid": "hub-jwt-key-1"}
    return pyjwt.encode(
        claims, settings.jwt_private_key, algorithm="RS256", headers=headers
    )


def build_oidc_metadata(settings) -> dict:
    """Construit le document de discovery /.well-known/openid-configuration."""
    issuer = settings.oidc_issuer.rstrip("/")
    return {
        "issuer": issuer,
        # Note: authorization_endpoint utilise notre shortcut /oidc/launch/{client_id}
        # plutôt que le /authorize standard (différé V2).
        "authorization_endpoint": f"{issuer}/api/oidc/launch/{{client_id}}",
        "token_endpoint": f"{issuer}/api/oidc/token",
        "userinfo_endpoint": f"{issuer}/api/oidc/userinfo",
        "jwks_uri": f"{issuer}/api/auth/.well-known/jwks.json",
        "scopes_supported": ["openid", "profile", "email"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic", "client_secret_post"
        ],
        "claims_supported": [
            "sub", "name", "preferred_username", "email", "email_verified"
        ],
        "backchannel_logout_supported": settings.oidc_backchannel_logout_enabled,
        "backchannel_logout_session_supported": settings.oidc_backchannel_logout_enabled,
    }
