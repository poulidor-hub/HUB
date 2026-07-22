"""OIDC Provider pour le HUB POULIDOR (bridge OIDC pour apps externes).

Approche C : implémentation from-scratch avec pyjwt[crypto] déjà présent.
Réutilise settings.jwt_private_key, JWKS endpoint, hash_token(), get_password_hash().
"""
from fastapi import APIRouter

from app.config import get_settings
from app.oidc_helpers import build_oidc_metadata

router = APIRouter(tags=["OIDC"])
settings = get_settings()


@router.get("/.well-known/openid-configuration")
async def openid_configuration():
    """Document de discovery OIDC standard (public)."""
    return build_oidc_metadata(settings)
