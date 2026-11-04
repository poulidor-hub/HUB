# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import time
import pyotp
import pytest

from app.totp import (
    generate_totp_secret,
    build_provisioning_uri,
    build_qr_data_uri,
    verify_totp_code,
)
from app.auth import create_reset_token, decode_reset_token, password_fingerprint
from fastapi import HTTPException


def test_generate_secret_is_base32():
    secret = generate_totp_secret()
    assert isinstance(secret, str) and len(secret) >= 16
    pyotp.TOTP(secret).now()  # ne doit pas lever


def test_provisioning_uri_contains_issuer_and_account():
    uri = build_provisioning_uri("JBSWY3DPEHPK3PXP", "alice", "HUB")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=HUB" in uri
    assert "alice" in uri


def test_qr_data_uri_is_svg_data_uri():
    uri = build_qr_data_uri("otpauth://totp/HUB:alice?secret=JBSWY3DPEHPK3PXP&issuer=HUB")
    assert uri.startswith("data:image/svg+xml;base64,")


def test_verify_valid_code_returns_counter():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    counter = verify_totp_code(secret, code, last_counter=None)
    assert counter == int(time.time()) // 30


def test_verify_invalid_code_returns_none():
    secret = generate_totp_secret()
    assert verify_totp_code(secret, "000000", last_counter=None) is None


def test_verify_rejects_replay():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    counter = verify_totp_code(secret, code, last_counter=None)
    assert counter is not None
    # Rejouer le même code avec last_counter = counter doit échouer
    assert verify_totp_code(secret, code, last_counter=counter) is None


def test_reset_token_roundtrip():
    token = create_reset_token("alice", "hashedpw")
    payload = decode_reset_token(token)
    assert payload["sub"] == "alice"
    assert payload["scope"] == "pwd_reset"
    assert payload["pfp"] == password_fingerprint("hashedpw")


def test_reset_token_rejects_non_reset_scope():
    from app.auth import create_access_token
    access, _, _ = create_access_token(username="alice", apps={})
    with pytest.raises(HTTPException):
        decode_reset_token(access)
