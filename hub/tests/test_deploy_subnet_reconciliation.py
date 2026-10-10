"""Réconciliation subnet Hub ↔ réseau Docker vivant (bugs 2026-10-10).

Cause racine : le pipeline de déploiement ré-écrit APP_SUBNET depuis
l'allocation DB à CHAQUE déploiement, sans vérifier le réseau Docker existant
de l'app (nommé en dur dans son compose, IPAM épinglé sur ${APP_SUBNET}).
Quand l'allocation diverge du réseau vivant (ré-allocation après purge /
re-création / host instable), `make up` tente de recréer le réseau :
- refusé si des conteneurs tiers y sont attachés (lab) → exit 2
  (exemple-app) ;
- sinon réseau recréé et conteneurs existants cassés (« network not found »)
  (exemple-app-test #9).

Règle : le réseau Docker vivant gagne sur l'allocation Hub.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.docker_scanner import DockerScanner
from app.models import PortAllocation


# ---- Scanner : subnets des réseaux Docker d'un projet Compose ----


class _FakeNet:
    def __init__(self, attrs):
        self.attrs = attrs


def _scanner_with_networks(networks_attrs):
    s = DockerScanner()
    s._available = True
    s._client = SimpleNamespace(
        networks=SimpleNamespace(
            list=lambda: [_FakeNet(a) for a in networks_attrs]
        )
    )
    return s


def _net_attrs(labels, subnets):
    return {
        "Labels": labels,
        "IPAM": {"Config": [{"Subnet": s} for s in subnets]},
    }


def test_get_project_network_subnets_filters_by_compose_label():
    """Seuls les réseaux labellisés com.docker.compose.project=<projet> comptent."""
    s = _scanner_with_networks([
        _net_attrs({"com.docker.compose.project": "exemple-app"},
                   ["10.50.2.0/24"]),
        _net_attrs({"com.docker.compose.project": "autre-app"}, ["10.50.3.0/24"]),
        _net_attrs({}, ["172.17.0.0/16"]),  # réseau système, sans label
    ])
    assert s.get_project_network_subnets("exemple-app") == {"10.50.2.0/24"}


def test_get_project_network_subnets_empty_when_unavailable():
    """Best-effort : set() si Docker est indisponible (symétrique des autres scans)."""
    s = DockerScanner()
    s._available = False
    s._client = False
    assert s.get_project_network_subnets("x") == set()


def test_get_project_network_subnets_never_raises():
    """Un client défaillant ne lève pas : set()."""
    def _boom():
        raise RuntimeError("docker down")
    s = DockerScanner()
    s._available = True
    s._client = SimpleNamespace(networks=SimpleNamespace(list=_boom))
    assert s.get_project_network_subnets("x") == set()


# ---- Réconciliation : le réseau vivant gagne sur l'allocation Hub ----

from app.config import get_settings
from app.port_allocator import reconcile_subnet_with_docker

settings = get_settings()
HUB_HOST = settings.get_hub_host()


@pytest.fixture
async def allocation(async_session):
    row = PortAllocation(
        app_slug="exemple-app", host=HUB_HOST,
        api_port=9009, frontend_port=4220, frontend_https_port=4221,
        subnet="10.50.3.0/24",  # allocation Hub divergente
    )
    async_session.add(row)
    await async_session.commit()
    await async_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_reconcile_updates_allocation_to_docker_subnet(async_session, allocation, monkeypatch):
    """Le subnet du réseau Docker vivant remplace l'allocation Hub divergente."""
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24"},
    )
    real = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)

    assert real == "10.50.2.0/24"
    assert allocation.subnet == "10.50.2.0/24"
    # persisté en DB
    async_session.expire_all()
    row = (await async_session.execute(
        select(PortAllocation).where(PortAllocation.app_slug == "exemple-app")
    )).scalar_one()
    assert row.subnet == "10.50.2.0/24"


@pytest.mark.asyncio
async def test_reconcile_noop_when_already_aligned(async_session, allocation, monkeypatch):
    """Allocation Hub == réseau Docker : aucun changement (idempotent)."""
    allocation.subnet = "10.50.2.0/24"
    await async_session.commit()
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24"},
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.2.0/24"


@pytest.mark.asyncio
async def test_reconcile_ignores_when_no_project_network(async_session, allocation, monkeypatch):
    """Aucun réseau Docker du projet (app jamais démarrée) : allocation conservée."""
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: set(),
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.3.0/24"


@pytest.mark.asyncio
async def test_reconcile_ignores_multiple_networks(async_session, allocation, monkeypatch):
    """Plusieurs réseaux pour le projet : situation ambiguë, on ne tranche pas."""
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24", "10.50.9.0/24"},
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.3.0/24"


@pytest.mark.asyncio
async def test_reconcile_skips_remote_host(async_session, allocation, monkeypatch):
    """Le scan ne reflète que l'hôte local : ignoré pour une allocation distante."""
    await async_session.execute(select(PortAllocation))  # no-op, session chaude
    allocation.host = "10.20.30.40"
    await async_session.commit()
    mocked = monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24"},
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.3.0/24"


@pytest.mark.asyncio
async def test_reconcile_graceful_on_scan_failure(async_session, allocation, monkeypatch):
    """Échec du scan : fallback gracieux, allocation conservée, jamais de raise."""

    def _boom(project):
        raise RuntimeError("docker down")

    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets", _boom,
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.3.0/24"


@pytest.mark.asyncio
async def test_reconcile_disabled_by_flag(async_session, allocation, monkeypatch):
    """Feature flag subnet_scan_docker_enabled=false : pas de réconciliation."""
    monkeypatch.setattr(settings, "subnet_scan_docker_enabled", False, raising=False)
    mocked = monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24"},
    )
    ret = await reconcile_subnet_with_docker(async_session, "exemple-app", allocation, settings)
    assert ret is None
    assert allocation.subnet == "10.50.3.0/24"


# ---- Intégration pipeline : le .env du déploiement porte le subnet réconcilié ----


import asyncio

from tests.test_app_deploy import (
    _CtxSession,
    _install_fake_exec,
    _mk_deployment,
)


@pytest.mark.asyncio
async def test_run_deployment_writes_reconciled_subnet(async_session, monkeypatch, tmp_path):
    """Le pipeline écrit APP_SUBNET du réseau Docker vivant (pas de l'allocation
    divergente) : Compose n'a plus aucune raison de recréer le réseau."""
    import app.deploy_service as ds

    dep_id = await _mk_deployment(async_session)  # allocation DB : 10.50.0.7/24
    appdir = tmp_path / "exemple-app"
    appdir.mkdir()
    (appdir / "Makefile").write_text("build:\nup:\n")
    (appdir / ".env.example").write_text("APP_SLUG=old\n")

    _install_fake_exec(monkeypatch, [(0, b"clone"), (0, b""), (0, b"")])
    monkeypatch.setattr(ds, "_wait_healthy", lambda *a, **k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ds.get_settings(), "apps_host_path", str(tmp_path))
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: {"10.50.2.0/24"},
    )

    await ds._run_deployment(dep_id, "exemple-app",
                             session_factory=lambda: _CtxSession(async_session))

    env_text = (appdir / ".env").read_text()
    assert "APP_SUBNET=10.50.2.0/24" in env_text
    assert "APP_SUBNET=10.50.0.7/24" not in env_text

    dep = await async_session.get(ds.AppDeployment, dep_id)
    await async_session.refresh(dep)
    assert dep.status == "success"
