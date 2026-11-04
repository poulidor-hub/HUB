# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Claims identité (uid/prenom/nom) dans le JWT applicatif."""
import jwt as pyjwt

from app.auth import create_access_token


def test_access_token_contains_uid_and_names():
    token, _, _ = create_access_token(
        username="jdupont", apps={}, uid="uuid-1234", prenom="Julie", nom="Martin"
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "jdupont"
    assert claims["uid"] == "uuid-1234"
    assert claims["prenom"] == "Julie"
    assert claims["nom"] == "Martin"


def test_access_token_without_identity_has_no_claims():
    """Sans uid (service accounts, anciens appels), aucun claim ajouté."""
    token, _, _ = create_access_token(username="svc", apps={})
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert "uid" not in claims
    assert "prenom" not in claims
    assert "nom" not in claims
