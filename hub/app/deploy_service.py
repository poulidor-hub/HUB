"""Déploiement automatique des apps du parc (spec 2026-08-18-auto-deploy-apps).

Pipeline : git clone/pull --ff-only -> .env -> make build && make up
(parité voie manuelle : make build/up incluent le sync-env de l'app) ->
health check. Un déploiement à la fois (lock asyncio global).
App sans Makefile = échec explicite (jamais de `docker compose` nu sans
sync-env ni overlay HTTPS).
"""
import re
import secrets
from pathlib import Path

# Variantes de clés .env rencontrées dans le parc (parité hubctl _write_app_env).
ENV_VARIANTS: dict[str, list[str]] = {
    "APP_SLUG": ["APP_SLUG"],
    "API_PORT": ["API_PORT", "APP_PORT", "APP_PORT_API"],
    "FRONTEND_PORT": ["FRONTEND_PORT"],
    "FRONTEND_HTTPS_PORT": ["FRONTEND_HTTPS_PORT"],
    "HUB_HOST": ["HUB_HOST", "HUB_URL", "HUB_BASE_URL"],
    "APP_HOST": ["APP_HOST", "SERVER_IP", "HOST_IP"],
    "POSTGRES_PASSWORD": ["POSTGRES_PASSWORD"],
    "SECRETS_ENCRYPTION_KEY": ["SECRETS_ENCRYPTION_KEY"],
    # Le port API du HUB est servi en HTTP dans ce parc (HTTPS termine au
    # frontend 443) — les apps ne doivent jamais joindre l'API en https.
    "HUB_HTTPS_ENABLED": ["HUB_HTTPS_ENABLED"],
}

# Clés décisionnelles toujours présentes dans le .env final (« le HUB décide,
# l'app suit »). FRONTEND_HTTPS_PORT n'y figure pas : remplacée seulement si
# présente (le Makefile sync-env la pose sinon).
# - APP_SUBNET : parité hubctl _write_app_env — écrit dès le déploiement,
#   sinon `make build` échoue sur ${APP_SUBNET:?} avant le sync-env de l'app.
# - SECRETS_ENCRYPTION_KEY : générée uniquement à la création du .env ; si le
#   .env.example omet la clé, elle doit quand même être persistée.
ENSURE_KEYS = [
    "APP_SLUG", "API_PORT", "FRONTEND_PORT", "APP_SUBNET",
    "HUB_HOST", "APP_HOST", "HUB_HTTPS_ENABLED", "SECRETS_ENCRYPTION_KEY",
]


def build_env_values(slug: str, api_port: int, frontend_port: int, hub_host: str) -> dict[str, str]:
    """Valeurs décisionnelles canoniques injectées à chaque déploiement."""
    return {
        "APP_SLUG": slug,
        "API_PORT": str(api_port),
        "FRONTEND_PORT": str(frontend_port),
        "FRONTEND_HTTPS_PORT": str(frontend_port + 1),
        "HUB_HOST": hub_host,
        "APP_HOST": "",  # app colocalisée avec le HUB
        "HUB_HTTPS_ENABLED": "false",  # l'API du HUB est HTTP (HTTPS au frontend)
    }


def generate_secrets() -> dict[str, str]:
    """Secrets forts, générés uniquement à la création du .env (jamais régénérés)."""
    from cryptography.fernet import Fernet
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "SECRETS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


def apply_env_values(env_path: Path, values: dict[str, str], ensure: list[str] | None = None) -> None:
    """Remplace dans le .env les variantes de chaque clé canonique.

    - Remplace TOUTES les occurrences des variantes (plusieurs fichiers du parc
      déclarent APP_PORT et APP_PORT_API ensemble).
    - Les clés de `ensure` (défaut ENSURE_KEYS) sont ajoutées en fin de fichier
      si aucune variante n'existe.
    - Si POSTGRES_PASSWORD ∈ values et qu'une ligne DATABASE_URL existe, le mot
      de passe dans l'URL est reconstruit.
    """
    ensure = ENSURE_KEYS if ensure is None else ensure
    lines = env_path.read_text().splitlines()

    for canonical, value in values.items():
        keys = ENV_VARIANTS.get(canonical, [canonical])
        pattern = re.compile(rf"^({'|'.join(re.escape(k) for k in keys)})\s*=.*$")
        found = False
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if m:
                lines[i] = f"{m.group(1)}={value}"
                found = True
        if not found and canonical in ensure:
            lines.append(f"{canonical}={value}")

    text = "\n".join(lines) + "\n"

    if "POSTGRES_PASSWORD" in values:
        pwd = values["POSTGRES_PASSWORD"]
        text = re.sub(r"(://[^:/@\s]+:)[^@\s]*(@)", rf"\g<1>{pwd}\g<2>", text)

    env_path.write_text(text)


# ==== Worker de déploiement ====

import asyncio
import base64
import logging
import os
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compose_env import compose_base_env
from app.config import get_settings
from app.database import async_session_maker
from app.models import AppDeployment, Application
from app.port_allocator import allocate_ports_for_app

logger = logging.getLogger(__name__)

# Timeouts par étape (secondes)
TIMEOUT_CLONE = 300
TIMEOUT_BUILD = 900
TIMEOUT_UP = 180
TIMEOUT_HEALTH = 120
LOGS_CAP = 100_000

_deploy_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Pool de déploiements simultanés (lazy : N lu une seule fois, borné 1-6).

    Remplace l'ancien verrou global (1 déploiement à la fois) : les petites apps
    peuvent se déployer en parallèle, les excédentaires attendent en file
    (statut DB 'pending').
    """
    global _deploy_semaphore
    if _deploy_semaphore is None:
        n = max(1, min(6, int(get_settings().max_concurrent_deployments)))
        _deploy_semaphore = asyncio.Semaphore(n)
    return _deploy_semaphore
_deploy_tasks: set[asyncio.Task] = set()


class DeployTimeout(Exception):
    pass


def get_apps_dir() -> Path:
    return Path(get_settings().apps_host_path)


async def has_active_deployment_for(db: AsyncSession, slug: str) -> bool:
    """Un déploiement est-il déjà en cours/en file pour CETTE app uniquement ?"""
    result = await db.execute(
        select(AppDeployment.id)
        .where(AppDeployment.status.in_(["pending", "running"]))
        .where(AppDeployment.app_slug == slug)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def cancel_all_deployments() -> None:
    """Shutdown : annule les déploiements en cours (pattern lifespan du HUB)."""
    for task in list(_deploy_tasks):
        task.cancel()
    _deploy_tasks.clear()


async def requalify_stuck_deployments(db: AsyncSession) -> int:
    """Boot : requalifie en 'failed' les déploiements restés pending/running.

    Au démarrage, aucune tâche de déploiement n'est vivante : toute ligne
    pending/running provient d'un crash/redémarrage (sinon 409 permanent).
    """
    result = await db.execute(
        select(AppDeployment).where(AppDeployment.status.in_(["pending", "running"]))
    )
    stuck = result.scalars().all()
    for dep in stuck:
        dep.status = "failed"
        dep.current_step = "interrompu"
        dep.finished_at = _now()
        dep.logs = ((dep.logs or "") +
                    f"[{_now():%H:%M:%S}] ❌ Interrompu par redémarrage du HUB — relancez le déploiement\n")[-LOGS_CAP:]
    await db.commit()
    return len(stuck)


async def start_deployment(slug: str, triggered_by: str | None) -> int:
    """Crée la ligne de déploiement et lance la tâche de fond (non-bloquant)."""
    async with async_session_maker() as db:
        dep = AppDeployment(app_slug=slug, status="pending", triggered_by=triggered_by)
        db.add(dep)
        await db.commit()
        await db.refresh(dep)
        deployment_id = dep.id
    task = asyncio.create_task(_run_deployment(deployment_id, slug))
    _deploy_tasks.add(task)
    task.add_done_callback(_deploy_tasks.discard)
    return deployment_id


async def _update_deployment(deployment_id: int, session_factory=None, **fields) -> None:
    factory = session_factory or async_session_maker
    async with factory() as db:
        dep = await db.get(AppDeployment, deployment_id)
        if dep is None:
            return
        for k, v in fields.items():
            if k == "logs":
                merged = ((dep.logs or "") + (v or ""))
                setattr(dep, k, merged[-LOGS_CAP:])
            else:
                setattr(dep, k, v)
        await db.commit()


def _git_auth_args(token: str | None) -> list[str]:
    """Args git d'auth PAT — le token ne transite qu'en mémoire processus
    (extraheader), jamais en ligne de commande, jamais écrit sur disque."""
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {basic}"]


async def _get_github_token(db: AsyncSession) -> str | None:
    from app.models import AppSetting
    settings = get_settings()
    if settings.github_token:
        return settings.github_token
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == "github_token")
    )
    return result.scalar_one_or_none()


async def _run_cmd(cmd: list[str], cwd: Path, timeout: int,
                   env_extra: dict[str, str] | None = None,
                   env_base: dict[str, str] | None = None) -> str:
    """Exécute cmd. env_extra surcharge l'env hérité ; env_base REMPLACE cet
    env hérité (défaut : os.environ) — utilisé pour ne pas fuiter l'env du
    conteneur hub dans les apps (cf. workshop_service._COMPOSE_ENV_ALLOWLIST)."""
    base = os.environ if env_base is None else env_base
    env = {**base, **(env_extra or {})}
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise DeployTimeout(f"Timeout ({timeout}s) sur : {cmd[0]}")
    output = out.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"Échec ({proc.returncode}) de '{cmd[0]}':\n{output[-4000:]}"
        )
    return output


async def _wait_healthy(api_port: int, timeout: int = TIMEOUT_HEALTH) -> bool:
    host = get_settings().get_hub_host()
    url = f"http://{host}:{api_port}/api/health"
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=5) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(3)
    return False


async def _run_deployment(deployment_id: int, slug: str, session_factory=None) -> None:
    """Pipeline complet. Chaque étape journalise en DB (statut + étape + logs).

    File d'attente : la ligne reste 'pending' tant qu'aucun slot du pool n'est
    libre ; le statut passe 'running' à l'acquisition du sémaphore.
    """
    factory = session_factory or async_session_maker
    sem = _get_semaphore()
    try:
        await sem.acquire()
    except asyncio.CancelledError:
        await _update_deployment(
            deployment_id, factory, status="failed",
            current_step="annulé en file", finished_at=_now(),
            logs="Déploiement annulé (shutdown du HUB) pendant l'attente d'un slot\n",
        )
        raise
    try:
        await _update_deployment(deployment_id, factory, status="running", current_step="clone",
                                 logs=f"[{_now():%H:%M:%S}] Déploiement démarré\n")
        try:
            async with factory() as db:
                result = await db.execute(
                    select(Application).where(Application.slug == slug)
                )
                app = result.scalar_one()
                repo_url = app.git_repo_url
                host = get_settings().get_hub_host()
                allocation, _reused = await allocate_ports_for_app(db, slug, host, get_settings())
                token = await _get_github_token(db)

            app_dir = get_apps_dir() / slug
            api_port, frontend_port = allocation.api_port, allocation.frontend_port

            # --- Étape 1 : clone / pull --ff-only (parité action UPDATE) ---
            git_common = ["git", "-c", "safe.directory=*"]
            if (app_dir / ".git").exists():
                cmd = git_common + _git_auth_args(token) + ["-C", str(app_dir),
                                                            "pull", "--ff-only"]
                cwd = app_dir
            else:
                app_dir.parent.mkdir(parents=True, exist_ok=True)
                cmd = git_common + _git_auth_args(token) + ["clone", repo_url, str(app_dir)]
                cwd = app_dir.parent
            logger.info(f"[deploy:{slug}] git {'pull' if 'pull' in cmd else 'clone'}")
            out = await _run_cmd(cmd, cwd, TIMEOUT_CLONE)
            await _update_deployment(deployment_id, factory, current_step="env",
                                     logs=out[-2000:] + "\n")

            # --- Étape 2 : .env ---
            env_path = app_dir / ".env"
            example = app_dir / ".env.example"
            if not env_path.exists():
                if not example.exists():
                    raise RuntimeError(
                        "Aucun .env ni .env.example dans le dépôt — "
                        "impossible de configurer l'app automatiquement."
                    )
                env_path.write_text(example.read_text())
                values = {**build_env_values(slug, api_port, frontend_port, host),
                          **generate_secrets()}
            else:
                values = build_env_values(slug, api_port, frontend_port, host)
            # Subnet alloué par le hub — écrit dès le déploiement (parité
            # hubctl _write_app_env) : NULL pour les allocations antérieures
            # au pool, dans ce cas le sync-env de l'app le rattrapera.
            if allocation.subnet:
                values["APP_SUBNET"] = allocation.subnet
            apply_env_values(env_path, values)
            env_path.chmod(0o600)  # secrets en clair : parité hubctl _write_app_env
            await _update_deployment(
                deployment_id, factory, current_step="build",
                logs=f"[{_now():%H:%M:%S}] .env configuré (API={api_port}, FRONT={frontend_port})\n")

            # --- Étapes 3-4 : build + up via le Makefile de l'app (voie
            #     manuelle : make build/make up incluent le sync-env). Jamais
            #     de `docker compose` nu : sans Makefile, l'app est non conforme
            #     au template (pas de sync-env, pas d'overlay HTTPS, et le
            #     compose exigerait ${APP_SUBNET:?} absent du .env). ---
            if not (app_dir / "Makefile").exists():
                raise RuntimeError(
                    "App sans Makefile — non conforme au template HUBapp. "
                    "Le déploiement automatique exige le Makefile du template "
                    "(make build / make up) ; déployez manuellement ou "
                    "réalignez l'app sur le template."
                )
            env_base = compose_base_env()  # jamais l'env du hub dans l'app (cf. COMPOSE_ENV_ALLOWLIST)
            out = await _run_cmd(["make", "build"], app_dir, TIMEOUT_BUILD,
                                 env_base=env_base)
            await _update_deployment(deployment_id, factory, current_step="up",
                                     logs=out[-4000:] + "\n")
            out = await _run_cmd(["make", "up"], app_dir, TIMEOUT_UP,
                                 env_base=env_base)
            await _update_deployment(deployment_id, factory, current_step="health",
                                     logs=out[-2000:] + "\n")

            # --- Étape 5 : health check ---
            healthy = await _wait_healthy(api_port)
            if not healthy:
                raise RuntimeError(
                    f"Health check KO après {TIMEOUT_HEALTH}s sur le port {api_port}"
                )
            await _update_deployment(
                deployment_id, factory, status="success", current_step="done",
                finished_at=_now(),
                logs=f"[{_now():%H:%M:%S}] ✅ App en ligne (port {api_port})\n",
            )
            logger.info(f"[deploy:{slug}] succès")
        except asyncio.CancelledError:
            await _update_deployment(deployment_id, factory, status="failed",
                                     current_step="annulé", finished_at=_now(),
                                     logs="Déploiement annulé (shutdown du HUB)\n")
            raise
        except Exception as e:
            logger.error(f"[deploy:{slug}] échec: {e}", exc_info=True)
            await _update_deployment(deployment_id, factory, status="failed",
                                     finished_at=_now(),
                                     logs=f"[{_now():%H:%M:%S}] ❌ {e}\n")
    finally:
        sem.release()


def _now():
    from app.models import utcnow
    return utcnow()
