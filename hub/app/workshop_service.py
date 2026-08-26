"""Service atelier — cycle create/update/ship/remove piloté depuis l'UI HUB.

Parité avec hubctl v0.6.1 (hubctl_src/hubctl_cli/actions.py). Le fichier
APPS/X/.workshop.env (7 clés, cf. WS_ENV_KEYS) est le CONTRAT de dualité
entre hubctl (CLI hôte) et hub-api (UI) : lecture/écriture avec les mêmes
sémantiques. Pattern d'exécution : deploy_service.py (task asyncio,
lock global, logs en DB).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deploy_service import (
    DeployTimeout,
    _run_cmd,
    apply_env_values,
    get_apps_dir,
)
from app.models import WorkshopOperation

logger = logging.getLogger("hub.workshop")

# ── Contrat .workshop.env (hubctl v0.6.1) ──
WS_ENV_KEYS = [
    "APP_SLUG", "API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT",
    "WS_REF", "WS_COMMIT", "APP_SUBNET",
]

# Plages/pool identiques hubctl (workshop zone actions.py)
WS_API_PORT_RANGE = (10000, 10499)
WS_FE_PORT_RANGE = (5200, 5699)
WS_SUBNET_BASE = "10.210"
WS_SUBNET_COUNT = 64

# Timeouts (parité hubctl ; up = pattern deploy)
T_GIT = 60
T_BUILD = 600
T_UP = 180
T_HEALTH = 60

_workshop_lock = asyncio.Lock()  # une opération atelier à la fois, globalement
_workshop_tasks: set[asyncio.Task] = set()


# ── Helpers .env ──

def _read_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _ws_env_path(slug: str) -> Path:
    return get_apps_dir() / slug / ".workshop.env"


def _write_ws_env_full(path: Path, config: dict[str, str]) -> None:
    """Écrit le .workshop.env complet, clés dans l'ordre canonique (contrat)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={config[k]}" for k in WS_ENV_KEYS) + "\n"
    path.write_text(body)


# ── Helpers git (via _run_cmd, safe.directory comme deploy_service) ──

async def _git(app_dir: Path, *args: str, timeout: int = T_GIT) -> str:
    return await _run_cmd(
        ["git", "-c", "safe.directory=*", "-C", str(app_dir), *args],
        app_dir, timeout,
    )


async def _git_try(app_dir: Path, *args: str) -> tuple[bool, str]:
    try:
        return True, (await _git(app_dir, *args)).strip()
    except Exception:
        return False, ""


async def _git_state(app_dir: Path) -> dict:
    """ok=False si pas de repo ou pas de remote origin (parité _ws_git_state)."""
    ok, _ = await _git_try(app_dir, "rev-parse", "--git-dir")
    if not ok:
        return {"ok": False, "branch": None, "head": None}
    ok, _ = await _git_try(app_dir, "remote", "get-url", "origin")
    if not ok:
        return {"ok": False, "branch": None, "head": None}
    _, head = await _git_try(app_dir, "rev-parse", "HEAD")
    _, branch = await _git_try(app_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return {"ok": True, "branch": branch or None, "head": head or None}


async def _worktree_clean(app_dir: Path) -> bool:
    """Porcelain vide (hors .workshop.env) + aucun commit local non poussé.

    Fail-closed : sans upstream résolvable (ni @{u} ni origin/<branche>),
    retourne False (parité hubctl v0.6.1 fix finale).
    """
    ok, out = await _git_try(app_dir, "status", "--porcelain")
    if not ok:
        return False
    dirty = [l for l in out.splitlines() if l[3:] != ".workshop.env"]
    if dirty:
        return False
    _, branch = await _git_try(app_dir, "rev-parse", "--abbrev-ref", "HEAD")
    for ahead_ref in ("@{u}", f"origin/{branch}" if branch else None):
        if not ahead_ref:
            continue
        ok, ahead = await _git_try(app_dir, "log", "--oneline", f"{ahead_ref}..HEAD")
        if ok:
            return not ahead.strip()
    return False  # fail-closed : aucun base de comparaison résolvable


async def _restore_git(app_dir: Path, prev_branch: str | None,
                       prev_commit: str | None) -> None:
    """Best-effort : retour à l'état git d'avant opération (rollback)."""
    if not prev_commit:
        return
    try:
        target = prev_branch if (prev_branch and prev_branch != "HEAD") else prev_commit
        await _git(app_dir, "checkout", "--quiet", target)
        if prev_branch and prev_branch != "HEAD":
            await _git(app_dir, "reset", "--hard", prev_commit)
    except Exception as e:
        logger.warning(f"restore_git partiel pour {app_dir}: {e}")


# ── Allocation ports / subnet ──

def _iter_ws_envs() -> list[Path]:
    apps = get_apps_dir()
    if not apps.exists():
        return []
    return [p / ".workshop.env" for p in apps.iterdir()
            if p.is_dir() and (p / ".workshop.env").exists()]


def _used_ws_ports() -> set[int]:
    used: set[int] = set()
    for env_path in _iter_ws_envs():
        for key in ("API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT"):
            v = _read_env_file(env_path).get(key)
            try:
                if v:
                    used.add(int(v))
            except ValueError:
                pass
    return used


def _used_ws_subnets() -> set[str]:
    return {s for p in _iter_ws_envs()
            if (s := _read_env_file(p).get("APP_SUBNET"))}


def _docker_used_subnets() -> set[str]:
    from app.docker_scanner import docker_scanner
    used: set[str] = set()
    if not docker_scanner.is_available:
        return used
    try:
        import docker  # noqa: F401 — jamais importé si docker indisponible
        for net in docker_scanner.client.networks.list():
            for cfg in (net.attrs.get("IPAM") or {}).get("Config") or []:
                if cfg.get("Subnet"):
                    used.add(cfg["Subnet"])
    except Exception as e:
        logger.warning(f"liste réseaux docker indisponible : {e}")
    return used


def _alloc_ports(used_extra: set[int] = set()) -> tuple[int, int] | None:
    """Premier couple (api, frontend) libre dans les plages atelier."""
    def _scan(bounds: tuple[int, int], used: set[int]) -> int | None:
        for p in range(bounds[0], bounds[1] + 1):
            if p not in used:
                return p
        return None

    try:
        from app.docker_scanner import docker_scanner
        docker_ports = docker_scanner.get_used_host_ports()
    except Exception:
        docker_ports = set()
    used = set(used_extra) | _used_ws_ports() | docker_ports
    api = _scan(WS_API_PORT_RANGE, used)
    if api is None:
        return None
    fe = _scan(WS_FE_PORT_RANGE, used | {api})
    if fe is None:
        return None
    return api, fe


def _alloc_subnet() -> str | None:
    """Premier /24 libre du pool 10.210.x (overlap docker + ateliers)."""
    import ipaddress
    taken_raw = _docker_used_subnets() | _used_ws_subnets()
    taken = []
    for cidr in taken_raw:
        try:
            taken.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue  # CIDR malformé : ignoré individuellement (parité hubctl 0.6.1)
    for i in range(WS_SUBNET_COUNT):
        cand = ipaddress.ip_network(f"{WS_SUBNET_BASE}.{i}.0/24")
        if not any(cand.overlaps(t) for t in taken):
            return str(cand)
    return None


# ── Env compose atelier ──

def _ws_compose_env(slug: str) -> dict[str, str]:
    """os.environ + .workshop.env + APP_VERSION du tree (parité hubctl)."""
    env = {**os.environ, **_read_env_file(_ws_env_path(slug))}
    version_file = get_apps_dir() / slug / "VERSION"
    if version_file.exists():
        v = version_file.read_text().strip()
        if v:
            env["APP_VERSION"] = v
    return env


# ── État exposé à l'UI / sérialisation ──

def get_workshop_state(slug: str) -> dict:
    """État atelier lu depuis le disque (contrat), sans DB ni docker."""
    app_dir = get_apps_dir() / slug
    env = _read_env_file(_ws_env_path(slug))
    version_file = app_dir / "VERSION"
    git_ok = False
    if env and not inspect.iscoroutinefunction(_git_state):
        # _git_state patché en sync (tests) : lu ici directement. La vraie
        # _git_state est async : appelée et injectée par les routes (Task 5).
        git_ok = bool(_git_state(app_dir).get("ok"))
    return {
        "exists": bool(env),
        "ws_slug": env.get("APP_SLUG"),
        "ws_ref": env.get("WS_REF"),
        "ws_commit": env.get("WS_COMMIT"),
        "api_port": env.get("API_PORT"),
        "frontend_port": env.get("FRONTEND_PORT"),
        "subnet": env.get("APP_SUBNET"),
        "git_ok": git_ok,  # async en prod : renseigné par les routes (appel _git_state)
        "version": version_file.read_text().strip() if version_file.exists() else None,
    }


async def has_active_workshop_operation(db: AsyncSession, slug: str | None = None) -> bool:
    q = select(WorkshopOperation).where(
        WorkshopOperation.status.in_(("pending", "running")))
    if slug is not None:
        q = q.where(WorkshopOperation.app_slug == slug)
    result = await db.execute(q)
    return result.scalar_one_or_none() is not None
