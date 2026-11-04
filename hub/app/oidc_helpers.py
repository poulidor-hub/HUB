# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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

from app import jwt_keyring
from app.models import utcnow  # réutilise le utcnow existant (naïf UTC)


def compute_email_claim(oidc_sub: str, settings) -> str:
    """Calcule l'email synthétique selon le template OIDC_EMAIL_TEMPLATE."""
    return settings.oidc_email_template.format(username=oidc_sub)


def compute_display_name(prenom: str | None, nom: str | None, username: str) -> str:
    """« Prénom Nom » si renseigné, sinon fallback sur le username."""
    full = " ".join(p for p in (prenom, nom) if p)
    return full if full else username


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


def create_id_token(sub: str, username: str, client_id: str, nonce: Optional[str],
                    scope: str, settings,
                    uid: Optional[str] = None,
                    prenom: Optional[str] = None,
                    nom: Optional[str] = None) -> str:
    """Génère un id_token OIDC JWT signé RS256 avec la clé privée du HUB.

    Claims selon les scopes :
      - openid (toujours) : sub (= oidc_sub, immuable), iss, aud, exp, iat, uid (si uuid connu)
      - profile : preferred_username (= username courant), name (« Prénom Nom » si renseigné)
      - email : email (figé sur oidc_sub), email_verified
    """
    now = utcnow()
    expire = now + timedelta(minutes=settings.oidc_id_token_ttl_minutes)

    claims = {
        "iss": settings.oidc_issuer,
        "sub": sub,
        "aud": client_id,
        "exp": expire,
        "iat": now,
    }
    if nonce:
        claims["nonce"] = nonce

    # Claims conditionnels selon scopes
    if uid:
        claims["uid"] = uid
    _add_claim_if_scope(claims, scope, "preferred_username", username)
    _add_claim_if_scope(claims, scope, "name", compute_display_name(prenom, nom, username))
    _add_claim_if_scope(claims, scope, "email", compute_email_claim(sub, settings))
    _add_claim_if_scope(claims, scope, "email_verified", True)

    headers = {"kid": jwt_keyring.get_active_kid()}

    return pyjwt.encode(
        claims,
        jwt_keyring.get_active_private_pem(),
        algorithm="RS256",
        headers=headers,
    )


def create_access_token_jwt(sub: str, client_id: str, scope: str,
                            settings) -> str:
    """Génère un access_token JWT signé RS256 (court TTL, stateless).

    Cet access_token est validé via /userinfo (signature JWKS HUB).
    """
    now = utcnow()
    expire = now + timedelta(minutes=settings.oidc_access_token_ttl_minutes)

    claims = {
        "iss": settings.oidc_issuer,
        "sub": sub,
        "aud": client_id,
        "scope": scope,
        "exp": expire,
        "iat": now,
        "token_type": "OIDC_ACCESS",
    }
    headers = {"kid": jwt_keyring.get_active_kid()}
    return pyjwt.encode(
        claims, jwt_keyring.get_active_private_pem(), algorithm="RS256", headers=headers
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

    headers = {"kid": jwt_keyring.get_active_kid()}
    return pyjwt.encode(
        claims, jwt_keyring.get_active_private_pem(), algorithm="RS256", headers=headers
    )


def build_oidc_metadata(settings) -> dict:
    """Construit le document de discovery /.well-known/openid-configuration."""
    issuer = settings.oidc_issuer.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oidc/authorize",
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


# ============================================================
# Session cookie OIDC (pour /oidc/authorize sans session navigateur)
# ============================================================

def create_oidc_session_cookie(sub: str, settings) -> str:
    """Génère un JWT court (8h) signé RS256 servant de cookie de session OIDC.

    Permet à /oidc/authorize de savoir qui est connecté sans session cookie
    dédiée au HUB (qui utilise JWT localStorage). Posé au login password.
    `sub` est l'oidc_sub immuable de l'user (fallback username pour les
    comptes sans oidc_sub, ex. sessions posées avant le déploiement).
    """
    now = utcnow()
    expire = now + timedelta(hours=8)
    claims = {
        "sub": sub,
        "iat": now,
        "exp": expire,
        "type": "oidc_session",
    }
    headers = {"kid": jwt_keyring.get_active_kid()}
    return pyjwt.encode(
        claims, jwt_keyring.get_active_private_pem(), algorithm="RS256", headers=headers
    )


def decode_oidc_session_cookie(token: str, settings) -> Optional[str]:
    """Décode un cookie de session OIDC. Retourne le sub (oidc_sub) si valide, None sinon.

    Valide signature RS256 (clé publique HUB), type=oidc_session, expiration.
    NB : les cookies posés avant le déploiement d'oidc_sub contiennent
    l'username — la résolution user (resolve_user_by_oidc_sub) gère le
    fallback. Ce helper retourne la valeur brute sans résoudre.
    """
    entry, public_pem = jwt_keyring.select_verification_key(token)
    if public_pem is None:
        return None
    try:
        payload = pyjwt.decode(
            token,
            public_pem,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        if payload.get("type") != "oidc_session":
            return None
        sub = payload.get("sub")
        return sub if sub else None
    except pyjwt.PyJWTError:
        return None


# Aucune dépendance FastAPI au niveau module : on type le Response en duck-typing
# (n'importe quel objet exposant .set_cookie fonctionne, notamment starlette).
def attach_oidc_session_cookie(response, sub: str, request, settings) -> None:
    """Pose le cookie hub_oidc_session sur la response donnée.

    `sub` doit être l'oidc_sub immuable de l'user (les appelants passent
    `user.oidc_sub or user.username` pour les comptes pré-déploiement).

    Centralise les flags du cookie OIDC (httpOnly, samesite=lax, secure selon le
    schéma réel de la requête, TTL 8h, path=/). À appeler :
      - au login password (POST /api/auth/token grant_type=password)
      - à chaque refresh token (POST /api/auth/token grant_type=refresh_token
        ET POST /api/auth/token/refresh)

    Sans re-pose au refresh, le cookie expire (TTL 8h) alors que la session
    dashboard continue via le refresh token (7j) → l'utilisateur reste connecté
    au dashboard mais OIDC le considère déconnecté → erreur 401 "Session HUB
    requise" au lancement des apps OIDC (Vikunja, etc.).
    """
    response.set_cookie(
        key="hub_oidc_session",
        value=create_oidc_session_cookie(sub, settings),
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        max_age=8 * 3600,
        path="/",
    )
