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
    _wait_healthy,
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


# ── Exécution des opérations (pattern deploy_service) ──

LOGS_CAP = 100_000
_WS_MTIME_WARN_S = 60  # dualité : hubctl vient probablement d'agir


async def _update_op(op_id: int, **fields) -> None:
    from app.database import async_session_maker
    async with async_session_maker() as db:
        op = await db.get(WorkshopOperation, op_id)
        if op is None:
            return
        for k, v in fields.items():
            if k == "logs":
                merged = (op.logs or "") + (v or "")
                setattr(op, k, merged[-LOGS_CAP:])
            else:
                setattr(op, k, v)
        await db.commit()


async def _step(op_id: int, step: str, msg: str = "") -> None:
    await _update_op(op_id, current_step=step, logs=f"[{step}] {msg}\n")


async def _fail(op_id: int, e: Exception) -> None:
    from app.models import utcnow
    await _update_op(op_id, status="failed", logs=f"❌ {e}\n",
                    finished_at=utcnow())


def cancel_all_workshop_operations() -> None:
    """Shutdown : annule les opérations atelier en cours."""
    for task in list(_workshop_tasks):
        task.cancel()
    _workshop_tasks.clear()


async def start_workshop_operation(slug: str, operation: str,
                                   triggered_by: str | None) -> int:
    """Crée la ligne d'opération et lance la tâche de fond (non-bloquant)."""
    from app.database import async_session_maker
    async with async_session_maker() as db:
        op = WorkshopOperation(app_slug=slug, operation=operation,
                               status="pending", triggered_by=triggered_by)
        db.add(op)
        await db.commit()
        await db.refresh(op)
        op_id = op.id
    task = asyncio.create_task(_run_workshop_operation(op_id, slug, operation))
    _workshop_tasks.add(task)
    task.add_done_callback(_workshop_tasks.discard)
    return op_id


async def _ws_mtime_warning(slug: str, op_id: int) -> None:
    """Dualité UI/CLI : .workshop.env touché il y a < 60 s → warning (best-effort)."""
    path = _ws_env_path(slug)
    try:
        if path.exists() and time.time() - path.stat().st_mtime < _WS_MTIME_WARN_S:
            await _update_op(op_id, logs=(
                "⚠ .workshop.env modifié il y a moins de 60 s — "
                "hubctl a peut-être agi récemment sur cet atelier.\n"))
    except OSError:
        pass


async def _run_workshop_operation(op_id: int, slug: str, operation: str) -> None:
    """Dispatch sous lock global — une opération atelier à la fois."""
    async with _workshop_lock:
        await _update_op(op_id, status="running")
        await _ws_mtime_warning(slug, op_id)
        handlers = {"create": _op_create, "update": _op_update,
                    "ship": _op_ship, "remove": _op_remove}
        handler = handlers.get(operation)
        try:
            if handler is None:
                raise RuntimeError(f"Opération inconnue : {operation}")
            await handler(op_id, slug)
        except asyncio.CancelledError:
            from app.models import utcnow
            await _update_op(op_id, status="failed", current_step="annulé",
                             finished_at=utcnow())
            raise
        except Exception as e:
            await _fail(op_id, e)
            logger.exception(f"Opération atelier {operation}/{slug} échouée : {e}")


async def _origin_main(app_dir: Path) -> str | None:
    ok, sha = await _git_try(app_dir, "rev-parse", "origin/main")
    return sha or None


# ── CREATE ──

async def _op_create(op_id: int, slug: str) -> None:
    app_dir = get_apps_dir() / slug
    ws_slug = f"{slug}-ws"
    compose_file = app_dir / "docker-compose.yml"

    if not app_dir.exists() or not compose_file.exists():
        raise RuntimeError(f"Source introuvable ou sans docker-compose.yml : {app_dir}")
    if _ws_env_path(slug).exists():
        raise RuntimeError(f"Atelier « {ws_slug} » déjà existant")

    await _step(op_id, "allocate", "Ports + subnet atelier")
    ports = _alloc_ports()
    if ports is None:
        raise RuntimeError("Plus de ports libres pour l'atelier (plages 10000-10499 / 5200-5699)")
    ws_api, ws_fe = ports
    subnet = _alloc_subnet()
    if subnet is None:
        raise RuntimeError("Pool de subnets atelier épuisé (10.210.0.0/24 ×64)")

    state = await _git_state(app_dir)
    config = {
        "APP_SLUG": ws_slug, "API_PORT": str(ws_api), "FRONTEND_PORT": str(ws_fe),
        "FRONTEND_HTTPS_PORT": str(ws_fe + 1),
        "WS_REF": (state["branch"] or "n/a") if state["ok"] else "n/a",
        "WS_COMMIT": (state["head"] or "n/a") if state["ok"] else "n/a",
        "APP_SUBNET": subnet,
    }
    _write_ws_env_full(_ws_env_path(slug), config)
    compose_env = _ws_compose_env(slug)

    try:
        await _step(op_id, "clone-db", f"{slug}-postgres-data → {ws_slug}-postgres-data")
        await _run_cmd(["docker", "volume", "create", f"{ws_slug}-postgres-data"],
                       app_dir, 30)
        await _run_cmd(
            ["docker", "run", "--rm", "-v", f"{slug}-postgres-data:/from:ro",
             "-v", f"{ws_slug}-postgres-data:/to", "alpine", "cp", "-a", "/from/.", "/to/"],
            app_dir, 120)

        await _step(op_id, "build", f"docker compose -p {ws_slug} build")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "build"],
                       app_dir, T_BUILD, env_extra=compose_env)

        await _step(op_id, "up", f"docker compose -p {ws_slug} up -d")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                       app_dir, T_UP, env_extra=compose_env)

        await _step(op_id, "health", f"Santé atelier (port {ws_api}, 60 s)")
        if not await _wait_healthy(ws_api, timeout=T_HEALTH):
            raise RuntimeError(f"Atelier pas healthy après {T_HEALTH}s (port {ws_api})")
    except Exception:
        # Rollback création : down + config + volume cloné
        try:
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "down"],
                           app_dir, T_UP, env_extra=compose_env)
        except Exception:
            pass
        _ws_env_path(slug).unlink(missing_ok=True)
        try:
            await _run_cmd(["docker", "volume", "rm", f"{ws_slug}-postgres-data"],
                           app_dir, 30)
        except Exception:
            pass
        raise

    from app.models import utcnow
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Atelier « {ws_slug} » créé (ports {ws_api}/{ws_fe}, subnet {subnet})\n",
                     finished_at=utcnow())


# ── UPDATE (toujours origin/main — décision spec) ──

async def _op_update(op_id: int, slug: str) -> None:
    app_dir = get_apps_dir() / slug
    ws_slug = f"{slug}-ws"
    compose_file = app_dir / "docker-compose.yml"
    env = _read_env_file(_ws_env_path(slug))
    if not env:
        raise RuntimeError(f"Atelier « {ws_slug} » inexistant (.workshop.env manquant)")

    state = await _git_state(app_dir)
    if not state["ok"]:
        raise RuntimeError(f"{app_dir} n'est pas un repo git avec remote origin")
    if env.get("WS_REF", "n/a") == "n/a":
        raise RuntimeError("Atelier créé sans support git (WS_REF=n/a) — supprimez et recréez")
    if not await _worktree_clean(app_dir):
        raise RuntimeError("Working tree sale (modifications locales ou commits non poussés) — "
                           "commit/push ou stash avant la mise à jour")

    prev_branch, prev_commit = state["branch"], state["head"]

    await _step(op_id, "fetch", "git fetch origin")
    await _git(app_dir, "fetch", "--quiet", "origin", "--tags")
    target = await _origin_main(app_dir)
    if not target:
        raise RuntimeError("origin/main introuvable après fetch")

    # Idempotence (parité hubctl)
    if env.get("WS_REF") == "main" and env.get("WS_COMMIT") == target:
        from app.models import utcnow
        await _update_op(op_id, status="success", current_step="done",
                         logs=f"✓ Atelier déjà à jour sur main @ {target[:8]}\n",
                         finished_at=utcnow())
        return

    try:
        await _step(op_id, "checkout", f"checkout {target[:8]} (detached)")
        await _git(app_dir, "checkout", "--quiet", target)
        compose_env = _ws_compose_env(slug)

        await _step(op_id, "build", f"docker compose -p {ws_slug} build")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "build"],
                       app_dir, T_BUILD, env_extra=compose_env)

        await _step(op_id, "up", f"docker compose -p {ws_slug} up -d")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                       app_dir, T_UP, env_extra=compose_env)

        await _step(op_id, "health", f"Santé atelier (port {env.get('API_PORT')}, 60 s)")
        if not await _wait_healthy(int(env.get("API_PORT", "10000")), timeout=T_HEALTH):
            raise RuntimeError("Atelier pas healthy après mise à jour")

        apply_env_values(_ws_env_path(slug),
                         {"WS_REF": "main", "WS_COMMIT": target})
    except Exception:
        await _step(op_id, "rollback", "Restauration git + rebuild ancien commit")
        await _restore_git(app_dir, prev_branch, prev_commit)
        try:
            compose_env = _ws_compose_env(slug)
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "build"],
                           app_dir, T_BUILD, env_extra=compose_env)
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                           app_dir, T_UP, env_extra=compose_env)
        except Exception as e:
            logger.warning(f"rebuild rollback partiel {ws_slug}: {e}")
        raise RuntimeError(
            "Mise à jour échouée (rollback git effectué). "
            "DB du clone possiblement migrée — recréez l'atelier pour repartir du clone propre."
        ) from None

    from app.models import utcnow
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Atelier « {ws_slug} » à jour : main @ {target[:8]}\n",
                     finished_at=utcnow())


# ── ship / remove : implémentés en Task 4 (stubs pour le dispatch) ──

async def _op_ship(op_id: int, slug: str) -> None:
    raise NotImplementedError(f"Opération ship non implémentée (Task 4) : {slug}")


async def _op_remove(op_id: int, slug: str) -> None:
    raise NotImplementedError(f"Opération remove non implémentée (Task 4) : {slug}")
