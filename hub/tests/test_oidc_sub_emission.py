# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""sub OIDC = oidc_sub (immuable) ; preferred_username = username courant ; email figé."""
import jwt as pyjwt
import pytest

from app.config import get_settings
from app.oidc_helpers import compute_email_claim, create_access_token_jwt, create_id_token

SETTINGS = get_settings()


def test_id_token_sub_is_oidc_sub_and_preferred_username_is_current():
    token = create_id_token(
        sub="jdupont",          # oidc_sub (figé)
        username="jmartin",     # username courant après rename
        client_id="c1", nonce=None, scope="openid profile email", settings=SETTINGS,
        uid="uuid-1", prenom="Julie", nom="Martin",
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "jdupont"
    assert claims["preferred_username"] == "jmartin"
    assert claims["name"] == "Julie Martin"
    assert claims["email"] == "jdupont@hub.internal"  # figé sur oidc_sub


def test_access_token_jwt_sub_is_oidc_sub():
    token = create_access_token_jwt(
        sub="jdupont", client_id="c1", scope="openid", settings=SETTINGS
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "jdupont"


def test_email_claim_frozen_on_oidc_sub():
    assert compute_email_claim("jdupont", SETTINGS) == "jdupont@hub.internal"


@pytest.mark.asyncio
async def test_rename_does_not_change_oidc_sub(client, admin_user, test_user):
    """LE test killer : après rename, oidc_sub — donc le sub OIDC émis — est inchangé."""
    from tests.conftest import get_auth_header
    from app.auth import create_access_token
    from app.database import async_session_maker
    from sqlalchemy import select
    from app.models import User

    # Fixer explicitement l'oidc_sub (la fixture conftest ne le pose pas)
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == test_user.id))
        user_row = result.scalar_one()
        user_row.oidc_sub = "jdupont"
        await session.commit()
    oidc_sub_before = "jdupont"

    admin_token, _, _ = create_access_token(username=admin_user.username, apps={})
    resp = await client.post(
        f"/api/users/{test_user.id}/rename",
        json={"username": "jmartin"},
        headers=get_auth_header(admin_token),
    )
    assert resp.status_code == 200

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == test_user.id))
        user = result.scalar_one()
        assert user.username == "jmartin"
        assert user.oidc_sub == oidc_sub_before  # inchangé par le rename

        token = create_id_token(
            sub=user.oidc_sub, username=user.username,
            client_id="c1", nonce=None, scope="openid", settings=SETTINGS,
        )
    assert pyjwt.decode(token, options={"verify_signature": False})["sub"] == oidc_sub_before
