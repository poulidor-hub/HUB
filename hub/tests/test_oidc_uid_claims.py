"""OIDC : uid + name (Prénom Nom) dans l'id_token et le userinfo."""
import jwt as pyjwt

from app.config import get_settings
from app.oidc_helpers import compute_display_name, create_id_token


def test_compute_display_name():
    assert compute_display_name("Julie", "Martin", "jdupont") == "Julie Martin"
    assert compute_display_name("Julie", None, "jdupont") == "Julie"
    assert compute_display_name(None, None, "jdupont") == "jdupont"


def test_id_token_contains_uid_and_display_name():
    settings = get_settings()
    token = create_id_token(
        username="jdupont",
        client_id="client-1",
        nonce=None,
        scope="openid profile email",
        settings=settings,
        uid="uuid-1234",
        prenom="Julie",
        nom="Martin",
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "jdupont"
    assert claims["uid"] == "uuid-1234"
    assert claims["name"] == "Julie Martin"
    assert claims["preferred_username"] == "jdupont"


def test_id_token_name_falls_back_to_username():
    settings = get_settings()
    token = create_id_token(
        username="jdupont",
        client_id="client-1",
        nonce=None,
        scope="openid profile",
        settings=settings,
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["name"] == "jdupont"
    assert "uid" not in claims
