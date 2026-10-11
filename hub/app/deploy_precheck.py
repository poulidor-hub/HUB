"""Precheck de déploiement (feat 2026-10-11).

Vérifie AVANT d'ouvrir la modale de mise en place (et avant le POST) que tout
est réuni pour déployer l'app choisie — le Hub décide seul, sans rien demander :

  - git_repo_url présent et normalisable en HTTPS ;
  - dépôt accessible avec le PAT (API GitHub ; non-GitHub → git ls-remote) ;
  - Makefile à la racine avec les cibles `build:` et `up:` ;
  - `.env` ou `.env.example` à la racine ;
  - aucun déploiement en cours ;
  - ports + sous-réseau allouables (pool Hub) ;
  - outils du conteneur présents (git, make, docker compose v2) ;
  - démon Docker accessible (socket) ;
  - APPS_HOST_PATH réellement inscriptible (droits d'écriture — incident hub
    tiers : PermissionError au clone/build).

Politique bloquant strict (décision 2026-10-11) : toute incertitude (API
GitHub injoignable, timeout…) est un `fail`, pas un avertissement. L'UI ne
propose la modale que si `ready` est vrai, et le POST /deploy refuse (422)
sinon. Le pipeline conserve ses échecs explicites en dernier filet (fenêtre
TOCTOU entre precheck et lancement).
"""
import asyncio
import logging
import re
import shutil
import subprocess
import uuid

from sqlalchemy import select

from app.deploy_service import get_apps_dir, has_active_deployment_for
from app.github_client import (
    fetch_github_json,
    fetch_github_raw_with_status,
    normalize_github_url,
    parse_github_url,
)
from app.models import Application
from app.port_allocator import (
    PortPoolExhausted,
    SubnetPoolExhausted,
    allocate_ports_for_app,
)

logger = logging.getLogger(__name__)

_LS_REMOTE_TIMEOUT = 20   # secondes
_TOOLS_TIMEOUT = 15       # secondes
_TARGET_RE = "{target}\\s*:"  # cible Makefile, ancrée en début de ligne


def _check(cid: str, label: str, status: str, detail: str = "") -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


# ---- Helpers environnement (niveau module : mockables dans les tests) ----

def _detect_tools() -> list[tuple[str, bool]]:
    """Outils requis par le pipeline exécuté depuis le conteneur Hub."""
    tools: list[tuple[str, bool]] = []
    for name in ("git", "make"):
        tools.append((name, shutil.which(name) is not None))
    try:
        rc = subprocess.run(
            ["docker", "compose", "version"], capture_output=True,
            timeout=_TOOLS_TIMEOUT, check=False,
        ).returncode
        tools.append(("docker compose", rc == 0))
    except (OSError, subprocess.TimeoutExpired):
        tools.append(("docker compose", False))
    return tools


def _docker_daemon_status() -> tuple[bool, str]:
    """Le démon Docker local doit être joignable (make build/up, réconciliation)."""
    from app.docker_scanner import docker_scanner
    if docker_scanner.is_available:
        return True, ""
    return False, (
        "Démon Docker inaccessible : "
        f"{docker_scanner.last_error or 'socket absent ou non monté'}"
    )


def _apps_dir_writable(settings) -> tuple[bool, str]:
    """Droit d'écriture réel sur APPS_HOST_PATH (clone, .env, chmod 600)."""
    apps_dir = get_apps_dir()
    try:
        apps_dir.mkdir(parents=True, exist_ok=True)
        probe = apps_dir / f".precheck-{uuid.uuid4().hex}"
        probe.write_text("probe")
        probe.unlink()
        return True, ""
    except OSError as e:
        return False, (
            f"Le conteneur Hub ne peut pas écrire dans {apps_dir} ({e}) — "
            "vérifiez le montage APPS_HOST_PATH et les UID/GID du conteneur."
        )


def _ls_remote_ok(url: str) -> tuple[bool, str]:
    """Accès dépôt générique (hors GitHub) : git ls-remote, sans clone."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", url, "HEAD"], capture_output=True,
            timeout=_LS_REMOTE_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git ls-remote indisponible : {e}"
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or b"").decode(errors="replace").strip().splitlines()
    return False, err[-1] if err else f"ls-remote a échoué (rc={proc.returncode})"


def _makefile_targets_ok(text: str) -> list[str]:
    """Cibles manquantes parmi build/up (regex ancrée, tolère les espaces)."""
    return [t for t in ("build", "up")
            if not re.search(_TARGET_RE.format(target=t), text, re.M)]


async def _check_repo(normalized_url: str, db) -> tuple[str, str, dict | None]:
    """(status, detail, repo_info). repo_info = payload API si GitHub."""
    parsed = parse_github_url(normalized_url)
    if parsed:
        owner, repo = parsed
        data, status = await fetch_github_json(
            f"https://api.github.com/repos/{owner}/{repo}", db=db)
        if status == 200 and data:
            return "ok", f"github.com/{owner}/{repo}", data
        if status == 404:
            return ("fail",
                    f"Dépôt github.com/{owner}/{repo} introuvable "
                    "(ou PAT sans accès à ce dépôt)", None)
        return ("fail",
                f"API GitHub injoignable (status={status or 'réseau'}) — "
                "bloquant strict", None)
    ok, detail = await asyncio.to_thread(_ls_remote_ok, normalized_url)
    if ok:
        return "ok", "dépôt accessible (ls-remote)", None
    return "fail", f"Dépôt inaccessible : {detail}", None


async def _check_makefile(normalized_url: str, repo_info: dict | None,
                          slug: str) -> tuple[str, str]:
    parsed = parse_github_url(normalized_url)
    if parsed:
        owner, repo = parsed
        branch = (repo_info or {}).get("default_branch") or "main"
        text, status = await fetch_github_raw_with_status(
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/Makefile")
        if status == 404:
            return "fail", "Aucun Makefile à la racine du dépôt"
        if text is None:
            return "fail", f"Impossible de lire le Makefile (status={status or 'réseau'})"
        missing = _makefile_targets_ok(text)
        if missing:
            return "fail", "Makefile sans cible(s) : " + ", ".join(missing)
        return "ok", f"cibles build/up présentes (branche {branch})"
    # Non-GitHub : le disque local si déjà cloné, sinon vérifiable seulement au déploiement
    local = get_apps_dir() / slug / "Makefile"
    if local.exists():
        missing = _makefile_targets_ok(local.read_text(errors="replace"))
        if missing:
            return "fail", "Makefile (clone local) sans cible(s) : " + ", ".join(missing)
        return "ok", "cibles build/up présentes (clone local)"
    return ("warn",
            "Repo non-GitHub sans clone local : cibles vérifiables seulement "
            "au déploiement")


async def _check_env_file(normalized_url: str, repo_info: dict | None,
                          slug: str) -> tuple[str, str]:
    parsed = parse_github_url(normalized_url)
    if parsed:
        owner, repo = parsed
        branch = (repo_info or {}).get("default_branch") or "main"
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
        text, status = await fetch_github_raw_with_status(f"{base}/.env")
        if text is not None:
            return "ok", ".env présent dans le dépôt"
        text, status = await fetch_github_raw_with_status(f"{base}/.env.example")
        if text is not None:
            return "ok", ".env.example présent dans le dépôt"
        if status == 404:
            return ("fail",
                    "Ni .env ni .env.example à la racine : le Hub ne peut pas "
                    "configurer l'app automatiquement")
        return "fail", f"Impossible de lire .env/.env.example (status={status or 'réseau'})"
    local_dir = get_apps_dir() / slug
    if (local_dir / ".env").exists() or (local_dir / ".env.example").exists():
        return "ok", "fichier d'env présent (clone local)"
    return ("warn",
            "Repo non-GitHub sans clone local : fichier d'env vérifiable "
            "seulement au déploiement")


async def run_deploy_precheck(db, slug: str, settings) -> tuple[bool, list[dict]]:
    """Retourne (ready, checks). `ready` = aucun check en `fail`."""
    checks: list[dict] = []

    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    if app is None:
        return False, [_check("app", "Application", "fail", "Application inconnue")]

    # --- Chaîne dépôt (court-circuit : sans URL, rien de distant n'a de sens) ---
    normalized = normalize_github_url(app.git_repo_url) if app.git_repo_url else None
    if not normalized:
        checks.append(_check("git_url", "URL du dépôt Git", "fail",
                             "git_repo_url manquant ou non normalisable en HTTPS"))
        checks.append(_check("repo", "Dépôt accessible (PAT)", "skipped"))
        checks.append(_check("makefile", "Makefile (cibles build/up)", "skipped"))
        checks.append(_check("env_file", ".env / .env.example", "skipped"))
    else:
        checks.append(_check("git_url", "URL du dépôt Git", "ok", normalized))
        repo_status, repo_detail, repo_info = await _check_repo(normalized, db)
        checks.append(_check("repo", "Dépôt accessible (PAT)", repo_status, repo_detail))
        if repo_status == "ok":
            mk_status, mk_detail = await _check_makefile(normalized, repo_info, slug)
            checks.append(_check("makefile", "Makefile (cibles build/up)",
                                 mk_status, mk_detail))
            env_status, env_detail = await _check_env_file(normalized, repo_info, slug)
            checks.append(_check("env_file", ".env / .env.example", env_status, env_detail))
        else:
            checks.append(_check("makefile", "Makefile (cibles build/up)", "skipped"))
            checks.append(_check("env_file", ".env / .env.example", "skipped"))

    # --- Déploiement en cours ---
    if await has_active_deployment_for(db, slug):
        checks.append(_check("no_active_deploy", "Aucun déploiement en cours",
                             "fail",
                             "Un déploiement est déjà en cours ou en file d'attente"))
    else:
        checks.append(_check("no_active_deploy", "Aucun déploiement en cours", "ok"))

    # --- Ports + sous-réseau (idempotent : no-op si déjà alloué) ---
    try:
        allocation, _reused = await allocate_ports_for_app(
            db, slug, settings.get_hub_host(), settings)
        checks.append(_check("ports_subnet", "Ports + sous-réseau allouables",
                             "ok",
                             f"api={allocation.api_port}, "
                             f"frontend={allocation.frontend_port}, "
                             f"subnet={allocation.subnet}"))
    except (PortPoolExhausted, SubnetPoolExhausted) as e:
        checks.append(_check("ports_subnet", "Ports + sous-réseau allouables",
                             "fail", str(e)))
    except Exception as e:  # le precheck ne lève jamais
        logger.warning("Precheck '%s' : allocation impossible : %s", slug, e)
        checks.append(_check("ports_subnet", "Ports + sous-réseau allouables",
                             "fail", f"Allocation impossible : {e}"))

    # --- Environnement du conteneur Hub (droits + outils) ---
    tools = await asyncio.to_thread(_detect_tools)
    missing = [name for name, present in tools if not present]
    if missing:
        checks.append(_check("tools", "Outils du conteneur", "fail",
                             "manquants : " + ", ".join(missing)))
    else:
        checks.append(_check("tools", "Outils du conteneur", "ok",
                             "git, make, docker compose"))

    daemon_ok, daemon_detail = await asyncio.to_thread(_docker_daemon_status)
    checks.append(_check("docker_daemon", "Démon Docker accessible",
                         "ok" if daemon_ok else "fail", daemon_detail))

    dir_ok, dir_detail = _apps_dir_writable(settings)
    checks.append(_check("apps_dir_writable", "Écriture du dossier APPS",
                         "ok" if dir_ok else "fail", dir_detail))

    ready = all(c["status"] != "fail" for c in checks)
    return ready, checks
