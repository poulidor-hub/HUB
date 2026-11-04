# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chantier A (1a) — le socket Docker n'est monté QUE dans le proxy filtré.

Garde-fou anti-régression : si quelqu'un remonte le montage direct du socket
sur hub-api (ou retire le proxy), ces tests doivent échouer.

Whitelist de référence : HUB_base/docs/DOCKER_SOCKET_PROXY.md
(audit des appels réels du 02/11/2026 — SDK python + CLI compose).
Sans dépendance PyYAML : découpage texte des blocs de services.
"""

import re
from pathlib import Path

import pytest

HUB_BASE = Path(__file__).resolve().parents[2]

# HOST_ONLY : le compose n'est pas copié dans l'image — run hôte uniquement
import os as _os
if not _os.path.exists(str(HUB_BASE / "docker-compose.yml")):
    pytest.skip("fichiers checkout hors image — run hôte", allow_module_level=True)
COMPOSE_BASE = HUB_BASE / "docker-compose.yml"
COMPOSE_DEV = HUB_BASE / "docker-compose.dev.yml"

# Familles d'API Docker sans aucun usage dans le HUB (audit 02/11/2026) —
# doivent rester fermées dans l'env du proxy.
FAMILIES_INTERDITES = (
    "AUTH",
    "COMMIT",
    "CONFIGS",
    "DISTRIBUTION",
    "NODES",
    "PLUGINS",
    "SECRETS",
    "SERVICES",
    "SWARM",
    "TASKS",
    "ALLOW_PAUSE",
    "ALLOW_UNPAUSE",
)


def _service_blocks(text: str) -> dict[str, str]:
    """Découpe la section `services:` d'un compose en {nom: bloc_texte}.

    Un service commence par `^  <nom>:$` (2 espaces) et finit au prochain
    en-tête de service ou à une ligne de niveau 0 (clé top-level ou bannière
    de commentaire).
    """
    services: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            if current is not None:
                services[current] = "\n".join(buf)
            current, buf = m.group(1), []
        elif current is not None:
            if re.match(r"^[A-Za-z#]", line):
                services[current] = "\n".join(buf)
                current = None
            else:
                buf.append(line)
    if current is not None:
        services[current] = "\n".join(buf)
    return services


def _proxy_env(bloc: str) -> str:
    """Rendu des variables du proxy, normalisé pour recherche littérale."""
    return re.sub(r"^\s*-\s*", "", bloc, flags=re.M)


# ---------------------------------------------------------------- base


def test_base_socket_monte_uniquement_dans_le_proxy() -> None:
    blocs = _service_blocks(COMPOSE_BASE.read_text())
    assert "docker-socket-proxy" in blocs, "service proxy absent du compose de base"
    assert "docker.sock" in blocs["docker-socket-proxy"], "le proxy doit monter le socket"
    assert "docker.sock" not in blocs["hub-api"], (
        "hub-api ne doit plus voir /var/run/docker.sock directement"
    )


def test_base_hub_api_passe_par_le_proxy() -> None:
    blocs = _service_blocks(COMPOSE_BASE.read_text())
    assert "DOCKER_HOST=tcp://docker-socket-proxy:2375" in blocs["hub-api"]
    assert "docker-socket-proxy" in blocs["hub-api"], (
        "hub-api doit dépendre du proxy (depends_on)"
    )


def test_base_hub_api_sans_groupe_docker() -> None:
    blocs = _service_blocks(COMPOSE_BASE.read_text())
    assert "group_add" not in blocs["hub-api"], (
        "HOST_DOCKER_GID n'a plus de raison d'être sur hub-api (accès via tcp, pas socket)"
    )


def test_base_proxy_familles_dangereuses_fermees() -> None:
    blocs = _service_blocks(COMPOSE_BASE.read_text())
    env = _proxy_env(blocs["docker-socket-proxy"])
    for famille in FAMILIES_INTERDITES:
        assert f"{famille}=1" not in env, f"la famille {famille} doit rester fermée"


# ---------------------------------------------------------------- overlay dev


def test_dev_overlay_hub_api_sans_socket_ni_groupe() -> None:
    blocs = _service_blocks(COMPOSE_DEV.read_text())
    assert "docker.sock" not in blocs["hub-api"], (
        "l'overlay dev ne doit pas re-monter le socket sur hub-api"
    )
    assert "group_add" not in blocs["hub-api"], "l'overlay dev ne doit pas re-fournir le GID docker"
