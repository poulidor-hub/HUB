# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Service atelier — cycle create/update/ship/remove piloté depuis l'UI HUB.

Parité avec hubctl v0.6.1 (hubctl_src/hubctl_cli/actions.py). Le fichier
APPS/X/.workshop.env (7 clés, cf. WS_ENV_KEYS) est le CONTRAT de dualité
entre hubctl (CLI hôte) et hub-api (UI) : lecture/écriture avec les mêmes
sémantiques. Complément : APPS/X/.workshop.overrides.env (optionnel,
committé par l'app) surcharge l'env compose de l'atelier — sous les clés
du contrat, cf. _ws_override_env. Pattern d'exécution : deploy_service.py
(task asyncio, lock global, logs en DB).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.deploy_service import (
    DeployTimeout,
    _git_auth_args,
    _get_github_token,
    _run_cmd,
    _wait_healthy,
    apply_env_values,
    get_apps_dir,
)
from app.models import WorkshopOperation

logger = logging.getLogger("hub.workshop")

# Ref atelier (branche/tag) : sûr à passer en argv git (jamais de shell).
# Interdits : préfixe '-' (flag git), '..', blancs, ~^:?*[\, >120 chars.
_WS_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


def validate_ws_ref(ref: str | None) -> str | None:
    """Normalise et valide une ref atelier. None/'' → None. Lève ValueError."""
    if ref is None:
        return None
    ref = ref.strip()
    if not ref:
        return None
    if not _WS_REF_RE.fullmatch(ref) or ".." in ref or ref.endswith(".lock"):
        raise ValueError(f"Ref git invalide : {ref!r}")
    return ref

# ── Contrat .workshop.env (hubctl v0.6.1) ──
WS_ENV_KEYS = [
    "APP_SLUG", "API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT",
    "WS_REF", "WS_COMMIT", "APP_SUBNET",
]

# Clés possédées par l'allocateur atelier (contrat .workshop.env + overlay
# HTTPS) : présentes dans .workshop.overrides.env, elles sont ignorées avec
# un warning — les overrides ne peuvent pas détourner ports/subnet/ref.
WS_OVERRIDE_RESERVED = set(WS_ENV_KEYS) | {"HTTPS_ENABLED"}

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


def _app_dir(slug: str) -> Path:
    """Dossier local de l'app pour un slug donné.

    Le nom du dossier ne dérive pas toujours du slug (ex. APPS/exemple-app pour
    « philia-proxy ») : cas canonique APPS/<slug>/ si son .env déclare
    APP_SLUG=<slug>, sinon scan des .env ; repli : APPS/<slug>/ (comportement
    historique — les opérations lèveront une erreur explicite).
    """
    root = get_apps_dir()
    direct = root / slug
    if _read_env_file(direct / ".env").get("APP_SLUG") == slug:
        return direct
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if _read_env_file(child / ".env").get("APP_SLUG") == slug:
                return child
    except OSError:
        pass
    return direct


def _ws_env_path(slug: str) -> Path:
    return _app_dir(slug) / ".workshop.env"


def _ws_overrides_path(slug: str) -> Path:
    """Fichier d'overrides optionnel, même répertoire que le contrat."""
    return _app_dir(slug) / ".workshop.overrides.env"


def _ws_override_env(slug: str) -> dict[str, str]:
    """Env d'overrides compose de l'atelier (APPS/<slug>/.workshop.overrides.env).

    Motivation : les apps qui bindent des ports host métier via des vars de
    leur .env prod (ex. exemple-app : "${PHILIA_NG_PORT:-42690}:8080")
    retombaient sur le port prod dans l'atelier → conflit de bind → restart
    loop. Compose lit lui-même le .env prod du working tree (partagé avec la
    prod) : l'OS doit pouvoir lui imposer les valeurs atelier, sans toucher
    au tree ni au contrat .workshop.env.

    Sémantique :
    - fichier absent → {} (no-op strict, comportement identique à avant) ;
    - parsing tolérant via _read_env_file (pas d'échec dur sur lignes
      bizarres) ;
    - clés réservées (WS_OVERRIDE_RESERVED : clés du contrat + HTTPS_ENABLED)
      ignorées avec un warning — possédées par l'allocateur ;
    - fichier illisible (OSError, dont PermissionError) → warning + ignore :
      jamais de crash d'opération atelier pour ça.

    Précédence d'appel (du plus faible au plus fort) : _compose_base_env()
    < _ws_override_env() < clés du contrat .workshop.env (cf.
    _ws_compose_env)."""
    path = _ws_overrides_path(slug)
    if not path.exists():
        return {}
    try:
        overrides = _read_env_file(path)
    except (OSError, UnicodeDecodeError) as e:
        # OSError couvre PermissionError ; UnicodeDecodeError : fichier non
        # UTF-8 (encodage exotique) — même contrat : warning + ignore, jamais
        # de crash d'opération atelier pour un fichier d'override.
        logger.warning(f".workshop.overrides.env illisible ({e}) — ignoré, "
                       f"opération atelier poursuivie sans overrides ({slug})")
        return {}
    reserved = {k: v for k, v in overrides.items() if k in WS_OVERRIDE_RESERVED}
    for key in reserved:
        logger.warning(f"clé réservée {key} ignorée dans .workshop.overrides.env"
                       f" — possédée par l'allocateur ({slug})")
    return {k: v for k, v in overrides.items() if k not in reserved}


def _write_ws_env_full(path: Path, config: dict[str, str]) -> None:
    """Écrit le .workshop.env complet, clés dans l'ordre canonique (contrat)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={config[k]}" for k in WS_ENV_KEYS) + "\n"
    path.write_text(body)


# ── Helpers git (via _run_cmd, safe.directory comme deploy_service) ──

async def _git(app_dir: Path, *args: str, timeout: int = T_GIT,
              auth_args: list[str] | None = None) -> str:
    return await _run_cmd(
        ["git", "-c", "safe.directory=*", *(auth_args or []), "-C", str(app_dir), *args],
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


async def _github_token() -> str | None:
    """PAT GitHub (settings puis DB) — jamais loggé, transite en mémoire."""
    try:
        async with async_session_maker() as db:
            return await _get_github_token(db)
    except Exception:
        return None


async def _configure_remote(app_dir: Path, url: str) -> str:
    """Configure origin sur le repo local. added|updated|unchanged ;
    RuntimeError si APPS/X n'est pas un dépôt git."""
    ok, _ = await _git_try(app_dir, "rev-parse", "--git-dir")
    if not ok:
        raise RuntimeError(f"{app_dir} n'est pas un dépôt git — déployez l'app depuis son dépôt d'abord")
    ok, current = await _git_try(app_dir, "remote", "get-url", "origin")
    current = (current or "").strip()
    if ok and current == url:
        return "unchanged"
    if ok:
        await _git(app_dir, "remote", "set-url", "origin", url)
        return "updated"
    await _git(app_dir, "remote", "add", "origin", url)
    return "added"


async def test_git_connection(url: str) -> tuple[bool, str]:
    """ls-remote avec PAT si dispo. (ok, message) — échec = verdict, pas exception."""
    auth = _git_auth_args(await _github_token())
    try:
        out = await _run_cmd(["git", *auth, "ls-remote", "--heads", url],
                             get_apps_dir(), 30)
        n = len([l for l in out.strip().splitlines() if l.strip()])
        return True, f"Connexion OK — {n} branche(s)"
    except Exception as e:
        # _run_cmd préfixe "Échec (N) de 'git':" — garder les lignes utiles
        # de la sortie git (remote:/fatal:), pas le préfixe technique.
        lines = [l.strip() for l in str(e).splitlines()
                 if l.strip() and not l.startswith("Échec (")]
        return False, (" — ".join(lines[:2])[:300]) if lines else str(e)[:300]


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

# Allowlist déplacée dans app.compose_env (partagée avec deploy_service) ;
# alias privés conservés pour les appelants historiques.
from app.compose_env import (  # noqa: E402
    COMPOSE_ENV_ALLOWLIST as _COMPOSE_ENV_ALLOWLIST,
    compose_base_env as _compose_base_env,
)


def _ws_compose_env(slug: str) -> dict[str, str]:
    """Allowlist + overrides + .workshop.env + APP_VERSION du tree (parité hubctl).

    L'environnement du hub-api n'est volontairement PAS transmis (cf.
    _COMPOSE_ENV_ALLOWLIST). Précédence (du plus faible au plus fort) :
    allowlist docker < .workshop.overrides.env (bat le .env prod que Compose
    lit lui-même depuis le working tree) < clés du contrat .workshop.env
    (possédées par l'allocateur, gagnent TOUJOURS)."""
    env = _compose_base_env()
    env.update(_ws_override_env(slug))
    env.update(_read_env_file(_ws_env_path(slug)))
    version_file = _app_dir(slug) / "VERSION"
    if version_file.exists():
        v = version_file.read_text().strip()
        if v:
            env["APP_VERSION"] = v
    return env


async def _delete_workshop_app_row(ws_slug: str) -> bool:
    """Supprime la fiche Hub de l'app atelier ({slug}-ws).

    Sans cela, la ligne Application auto-enregistrée par l'app -ws reste en DB
    après suppression de l'atelier (ligne « fantôme ») : ses URLs pointent alors
    sur des ports réattribués à un autre atelier → la menu hub ouvre la
    mauvaise app. Garde-fou : seules les fiches is_workshop=True sont visées.
    True si supprimée, False si absente (l'appelant traite les erreurs)."""
    from sqlalchemy.orm import selectinload

    from app.database import async_session_maker
    from app.models import Application
    from app.routes.apps import delete_app_cascade

    async with async_session_maker() as db:
        result = await db.execute(
            select(Application)
            .options(selectinload(Application.service_accounts))
            .where(Application.slug == ws_slug,
                   Application.is_workshop.is_(True)))
        app = result.scalar_one_or_none()
        if app is None:
            return False
        await delete_app_cascade(db, app)
        await db.commit()
        logger.info(f"🗑 Fiche app atelier « {ws_slug} » supprimée du Hub")
        return True


# ── État exposé à l'UI / sérialisation ──

def get_workshop_state(slug: str) -> dict:
    """État atelier lu depuis le disque (contrat), sans DB ni docker."""
    app_dir = _app_dir(slug)
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
    # limit(1) + first : 2+ lignes pending/running (double-clic race) ne doivent
    # pas lever MultipleResultsFound (pattern has_active_deployment).
    q = select(WorkshopOperation.id).where(
        WorkshopOperation.status.in_(("pending", "running")))
    if slug is not None:
        q = q.where(WorkshopOperation.app_slug == slug)
    q = q.limit(1)
    return (await db.execute(q)).scalars().first() is not None


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
                                   triggered_by: str | None,
                                   ref: str | None = None) -> int:
    """Crée la ligne d'opération et lance la tâche de fond (non-bloquant).

    `ref` (create/update uniquement) : branche/tag à tester en atelier.
    None → comportement historique (create = branche courante, update = main).
    """
    from app.database import async_session_maker
    async with async_session_maker() as db:
        op = WorkshopOperation(app_slug=slug, operation=operation,
                               status="pending", triggered_by=triggered_by)
        db.add(op)
        await db.commit()
        await db.refresh(op)
        op_id = op.id
    task = asyncio.create_task(_run_workshop_operation(op_id, slug, operation, ref))
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


async def _run_workshop_operation(op_id: int, slug: str, operation: str,
                                  ref: str | None = None) -> None:
    """Dispatch sous lock global — une opération atelier à la fois."""
    async with _workshop_lock:
        await _update_op(op_id, status="running")
        await _ws_mtime_warning(slug, op_id)
        handlers = {"create": _op_create, "update": _op_update,
                    "ship": _op_ship, "remove": _op_remove,
                    "recreate": _op_recreate}
        handler = handlers.get(operation)
        try:
            if handler is None:
                raise RuntimeError(f"Opération inconnue : {operation}")
            if operation in ("create", "update"):
                await handler(op_id, slug, ref)
            else:
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


# ── RECREATE (recréation des conteneurs d'une app — ex. après propagation IP) ──

def _prod_overlay_files(app_dir: Path, env: dict) -> list[str]:
    """Overlays compose de prod à injecter, parité avec `make up` : HTTPS
    (sauf si HTTPS_ENABLED=false dans le .env de l'app) et GPU (si
    DOCKER_GPU). Chaque overlay n'est pris que si le fichier existe dans
    l'app — les anciennes apps sans overlay ne sont pas impactées."""
    files: list[str] = []
    if (env.get("HTTPS_ENABLED") or "true").strip().lower() != "false":
        p = app_dir / "docker-compose.https.yml"
        if p.exists():
            files.append(str(p))
    if (env.get("DOCKER_GPU") or "").strip():
        p = app_dir / "docker-compose.gpu.yml"
        if p.exists():
            files.append(str(p))
    return files


async def _op_recreate(op_id: int, slug: str) -> None:
    """`docker compose -p <slug> up -d --force-recreate` + health-check API.

    --force-recreate est indispensable : un simple up -d ne recrée pas un
    conteneur dont seule une variable du .env (non référencée au moment du run
    précédent) a changé.
    Les overlays HTTPS/GPU de l'app sont injectés comme le fait son
    `make up` — sinon la recréation (ex. propagation d'IP) fait perdre le
    port HTTPS de l'app (incident exemple-app 27-28/10/2026)."""
    app_dir = _app_dir(slug)
    compose_file = app_dir / "docker-compose.yml"
    if not compose_file.exists():
        raise RuntimeError(f"docker-compose.yml introuvable : {app_dir}")

    env = _read_env_file(app_dir / ".env")
    try:
        api_port = int(env.get("API_PORT") or 0) or None
    except ValueError:
        api_port = None

    cmd = ["docker", "compose", "-p", slug, "-f", str(compose_file)]
    for f in _prod_overlay_files(app_dir, env):
        cmd += ["-f", f]
    cmd += ["up", "-d", "--force-recreate"]

    await _step(op_id, "recreate", " ".join(cmd))
    await _run_cmd(cmd, app_dir, T_UP, env_base=_compose_base_env())

    if api_port:
        await _step(op_id, "health", f"Santé app (port {api_port}, {T_HEALTH} s)")
        if not await _wait_healthy(api_port, timeout=T_HEALTH):
            raise RuntimeError(f"App pas healthy après {T_HEALTH}s (port {api_port})")

    from app.models import utcnow
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Conteneurs de « {slug} » recréés et healthy\n",
                     finished_at=utcnow())


# ── CREATE ──

async def _op_create(op_id: int, slug: str, ref: str | None = None) -> None:
    app_dir = _app_dir(slug)
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

    # Ref demandée (branches → atelier) : checkout AVANT l'écriture du
    # .workshop.env, pour tracer exactement le code buildé (WS_REF/WS_COMMIT).
    if ref:
        await _step(op_id, "checkout", f"checkout origin/{ref}")
        auth = _git_auth_args(await _github_token())
        await _git(app_dir, "fetch", "--quiet", "origin", "--tags", auth_args=auth)
        sha = await _git_try(app_dir, "rev-parse", f"origin/{ref}")
        if not sha:
            raise RuntimeError(f"Ref « {ref} » introuvable sur origin après fetch")
        await _git(app_dir, "checkout", "--quiet", sha)
        ws_ref, ws_commit = ref, sha
    else:
        state = await _git_state(app_dir)
        ws_ref = (state["branch"] or "n/a") if state["ok"] else "n/a"
        ws_commit = (state["head"] or "n/a") if state["ok"] else "n/a"

    config = {
        "APP_SLUG": ws_slug, "API_PORT": str(ws_api), "FRONTEND_PORT": str(ws_fe),
        "FRONTEND_HTTPS_PORT": str(ws_fe + 1),
        "WS_REF": ws_ref,
        "WS_COMMIT": ws_commit,
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
                       app_dir, T_BUILD, env_base=compose_env)

        await _step(op_id, "up", f"docker compose -p {ws_slug} up -d")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                       app_dir, T_UP, env_base=compose_env)

        await _step(op_id, "health", f"Santé atelier (port {ws_api}, 60 s)")
        if not await _wait_healthy(ws_api, timeout=T_HEALTH):
            raise RuntimeError(f"Atelier pas healthy après {T_HEALTH}s (port {ws_api})")
    except Exception:
        # Rollback création : down + config + volume cloné
        try:
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "down"],
                           app_dir, T_UP, env_base=compose_env)
        except Exception:
            pass
        try:
            _ws_env_path(slug).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"suppression .workshop.env (rollback {ws_slug}) : {e}")
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

async def _op_update(op_id: int, slug: str, ref: str | None = None) -> None:
    """Mise à jour de l'atelier : checkout detached de origin/main (défaut)
    ou de origin/{ref} (branches → atelier). Idempotent, rollback sur échec."""
    app_dir = _app_dir(slug)
    ws_slug = f"{slug}-ws"
    compose_file = app_dir / "docker-compose.yml"
    env = _read_env_file(_ws_env_path(slug))
    if not env:
        raise RuntimeError(f"Atelier « {ws_slug} » inexistant (.workshop.env manquant)")

    target_ref = ref or "main"
    state = await _git_state(app_dir)
    if not state["ok"]:
        raise RuntimeError(f"{app_dir} n'est pas un repo git avec remote origin")
    if env.get("WS_REF", "n/a") == "n/a":
        raise RuntimeError("Atelier créé sans support git (WS_REF=n/a) — supprimez et recréez")
    if not await _worktree_clean(app_dir):
        raise RuntimeError("Working tree sale (modifications locales ou commits non poussés) — "
                           "commit/push ou stash avant la mise à jour")

    prev_branch, prev_commit = state["branch"], state["head"]

    await _step(op_id, "fetch", f"git fetch origin (cible : origin/{target_ref})")
    auth = _git_auth_args(await _github_token())
    await _git(app_dir, "fetch", "--quiet", "origin", "--tags", auth_args=auth)
    if target_ref == "main":
        target = await _origin_main(app_dir)
    else:
        target = await _git_try(app_dir, "rev-parse", f"origin/{target_ref}")
    if not target:
        raise RuntimeError(f"origin/{target_ref} introuvable après fetch")

    # Idempotence (parité hubctl)
    if env.get("WS_REF") == target_ref and env.get("WS_COMMIT") == target:
        from app.models import utcnow
        await _update_op(op_id, status="success", current_step="done",
                         logs=(f"✓ Atelier déjà à jour sur {target_ref} @ {target[:8]}\n"),
                         finished_at=utcnow())
        return

    try:
        await _step(op_id, "checkout", f"checkout {target[:8]} ({target_ref}, detached)")
        await _git(app_dir, "checkout", "--quiet", target)
        compose_env = _ws_compose_env(slug)

        await _step(op_id, "build", f"docker compose -p {ws_slug} build")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "build"],
                       app_dir, T_BUILD, env_base=compose_env)

        await _step(op_id, "up", f"docker compose -p {ws_slug} up -d")
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                       app_dir, T_UP, env_base=compose_env)

        await _step(op_id, "health", f"Santé atelier (port {env.get('API_PORT')}, 60 s)")
        if not await _wait_healthy(int(env.get("API_PORT", "10000")), timeout=T_HEALTH):
            raise RuntimeError("Atelier pas healthy après mise à jour")

        apply_env_values(_ws_env_path(slug),
                         {"WS_REF": target_ref, "WS_COMMIT": target})
    except Exception as e:
        await _step(op_id, "rollback", "Restauration git + rebuild ancien commit")
        await _restore_git(app_dir, prev_branch, prev_commit)
        try:
            compose_env = _ws_compose_env(slug)
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "build"],
                           app_dir, T_BUILD, env_base=compose_env)
            await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file), "up", "-d"],
                           app_dir, T_UP, env_base=compose_env)
        except Exception as e2:
            logger.warning(f"rebuild rollback partiel {ws_slug}: {e2}")
        raise RuntimeError(
            f"Mise à jour échouée ({e}) — rollback git effectué. "
            "DB du clone possiblement migrée — recréez l'atelier pour repartir du clone propre."
        ) from None

    from app.models import utcnow
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Atelier « {ws_slug} » à jour : {target_ref} @ {target[:8]}\n",
                     finished_at=utcnow())


# ── SHIP (déploiement prod + suppression atelier) ──

async def _op_ship(op_id: int, slug: str) -> None:
    app_dir = _app_dir(slug)
    ws_slug = f"{slug}-ws"
    compose_file = app_dir / "docker-compose.yml"
    env = _read_env_file(_ws_env_path(slug))
    if not env:
        raise RuntimeError(f"Atelier « {ws_slug} » inexistant")

    # Garde-fou 1 : on ne déploie QUE ce qui a été testé sur main (release-on-main)
    ws_ref, ws_commit = env.get("WS_REF", "n/a"), env.get("WS_COMMIT", "")
    if ws_ref != "main" or not ws_commit:
        raise RuntimeError(
            f"L'atelier teste « {ws_ref} » — le déploiement ne concerne que origin/main. "
            "Mergez vers main (release-on-main), mettez à jour l'atelier, puis réessayez.")

    state = await _git_state(app_dir)
    if not state["ok"]:
        raise RuntimeError(f"{app_dir} n'est pas un repo git avec remote origin")
    if not await _worktree_clean(app_dir):
        raise RuntimeError("Working tree sale — commit/push ou stash avant le déploiement")

    prev_branch, prev_commit = state["branch"], state["head"]

    # Garde-fou 2 : atelier sain avant de toucher à la prod
    await _step(op_id, "guards", f"Santé atelier (port {env.get('API_PORT')})")
    if not await _wait_healthy(int(env.get("API_PORT", "10000")), timeout=T_HEALTH):
        raise RuntimeError("L'atelier n'est pas sain — on ne déploie pas. "
                           "Diagnostiquez puis relancez.")

    has_makefile = (app_dir / "Makefile").exists()
    build_cmd = ["make", "build"] if has_makefile else \
        ["docker", "compose", "-f", str(compose_file), "build"]
    up_cmd = ["make", "up"] if has_makefile else \
        ["docker", "compose", "-f", str(compose_file), "up", "-d"]

    # Version AVANT déploiement (bilan old → new) — lue avant tout build,
    # sinon on relirait la nouvelle version après reset --hard origin/main.
    prev_version = _read_env_file(app_dir / ".env").get("APP_VERSION", "?")

    try:
        await _step(op_id, "guards", "Sync origin/main")
        auth = _git_auth_args(await _github_token())
        await _git(app_dir, "fetch", "--quiet", "origin", auth_args=auth)
        await _git(app_dir, "checkout", "--quiet", "main")
        await _git(app_dir, "reset", "--hard", "origin/main")

        # Garde-fou 3 : main n'a pas avancé depuis la validation atelier
        target = await _origin_main(app_dir)
        if target != ws_commit:
            raise RuntimeError(
                f"main a avancé depuis le test atelier (testé {ws_commit[:8]}, "
                f"main {(target or 'n/a')[:8]}) — relancez la mise à jour puis revalidez.")

        await _step(op_id, "build", "Build prod (main @ " + ws_commit[:8] + ")")
        await _run_cmd(build_cmd, app_dir, T_BUILD, env_base=_compose_base_env())

        await _step(op_id, "up", "Démarrage prod")
        await _run_cmd(up_cmd, app_dir, T_UP, env_base=_compose_base_env())

        await _step(op_id, "health", "Santé prod (60 s)")
        prod_api = int(_read_env_file(app_dir / ".env").get("API_PORT", "9000"))
        if not await _wait_healthy(prod_api, timeout=T_HEALTH):
            raise RuntimeError("Prod pas healthy après déploiement")
    except Exception as e:
        await _step(op_id, "rollback", "Rollback prod — atelier conservé")
        await _restore_git(app_dir, prev_branch, prev_commit)
        try:
            await _run_cmd(build_cmd, app_dir, T_BUILD, env_base=_compose_base_env())
            await _run_cmd(up_cmd, app_dir, T_UP, env_base=_compose_base_env())
            await _wait_healthy(int(_read_env_file(app_dir / ".env").get("API_PORT", "9000")),
                                timeout=T_HEALTH)
        except Exception as e2:
            logger.warning(f"rollback ship incomplet pour {slug}: {e2}")
        raise RuntimeError(
            f"Déploiement échoué ({e}) — rollback prod effectué, "
            "atelier conservé pour diagnostic."
        ) from None

    # Succès : suppression atelier (seul down -v légitime du cycle)
    await _step(op_id, "cleanup", f"Suppression atelier « {ws_slug} »")
    compose_env = _ws_compose_env(slug)
    try:
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file),
                        "down", "-v"], app_dir, T_UP, env_base=compose_env)
    except Exception as e:
        logger.warning(f"down -v atelier échoué ({e}) — .workshop.env supprimé quand même")
    try:
        _ws_env_path(slug).unlink(missing_ok=True)
    except OSError as e:
        # fs possiblement read-only (ex. EROFS Errno 30) : ne JAMAIS court-
        # circuiter la purge DB qui suit, sinon la fiche {slug}-ws devient un
        # fantôme (marqueur absent + ligne Application orpheline).
        logger.warning(f"unlink .workshop.env échoué ({e}) — fs possiblement "
                       "read-only ; purge DB poursuivie")
        await _step(op_id, "cleanup",
                    "⚠ unlink .workshop.env échoué — fs possiblement "
                    "read-only ; purge fiche Hub poursuivie")

    # Suppression de la fiche Hub {slug}-ws (sinon ligne « fantôme » pointant
    # sur des ports réattribués). Non bloquant : l'atelier est déjà supprimé.
    await _step(op_id, "cleanup", "Suppression fiche Hub de l'atelier")
    try:
        await _delete_workshop_app_row(ws_slug)
    except Exception as e:
        logger.warning(f"Suppression fiche app atelier {ws_slug} échouée : {e}")

    from app.models import utcnow
    version_file = app_dir / "VERSION"
    new_version = version_file.read_text().strip() if version_file.exists() else ""
    new_version = new_version or "?"
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Prod « {slug} » déployée : {prev_version} → {new_version} "
                          f"(main @ {ws_commit[:8]}), atelier « {ws_slug} » supprimé\n",
                     finished_at=utcnow())


# ── REMOVE ──

async def _op_remove(op_id: int, slug: str) -> None:
    app_dir = _app_dir(slug)
    ws_slug = f"{slug}-ws"
    compose_file = app_dir / "docker-compose.yml"
    env = _read_env_file(_ws_env_path(slug))
    if not env:
        raise RuntimeError(f"Atelier « {ws_slug} » inexistant")

    await _step(op_id, "down", f"docker compose -p {ws_slug} down -v")
    compose_env = _ws_compose_env(slug)
    try:
        await _run_cmd(["docker", "compose", "-p", ws_slug, "-f", str(compose_file),
                        "down", "-v"], app_dir, T_UP, env_base=compose_env)
    except Exception as e:
        logger.warning(f"down -v atelier échoué ({e}) — poursuite du cleanup")

    await _step(op_id, "cleanup", "Suppression .workshop.env")
    try:
        _ws_env_path(slug).unlink(missing_ok=True)
    except OSError as e:
        # fs possiblement read-only (ex. EROFS Errno 30) : ne JAMAIS court-
        # circuiter la purge DB qui suit, sinon la fiche {slug}-ws devient un
        # fantôme (marqueur absent + ligne Application orpheline).
        logger.warning(f"unlink .workshop.env échoué ({e}) — fs possiblement "
                       "read-only ; purge DB poursuivie")
        await _step(op_id, "cleanup",
                    "⚠ unlink .workshop.env échoué — fs possiblement "
                    "read-only ; purge fiche Hub poursuivie")

    # Suppression de la fiche Hub {slug}-ws (sinon ligne « fantôme » pointant
    # sur des ports réattribués). Non bloquant : l'atelier est déjà supprimé.
    await _step(op_id, "cleanup", "Suppression fiche Hub de l'atelier")
    try:
        await _delete_workshop_app_row(ws_slug)
    except Exception as e:
        logger.warning(f"Suppression fiche app atelier {ws_slug} échouée : {e}")

    from app.models import utcnow
    await _update_op(op_id, status="success", current_step="done",
                     logs=f"✓ Atelier « {ws_slug} » supprimé (ports + subnet + disque libérés)\n",
                     finished_at=utcnow())


# ── Purge ──

async def purge_workshop_operations(db: AsyncSession, retention_days: int) -> int:
    """Supprime les opérations terminées plus vieilles que retention_days."""
    from datetime import timedelta
    from app.models import utcnow
    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    result = await db.execute(
        select(WorkshopOperation).where(
            WorkshopOperation.status.in_(("success", "failed")),
            WorkshopOperation.finished_at < cutoff,
        ))
    ops = list(result.scalars().all())
    for op in ops:
        await db.delete(op)
    await db.commit()
    return len(ops)
