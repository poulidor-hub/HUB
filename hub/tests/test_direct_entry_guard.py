# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Fermeture de l'entrée publique pour les routes d'inscription (25/11/2026).

Le frontal nginx marque chaque requête arrivée par l'entrée publique avec
l'en-tête `X-Hub-Entry: public` et renvoie 403 lui-même pour cinq routes
d'inscription. Côté Hub, la dépendance `require_direct_entry` refuse ces
mêmes routes dès qu'elle voit la marque (contrôle en profondeur) : par l'entrée
publique, la garde par adresse voit l'adresse de nginx, qui appartient au
réseau du Hub et donc à la liste blanche.

Sans la marque, chaque route conserve sa garde par adresse et son comportement.

Seconde entrée, close par la même garde : les frontaux des applications
proxifient l'API du Hub sous `/hub/`. La requête arrive alors de la passerelle
Docker (en liste blanche), sans la marque, mais avec les en-têtes de transfert
posés par nginx — absents de tout appel direct légitime.
"""
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import Application

INTERNAL_ONLY = ["/api/apps/register", "/api/apps/allocate-ports", "/api/apps/me/credentials",
                 "/api/apps/me/shared", "/api/apps/me/openapi"]


async def test_register_refused_from_public_entry(client, async_session, tmp_path):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
         patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/register", json={"slug": "entry-guard", "api_port": 19501},
                              headers={"X-Hub-Entry": "public"})
    assert r.status_code == 403
    assert "réseau interne" in r.json()["detail"]
    assert (await async_session.execute(
        select(Application).where(Application.slug == "entry-guard"))).scalar_one_or_none() is None


@pytest.mark.parametrize("method,path", [("POST", "/api/apps/allocate-ports"),
                                         ("GET", "/api/apps/me/credentials"),
                                         ("PUT", "/api/apps/me/shared"),
                                         ("POST", "/api/apps/me/openapi")])
async def test_legacy_routes_refused_from_public_entry(client, method, path):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True):
        r = await client.request(method, path, headers={"X-Hub-Entry": "public"})
    assert r.status_code == 403
    assert "réseau interne" in r.json()["detail"]


async def test_public_entry_marker_is_case_and_space_insensitive(client):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True):
        r = await client.post("/api/apps/allocate-ports", json={"slug": "entry-guard"},
                              headers={"X-Hub-Entry": "  Public "})
    assert r.status_code == 403
    assert "réseau interne" in r.json()["detail"]


async def test_without_marker_the_route_reaches_its_own_guard(client):
    # 127.0.0.1 hors liste blanche : c'est la garde par adresse qui répond, pas la garde d'entrée
    r = await client.post("/api/apps/allocate-ports", json={"slug": "entry-guard"})
    assert r.status_code == 403
    assert "réseau Docker" in r.json()["detail"]


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@pytest.mark.parametrize("conf", ["nginx.conf", "nginx-ssl.conf"])
def test_nginx_refuses_internal_only_routes_on_public_entry(conf):
    text = (FRONTEND_DIR / conf).read_text(encoding="utf-8")
    for path in INTERNAL_ONLY:
        assert re.search(rf"location \^~ {re.escape(path)}\s*\{{\s*return 403;", text), \
            f"{conf} : bloc 403 absent pour {path}"
    assert "proxy_set_header X-Hub-Entry public;" in text, f"{conf} : en-tête X-Hub-Entry absent"
    assert not re.search(r"location \^~ /api/apps/me/\s*\{", text), \
        f"{conf} : le préfixe /api/apps/me/ ne doit pas être bloqué (frontal : /api/apps/me/role/)"


@pytest.mark.parametrize("en_tete", ["X-Forwarded-For", "X-Real-IP"])
async def test_register_refused_when_forwarded_by_a_proxy(client, async_session, tmp_path, en_tete):
    """Les frontaux des applications proxifient l'API du Hub sous /hub/ : la requête
    arrive alors de la passerelle Docker (en liste blanche), porteuse des en-têtes
    de transfert posés par nginx. Un appel légitime n'en porte aucun."""
    with patch("app.routes.apps.is_docker_network_ip", return_value=True), \
         patch("app.routes.service_accounts.SECRETS_DIR", tmp_path):
        r = await client.post("/api/apps/register", json={"slug": "entry-fwd", "api_port": 19502},
                              headers={en_tete: "203.0.113.5"})
    assert r.status_code == 403
    assert "réseau interne" in r.json()["detail"]
    assert (await async_session.execute(
        select(Application).where(Application.slug == "entry-fwd"))).scalar_one_or_none() is None


@pytest.mark.parametrize("method,path", [("POST", "/api/apps/allocate-ports"),
                                         ("GET", "/api/apps/me/credentials"),
                                         ("PUT", "/api/apps/me/shared"),
                                         ("POST", "/api/apps/me/openapi")])
async def test_legacy_routes_refused_when_forwarded_by_a_proxy(client, method, path):
    with patch("app.routes.apps.is_docker_network_ip", return_value=True):
        r = await client.request(method, path, headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 403
    assert "réseau interne" in r.json()["detail"]
