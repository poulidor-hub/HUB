import time
import pyotp
import pytest
from httpx import AsyncClient

from app.auth import verify_password
from tests.conftest import get_auth_header

pytestmark = pytest.mark.asyncio


async def _enrol(client: AsyncClient, token: str) -> str:
    """Enrôle l'utilisateur et retourne le secret TOTP."""
    resp = await client.post("/api/auth/totp/setup", headers=get_auth_header(token))
    secret = resp.json()["secret"]
    # Confirmer avec le code de la fenêtre courante (consomme ce pas de temps).
    await client.post(
        "/api/auth/totp/confirm", headers=get_auth_header(token),
        json={"code": pyotp.TOTP(secret).now()},
    )
    return secret


def _recovery_code(secret: str) -> str:
    """Code de la fenêtre suivante : son pas de temps est > au pas consommé à la confirmation.

    Évite le rejet anti-rejeu lorsque confirmation et récupération ont lieu
    dans la même fenêtre de 30 s (cas des tests qui s'exécutent en quelques ms).
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


async def test_recover_full_flow_changes_password(client, test_user, user_token, async_session):
    secret = await _enrol(client, user_token)
    verify = await client.post(
        "/api/auth/recover/verify",
        json={"username": "testuser", "totp_code": _recovery_code(secret)},
    )
    assert verify.status_code == 200
    reset_token = verify.json()["reset_token"]

    reset = await client.post(
        "/api/auth/recover/reset",
        json={"reset_token": reset_token, "new_password": "brandnewpass123"},
    )
    assert reset.status_code == 200
    await async_session.refresh(test_user)
    assert verify_password("brandnewpass123", test_user.hashed_password)


async def test_recover_verify_bad_code_is_generic_401(client, test_user, user_token):
    await _enrol(client, user_token)
    resp = await client.post(
        "/api/auth/recover/verify",
        json={"username": "testuser", "totp_code": "000000"},
    )
    assert resp.status_code == 401


async def test_recover_verify_unknown_user_is_generic_401(client):
    resp = await client.post(
        "/api/auth/recover/verify",
        json={"username": "ghost", "totp_code": "123456"},
    )
    assert resp.status_code == 401


async def test_reset_token_single_use(client, test_user, user_token, async_session):
    secret = await _enrol(client, user_token)
    verify = await client.post(
        "/api/auth/recover/verify",
        json={"username": "testuser", "totp_code": _recovery_code(secret)},
    )
    reset_token = verify.json()["reset_token"]
    first = await client.post(
        "/api/auth/recover/reset",
        json={"reset_token": reset_token, "new_password": "firstpass123"},
    )
    assert first.status_code == 200
    # Le même reset token ne doit plus marcher (fingerprint du hash a changé)
    second = await client.post(
        "/api/auth/recover/reset",
        json={"reset_token": reset_token, "new_password": "secondpass123"},
    )
    assert second.status_code == 400


async def test_recover_verify_rate_limited(client, test_user, user_token):
    await _enrol(client, user_token)
    statuses = []
    for _ in range(7):
        r = await client.post(
            "/api/auth/recover/verify",
            json={"username": "testuser", "totp_code": "000000"},
        )
        statuses.append(r.status_code)
    assert 429 in statuses
