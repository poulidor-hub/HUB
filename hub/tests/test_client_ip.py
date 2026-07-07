"""Tests de régression pour _get_client_ip (H7 + H8).

Le bug : _get_client_ip trustait X-Forwarded-For inconditionnellement.
Un client externe pouvait spoof son IP pour bypasser le rate-limit (H7)
et la décision interne/externe de /token/introspect (H8, Bearer requis).

Le fix : n'honorer X-Forwarded-For / X-Real-IP que si l'IP TCP directe
(request.client.host) est un proxy de confiance (réseau Docker = frontend
nginx). is_docker_network_ip valide déjà cette appartenance.

Note : le mock cible app.routes.apps.is_docker_network_ip (définition réelle
du helper, et point d'import local dans _get_client_ip), et non
app.routes.auth.is_docker_network_ip : le fix importe le helper localement
dans la fonction, donc le symbole n'est jamais un attribut de app.routes.auth.
"""
from unittest.mock import MagicMock
from app.routes.auth import _get_client_ip


def _make_request(direct_ip: str | None, headers: dict | None = None) -> MagicMock:
    """Construit un faux objet Request avec un client et des headers."""
    req = MagicMock()
    if direct_ip is None:
        req.client = None
    else:
        req.client = MagicMock()
        req.client.host = direct_ip
    req.headers = headers or {}
    return req


def test_external_client_ip_ignored_xff(monkeypatch):
    """Un client externe (pas du réseau docker) qui spoofe X-Forwarded-For
    doit voir son IP réelle utilisée, pas l'IP spoofée."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: False)
    req = _make_request("203.0.113.5", {"X-Forwarded-For": "172.17.0.9"})
    assert _get_client_ip(req) == "203.0.113.5", (
        "Un client externe ne doit pas pouvoir spoof son IP via X-Forwarded-For"
    )


def test_external_client_ip_ignored_xrealip(monkeypatch):
    """Idem avec X-Real-IP."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: False)
    req = _make_request("203.0.113.5", {"X-Real-IP": "172.17.0.9"})
    assert _get_client_ip(req) == "203.0.113.5"


def test_trusted_proxy_honors_xff(monkeypatch):
    """Une requête venant du réseau docker (frontend nginx) DOIT honorer
    X-Forwarded-For — c'est l'IP réelle du client derrière le proxy."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
    req = _make_request("172.17.0.2", {"X-Forwarded-For": "198.51.100.10, 172.17.0.2"})
    assert _get_client_ip(req) == "198.51.100.10"


def test_trusted_proxy_honors_xrealip(monkeypatch):
    """Une requête venant du proxy de confiance honore X-Real-IP."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
    req = _make_request("172.17.0.2", {"X-Real-IP": "198.51.100.10"})
    assert _get_client_ip(req) == "198.51.100.10"


def test_trusted_proxy_without_forwarded_falls_back_to_direct(monkeypatch):
    """Proxy de confiance mais sans header X-Forwarded-For → IP directe."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: True)
    req = _make_request("172.17.0.2")
    assert _get_client_ip(req) == "172.17.0.2"


def test_no_client_returns_unknown(monkeypatch):
    """Pas de client TCP (cas test ASGI) → 'unknown'."""
    monkeypatch.setattr("app.routes.apps.is_docker_network_ip", lambda ip: False)
    req = _make_request(None, {"X-Forwarded-For": "1.2.3.4"})
    assert _get_client_ip(req) == "unknown"
