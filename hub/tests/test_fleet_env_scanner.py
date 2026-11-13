# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Scanner des revendications (claims) APP_SUBNET/ports des .env de la flotte.

Cause racine (bug 2026-11-13, intendance vs contrat-w-pr / exemple-app) :
l'allocateur Hub ne consultait que son registre `port_allocations` et les
réseaux/conteneurs Docker VIVANTS. Une app legacy (jamais passée par
`allocate-ports`) ou simplement arrêtée au moment de l'allocation était
invisible : ses ports et son sous-réseau étaient ré-attribués à une nouvelle
app → « Pool overlaps with other one on this address space » et
« port is already allocated » au premier `make up`.

Règle : les `APP_SUBNET`/ports écrits dans les `.env` de la flotte (montage
`apps_host_path`) sont une source de vérité durable — l'allocateur doit les
traiter comme occupés, même sans conteneur ni réseau Docker vivant.
"""

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.fleet_env_scanner import read_env_claims


def _make_app(apps_dir: Path, name: str, env: str) -> Path:
    d = apps_dir / name
    d.mkdir(parents=True)
    (d / ".env").write_text(textwrap.dedent(env))
    return d


# ---- Scanner : lecture des .env de la flotte ----


def test_read_env_claims_collects_subnets_and_ports(tmp_path):
    _make_app(tmp_path, "app-a", """\
        APP_SLUG=app-a
        APP_SUBNET=10.50.16.0/24
        API_PORT=9019
        FRONTEND_PORT=4238
        FRONTEND_HTTPS_PORT=4239
    """)
    _make_app(tmp_path, "app-b", """\
        APP_SLUG=app-b
        APP_SUBNET=10.220.1.0/24
        API_PORT=1256
    """)
    subnets, ports = read_env_claims(tmp_path)
    assert subnets == {"10.50.16.0/24", "10.220.1.0/24"}
    assert ports == {9019, 4238, 4239, 1256}


def test_read_env_claims_ignores_invalid_entries(tmp_path):
    """CIDR invalide, port non numérique ou hors plage : ignorés, jamais levés."""
    _make_app(tmp_path, "broken", """\
        APP_SUBNET=not-a-cidr
        API_PORT=abc
        FRONTEND_PORT=99999
        FRONTEND_HTTPS_PORT=
    """)
    subnets, ports = read_env_claims(tmp_path)
    assert subnets == set()
    assert ports == set()


def test_read_env_claims_only_reads_exact_env_file(tmp_path):
    """Les sauvegardes (.env.pre-subnet-*, .env.bak) ne sont PAS des claims."""
    d = _make_app(tmp_path, "app-a", "APP_SUBNET=10.50.16.0/24\nAPI_PORT=9019\n")
    (d / ".env.pre-subnet-20261113").write_text("APP_SUBNET=10.99.99.0/24\nAPI_PORT=1\n")
    (d / ".env.bak").write_text("APP_SUBNET=10.98.98.0/24\n")
    subnets, ports = read_env_claims(tmp_path)
    assert subnets == {"10.50.16.0/24"}
    assert ports == {9019}


def test_read_env_claims_missing_dir_is_empty(tmp_path):
    """Dossier absent (montage manquant) : set() des deux côtés, jamais levé."""
    subnets, ports = read_env_claims(tmp_path / "does-not-exist")
    assert subnets == set()
    assert ports == set()


def test_read_env_claims_skips_files_and_nested_dirs(tmp_path):
    """Seuls les sous-dossiers directs comptent (pas les fichiers, pas *.env/*)."""
    (tmp_path / "fichier.txt").write_text("APP_SUBNET=10.50.250.0/24\n")
    nested = tmp_path / "app-nested" / "sub"
    nested.mkdir(parents=True)
    (nested / ".env").write_text("APP_SUBNET=10.50.251.0/24\n")
    subnets, ports = read_env_claims(tmp_path)
    assert subnets == set()
    assert ports == set()


# ---- Intégration : allocate_ports_for_app évite les claims de la flotte ----

from app.port_allocator import allocate_ports_for_app

HUB_HOST = "local-host"


def _settings(apps_dir: Path, **over):
    base = dict(
        fleet_env_scan_enabled=True,
        apps_host_path=str(apps_dir),
        subnet_scan_docker_enabled=False,  # isolé : pas de daemon Docker à mocker
        port_scan_docker_enabled=False,
        app_port_api_start=9000,
        app_port_api_end=9499,
        app_port_frontend_start=4200,
        app_port_frontend_end=4699,
        app_subnet_pool="10.50.0.0/16",
        app_subnet_size=24,
        port_reservation_ttl_minutes=0,
        get_hub_host=lambda: HUB_HOST,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_allocate_avoids_fleet_env_claims(async_session, tmp_path):
    """App legacy arrêtée (claim .env, rien de vivant) : ni son subnet ni ses
    ports ne sont ré-attribués (bug 2026-11-13 intendance vs contrat-w-pr /
    exemple-app)."""
    _make_app(tmp_path, "contrat-w-pr", """\
        APP_SUBNET=10.50.0.0/24
        API_PORT=9000
        FRONTEND_PORT=4200
        FRONTEND_HTTPS_PORT=4201
    """)
    settings = _settings(tmp_path)

    alloc, reused = await allocate_ports_for_app(async_session, "intendance", HUB_HOST, settings)

    assert reused is False
    assert alloc.subnet == "10.50.1.0/24"  # 10.50.0.0/24 revendiqué → sauté
    assert alloc.api_port == 9001  # 9000 revendiqué → sauté
    assert (alloc.frontend_port, alloc.frontend_https_port) == (4202, 4203)


@pytest.mark.asyncio
async def test_allocate_ignores_claims_when_scan_disabled(async_session, tmp_path):
    """`fleet_env_scan_enabled=False` : comportement historique (claims ignorés)."""
    _make_app(tmp_path, "x", "APP_SUBNET=10.50.0.0/24\nAPI_PORT=9000\n")
    settings = _settings(tmp_path, fleet_env_scan_enabled=False)

    alloc, _ = await allocate_ports_for_app(async_session, "demo", HUB_HOST, settings)

    assert alloc.subnet == "10.50.0.0/24"
    assert alloc.api_port == 9000


@pytest.mark.asyncio
async def test_allocate_ignores_claims_for_remote_host(async_session, tmp_path):
    """Demande d'un host distant : les claims de la flotte locale ne s'appliquent
    pas (symétrique des scans Docker, scopés sur l'hôte du Hub)."""
    _make_app(tmp_path, "x", "APP_SUBNET=10.50.0.0/24\nAPI_PORT=9000\n")
    settings = _settings(tmp_path)

    alloc, _ = await allocate_ports_for_app(async_session, "demo", "autre-serveur", settings)

    assert alloc.subnet == "10.50.0.0/24"
    assert alloc.api_port == 9000
