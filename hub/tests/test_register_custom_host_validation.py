# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Validation de l'hôte fourni à l'inscription d'une application.

Avant le correctif, `custom_host` (POST /api/apps/register, POST /api/apps/)
et `host` (POST /api/apps/allocate-ports) arrivaient bruts dans
`f"http://{host}:{port}"`, réécrit telle quelle dans un `proxy_pass` de la
configuration nginx générée : un retour à la ligne dans l'hôte y injectait
des directives. Une valeur refusée l'est en 422, AVANT tout effet de bord ;
une valeur vide reste acceptée et vaut « absent ».
"""

import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.config import get_settings
from app.models import Application, PortAllocation
from tests.conftest import get_auth_header

# Charge utile d'injection : ferme le bloc server, ouvre un location qui
# proxifie vers le socket Docker du Hub.
INJECTION_PAYLOAD = (
    "evil:8080;\n}\nlocation /x { proxy_pass http://docker-socket-proxy:2375"
)

MALICIOUS_HOSTS = [
    pytest.param(INJECTION_PAYLOAD, id="injection-nginx"),
    pytest.param("evil:8080;\n}\nlocation /x {",
                 id="retour-ligne-point-virgule-accolade"),
    pytest.param("evil\nhost", id="retour-ligne"),
    pytest.param("host:8080", id="suffixe-port"),
    pytest.param("http://host", id="schema"),
    pytest.param("host}", id="accolade"),
    pytest.param("2001:db8::1", id="ipv6-nu"),
    pytest.param("x" * 254, id="plus-de-253"),
]


@pytest.fixture
def trusted_docker_network(tmp_path):
    """Réseau Docker de confiance — condition d'accès de /register et
    /allocate-ports (les tests httpx arrivent avec une IP non-Docker) — et
    credentials M2M écrits sous tmp_path. Mêmes patches que
    TestRegisterIdempotent (test_api_apps.py)."""
    with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
         patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        yield


def _refused_fields(response) -> list[str]:
    """Champs du corps pointés par l'erreur de validation (cf.
    validation_exception_handler : detail = [{field, message}]). Vide si la
    réponse n'est pas une erreur de validation."""
    detail = response.json().get("detail")
    if not isinstance(detail, list):
        return []
    return [err.get("field") for err in detail]


# ---------------------------------------------------------------- register


@pytest.mark.parametrize("host", MALICIOUS_HOSTS)
async def test_register_rejects_malicious_custom_host(
        client, async_session, trusted_docker_network, host):
    response = await client.post("/api/apps/register", json={
        "slug": "evil-app", "name": "Evil App",
        "api_port": 9600, "frontend_port": 9601,
        "custom_host": host,
    })
    assert response.status_code == 422, response.text
    assert _refused_fields(response) == ["custom_host"]
    # Refus AVANT toute écriture : aucune application (même en attente)
    apps = (await async_session.execute(select(Application))).scalars().all()
    assert apps == []


@pytest.mark.parametrize("custom_host", [
    pytest.param("", id="vide"),
    pytest.param("   ", id="blancs"),
])
async def test_register_accepts_empty_custom_host(
        client, trusted_docker_network, custom_host):
    response = await client.post("/api/apps/register", json={
        "slug": "plain-app", "name": "Plain App",
        "api_port": 9610, "frontend_port": 9611,
        "custom_host": custom_host,
    })
    assert response.status_code == 200, response.text
    application = response.json()["application"]
    # custom_host vide = absent : URLs construites sur le host du Hub
    hub_host = get_settings().get_hub_host()
    assert application["frontend_url"] == f"http://{hub_host}:9611"
    assert application["is_remote"] is False


@pytest.mark.parametrize("custom_host, expected", [
    pytest.param("192.0.2.50", "192.0.2.50", id="ipv4"),
    pytest.param("app.exemple.lan", "app.exemple.lan", id="nom-domaine"),
    pytest.param("[2001:db8::1]", "[2001:db8::1]", id="ipv6-crochets"),
    pytest.param(" 192.0.2.50 ", "192.0.2.50", id="blancs-extremites"),
])
async def test_register_accepts_valid_custom_host(
        client, trusted_docker_network, custom_host, expected):
    response = await client.post("/api/apps/register", json={
        "slug": "remote-app", "name": "Remote App",
        "api_port": 9620, "frontend_port": 9621,
        "custom_host": custom_host,
    })
    assert response.status_code == 200, response.text
    assert response.json()["application"]["frontend_url"] == f"http://{expected}:9621"


# ---------------------------------------------------------------- allocate-ports


@pytest.mark.parametrize("host", MALICIOUS_HOSTS)
async def test_allocate_ports_rejects_malicious_host(
        client, async_session, trusted_docker_network, host):
    response = await client.post("/api/apps/allocate-ports", json={
        "slug": "evil-app", "host": host,
    })
    assert response.status_code == 422, response.text
    assert _refused_fields(response) == ["host"]
    # Refus AVANT toute écriture : aucune allocation persistée
    allocations = (await async_session.execute(
        select(PortAllocation))).scalars().all()
    assert allocations == []


# ---------------------------------------------------------------- création admin


@pytest.mark.parametrize("host", MALICIOUS_HOSTS)
async def test_admin_create_app_rejects_malicious_custom_host(
        client, admin_token, host):
    response = await client.post("/api/apps/", json={
        "name": "Evil App", "slug": "evil-app",
        "api_port": 9600, "frontend_port": 9601,
        "custom_host": host,
    }, headers=get_auth_header(admin_token))
    assert response.status_code == 422, response.text
    assert _refused_fields(response) == ["custom_host"]


@pytest.mark.parametrize("custom_host", [
    pytest.param("", id="vide"),
    pytest.param("   ", id="blancs"),
])
async def test_admin_create_app_accepts_empty_custom_host(
        client, admin_token, no_publish, custom_host):
    """L'IHM admin envoie une chaîne vide quand « host personnalisé » est
    coché sans valeur : elle doit rester acceptée (= absent)."""
    response = await client.post("/api/apps/", json={
        "name": "Plain App", "slug": "plain-app",
        "api_port": 9610, "frontend_port": 9611,
        "custom_host": custom_host,
    }, headers=get_auth_header(admin_token))
    assert response.status_code == 201, response.text
    hub_host = get_settings().get_hub_host()
    assert response.json()["application"]["frontend_url"] == f"http://{hub_host}:9611"
