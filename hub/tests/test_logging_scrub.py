# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Scrub des secrets en query string dans les access logs uvicorn.

Audit 08/11/2026 : chaque reconnexion SSE en mode query_token loggeait un
JWT admin complet (~2,5 Ko, valide 8 h) dans les access logs. Le filtre
`_ScrubSecretsFilter` doit masquer les valeurs de `token=` et
`beacon_token=` sans casser le reste de la ligne.
"""
import logging

from app.main import _ScrubSecretsFilter, _SuppressLegacySSE401Filter


def _make_record(path_with_query: str, status: int = 200) -> logging.LogRecord:
    """Reproduit le format d'args d'un LogRecord uvicorn.access."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", path_with_query, "1.1", status),
        exc_info=None,
    )


def test_scrub_masks_jwt_token_in_query():
    f = _ScrubSecretsFilter()
    rec = _make_record("/api/events/stream?token=eyJhbGciOiJSUzI1NiIsSECRET.value")
    assert f.filter(rec) is True  # la ligne reste loggée
    joined = str(rec.args)
    assert "eyJhbGciOiJSUzI1NiIsSECRET" not in joined
    assert "token=***" in joined


def test_scrub_masks_beacon_token_in_query():
    f = _ScrubSecretsFilter()
    rec = _make_record("/api/events/stream?user_id=1&beacon_token=97ade955cfe5603c")
    f.filter(rec)
    joined = str(rec.args)
    assert "97ade955cfe5603c" not in joined
    assert "beacon_token=***" in joined


def test_scrub_masks_token_after_other_params():
    f = _ScrubSecretsFilter()
    rec = _make_record("/api/x?app=exemple-app-6&token=eyJSECRET")
    f.filter(rec)
    joined = str(rec.args)
    assert "eyJSECRET" not in joined
    assert "app=exemple-app-6" in joined  # le reste de la query est intact


def test_scrub_leaves_clean_path_untouched():
    f = _ScrubSecretsFilter()
    original = "/api/users?page=2"
    rec = _make_record(original)
    f.filter(rec)
    assert rec.args[2] == original


def test_suppress_legacy_sse_401_unchanged():
    """Comportement existant préservé : 401 GET /api/events/stream filtré."""
    f = _SuppressLegacySSE401Filter()
    assert f.filter(_make_record("/api/events/stream?token=eyJx", status=401)) is False
    assert f.filter(_make_record("/api/events/stream", status=200)) is True
    assert f.filter(_make_record("/api/auth/login", status=401)) is True
