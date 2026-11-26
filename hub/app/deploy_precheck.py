# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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

Le précheck « image socle » (`check_socle_images`) fait exception de position :
il lit le dépôt CLONÉ, donc ne peut pas tourner avant le clone — le pipeline
(`deploy_service._run_deployment`) l'exécute entre le `.env` et `make build`.
"""
import asyncio
import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml
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


# ---- Précheck « image socle » (FROM poulidor/socle-api|web) ----
#
# Les apps du parc sont construites `FROM poulidor/socle-api:<V>` — image
# LOCALE, jamais sur un registry (`./hubctl socle build`). Absente du Docker
# local, `make build` meurt plus loin sur un « pull access denied » buildkit,
# sans remède. Ce précheck lit les Dockerfile* du dépôt CLONÉ, résout la
# version et interroge le démon.

# Partie image d'un `FROM` (sans le mot-clé) : la référence doit suivre
# immédiatement l'image (`:tag` — accolades acceptées avec espaces,
# `${V:?message}` — ou `@digest`), sinon il n'y en a pas (`latest`).
_SOCLE_FROM_RE = re.compile(
    r"^(?:--platform=\S+\s+)?poulidor/(?P<image>socle-api|socle-web)"
    r"(?P<ref>:\$\{[^}]+\}|[:@][^\s]+)?(?:\s|$)", re.I)
_VAR_RE = re.compile(
    r"\$(?:\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-+?])(?P<rest>[^}]*)?)?\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))")
_ARG_TOKEN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$", re.S)
_SOCLE_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}
_DOCKER_TIMEOUT = 15      # secondes, par `docker image inspect`
_BASE_COMPOSE_FILES = frozenset(("docker-compose.yml", "docker-compose.yaml",
                                 "compose.yml", "compose.yaml"))


class _Unresolved(Exception):
    """Une variable du `FROM` reste sans valeur (ex. `${SOCLE_VERSION}`)."""


class _ComposeLoader(yaml.SafeLoader):
    """safe_load tolérant aux tags Compose (`!override`, `!reset`) : valeur non lue."""


_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _docker_image_state(ref: str) -> str:
    """`docker image inspect <ref>` : present | absent | unknown.

    `unknown` (démon injoignable, droit refusé par le proxy de socket…) n'est
    JAMAIS assimilé à « absente » : on ne bloque pas sur ce qu'on n'a pas pu
    vérifier.
    """
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", ref], capture_output=True, text=True,
            timeout=_DOCKER_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode == 0:
        return "present"
    return "absent" if "no such image" in (proc.stderr or "").lower() else "unknown"


def _find_dockerfiles(repo_dir: Path) -> list[Path]:
    """`Dockerfile*` du dépôt cloné (VCS et dépendances écartés), ordre stable."""
    found: list[Path] = []
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in _SOCLE_SKIP_DIRS)
        found.extend(Path(root) / name for name in sorted(files)
                     if name.lower().startswith("dockerfile"))
    return found


def _dockerfile_instructions(text: str):
    r"""(INSTRUCTION, reste) du Dockerfile : continuations `\` jointes,
    commentaires et lignes vides retirés."""
    pending = ""
    for raw in text.splitlines():
        line = f"{pending} {raw}" if pending else raw
        if line.rstrip().endswith("\\"):
            pending = line.rstrip()[:-1]
            continue
        pending = ""
        parts = line.strip().split(None, 1)
        if parts and not parts[0].startswith("#"):
            yield parts[0].upper(), parts[1] if len(parts) > 1 else ""


def _parse_args(rest: str) -> dict[str, str | None]:
    """`ARG A=1 B="x y"` → {A: "1", B: "x y"} (None : déclaré sans valeur)."""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()
    args: dict[str, str | None] = {}
    for token in tokens:
        m = _ARG_TOKEN_RE.match(token)
        if m:
            args[m.group(1)] = m.group(2)
    return args


def _socle_froms(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """`FROM poulidor/socle-*` d'un Dockerfile : (image, `:tag`/`@digest` ou '',
    ARG visibles `{nom: défaut}`).

    Seuls les ARG déclarés avant le PREMIER FROM portent sur un FROM : ceux
    déclarés après sont locaux à leur étape (sinon version fausse = faux blocage).
    """
    global_args: dict[str, str | None] = {}
    out: list[tuple[str, str, dict[str, str]]] = []
    seen_from = False
    for instr, rest in _dockerfile_instructions(text):
        if instr == "ARG":
            if not seen_from:
                global_args.update(_parse_args(rest))
        elif instr == "FROM":
            m = _SOCLE_FROM_RE.match(rest)
            if m:
                scope = {k: v for k, v in global_args.items() if v is not None}
                out.append(("poulidor/" + m.group("image").lower(),
                            m.group("ref") or "", scope))
            seen_from = True
    return out


def _expand(template: str, lookup) -> str:
    """Substitue les variables à la syntaxe Dockerfile/compose : `$VAR`,
    `${VAR}`, `${VAR:-défaut}` / `${VAR-défaut}`, `${VAR:?err}` / `${VAR?err}`,
    `${VAR:+alt}`. Lève `_Unresolved` si une variable reste sans valeur."""
    def repl(m: re.Match) -> str:
        name = m.group("name") or m.group("bare")
        op, alt = m.group("op"), m.group("rest") or ""
        value = lookup(name)
        if op is None:
            if value is None:
                raise _Unresolved(name)
            return value
        unset = value is None or (op.startswith(":") and value == "")
        if op[1] == "-":                         # la valeur, sinon le défaut
            return alt if unset else value
        if op[1] == "?":                         # valeur obligatoire
            if unset:
                raise _Unresolved(name)
            return value
        return "" if unset else alt              # :+ → alt si la variable est définie
    return _VAR_RE.sub(repl, template)


def _load_env_file(path: Path) -> dict[str, str]:
    """Clés d'un fichier d'env à la compose : `KEY=valeur`, `export KEY=…`,
    valeurs quotées, `# commentaires`."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or key.startswith("#"):
            continue
        value = value.strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, 1)[0].rstrip()
        env[key] = value
    return env


def _load_env(repo_dir: Path) -> dict[str, str]:
    """`.env` (écrit par le Hub, lu par compose et le Makefile) prime sur `.env.example`."""
    env = _load_env_file(repo_dir / ".env.example")
    env.update(_load_env_file(repo_dir / ".env"))
    return env


def _is_deploy_compose(filename: str) -> bool:
    """Compose chargé par `make build` / `make up` ? `docker-compose.yml`,
    `.https.yml`, `.admin.yml`… : oui ; `.dev.yml` (make dev), `.test.yml`
    (make test-docker), `.ci.yml` : non — ils ne construisent rien au déploiement."""
    segments = filename.rsplit(".", 1)[0].split(".")[1:]
    return not any(s.startswith(("test", "dev")) or s == "ci" for s in segments)


def _build_spec(build, inherited=None) -> tuple[str, str, dict[str, str]] | None:
    """Section `build:` d'un service → (contexte, Dockerfile, args de build).
    Un overlay hérite de ce qu'il ne redéfinit pas (`inherited` : le même
    service dans un fichier déjà lu) et complète ses `args`. None si le
    contexte sort du dépôt."""
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        build = {}
    context, dockerfile, args = inherited or (".", "Dockerfile", {})
    context = str(build.get("context") or context)
    dockerfile = str(build.get("dockerfile") or dockerfile)
    raw_args = build.get("args") or {}
    if isinstance(raw_args, list):                  # forme liste : `- CLE=valeur`
        raw_args = dict(str(token).split("=", 1) for token in raw_args if "=" in str(token))
    if not isinstance(raw_args, dict):
        raw_args = {}
    args = {**args, **{str(k): str(v) for k, v in raw_args.items() if v is not None}}
    if os.path.isabs(context) or context.split(os.sep)[0] == "..":
        return None
    return context, dockerfile, args


def _compose_builds(repo_dir: Path) -> tuple[dict[str, list[dict[str, str]]], set[str]]:
    """(args de build des composes de déploiement, par Dockerfile construit —
    un jeu d'args par service qui le construit ; ensemble des Dockerfiles
    construits par un compose quelconque, dev/test inclus)."""
    services_seen: dict[str, tuple[str, str, dict[str, str]]] = {}
    any_built: set[str] = set()
    paths = [p for pattern in ("docker-compose*.y*ml", "compose*.y*ml")
             for p in repo_dir.glob(pattern) if p.is_file()]
    # Fichier de base d'abord : ses args sont complétés par les overlays.
    paths.sort(key=lambda p: (p.name not in _BASE_COMPOSE_FILES, p.name))
    for path in paths:
        try:
            data = yaml.load(path.read_text(errors="replace"), Loader=_ComposeLoader)
        except (OSError, yaml.YAMLError):
            continue                           # compose illisible : pas de preuve
        sections = data.get("services") if isinstance(data, dict) else None
        for name, service in (sections or {}).items():
            if not isinstance(service, dict) or "build" not in service:
                continue
            deploy_file = _is_deploy_compose(path.name)
            spec = _build_spec(service["build"], services_seen.get(name) if deploy_file else None)
            if spec is None:
                continue
            any_built.add(os.path.normpath(os.path.join(spec[0], spec[1])))
            if deploy_file:
                services_seen[name] = spec
    builds: dict[str, list[dict[str, str]]] = {}
    for context, dockerfile, args in services_seen.values():
        built = builds.setdefault(os.path.normpath(os.path.join(context, dockerfile)), [])
        if args not in built:
            built.append(args)
    return builds, any_built


def _from_lookup(scope: dict[str, str], build_args: dict[str, str], env: dict[str, str]):
    """Valeur d'une variable d'un `FROM`, par ordre de priorité Docker :
    args de build du compose (`--build-arg`, interpolés par compose avec le
    `.env` — prime sur le défaut de l'ARG), ARG déclaré avant ce FROM dans le
    même Dockerfile, puis clé du `.env`/`.env.example` à la racine du dépôt."""
    def lookup(name: str) -> str | None:
        if name in build_args:
            return _expand(build_args[name], env.get)
        if name in scope:
            value = scope[name]
            return _expand(value, scope.get) if "$" in value else value
        return env.get(name)
    return lookup


def _check_socle_images(repo_dir: Path) -> tuple[str, str]:
    refs: dict[str, str] = {}          # référence d'image → Dockerfile qui l'exige
    unresolved: dict[str, None] = {}   # versions non résolubles (ordre conservé)
    env = _load_env(repo_dir)
    deploy_args, any_built = _compose_builds(repo_dir)
    for path in _find_dockerfiles(repo_dir):
        rel = os.path.relpath(path, repo_dir)
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        froms = _socle_froms(text)
        if not froms:
            continue
        # Sans compose construisant quoi que ce soit (app bâtie par `docker
        # build` dans son Makefile), tous les Dockerfiles comptent. Sinon seuls
        # comptent ceux que construit le compose de déploiement : un
        # `Dockerfile.test` ne doit pas bloquer un déploiement qui réussirait.
        if any_built and rel not in deploy_args:
            continue
        for args in deploy_args.get(rel, [{}]):
            for image, ref, scope in froms:
                if not ref.startswith(":"):
                    refs.setdefault(image + (ref or ":latest"), rel)
                    continue
                written = ref[1:]
                try:
                    tag = _expand(written, _from_lookup(scope, args, env))
                    if not tag:
                        raise _Unresolved(written)
                except _Unresolved as e:
                    unresolved[f"{image}:{written} ({rel} — {e.args[0]} sans valeur)"] = None
                    continue
                refs.setdefault(f"{image}:{tag}", rel)
    if not refs and not unresolved:
        return "ok", ""

    states = {ref: _docker_image_state(ref) for ref in refs}
    missing = [r for r, state in states.items() if state == "absent"]
    if missing:
        listed = ", ".join(f"{r} ({refs[r]})" for r in missing)
        versions = sorted({r.rsplit(":", 1)[1] for r in missing if "@" not in r})
        # Remède tel que nommé par hubctl : la commande construit HUBsocle <V>.
        # L'UI n'affiche que la fin des logs du déploiement : cause et remède
        # tiennent en une ligne.
        remedy = "./hubctl socle build"
        if versions:
            remedy += " (" + ", ".join(f"HUBsocle {v}" for v in versions) + ")"
        remedy = f"lancez {remedy} puis relancez le déploiement"
        if len(missing) == 1:
            return "fail", (
                f"Image socle {listed} attendue mais absente du Docker local — {remedy}."
            )
        return "fail", (
            f"Images socle attendues mais absentes du Docker local — {remedy} : {listed}."
        )
    if unresolved or "unknown" in states.values():
        reasons = [f"version non résoluble : {u}" for u in unresolved]
        reasons += [f"{r} ({refs[r]}) non inspectable — démon Docker ou droit "
                    f"d'accès indisponible"
                    for r, state in states.items() if state == "unknown"]
        return "warn", "Image socle non vérifiée — " + " ; ".join(reasons) + "."
    listed = ", ".join(f"{r} ({refs[r]})" for r in refs)
    if len(refs) == 1:
        return "ok", f"Image socle présente : {listed}"
    return "ok", f"Images socle présentes : {listed}"


def check_socle_images(repo_dir) -> tuple[str, str]:
    """Précheck « image socle » d'un dépôt CLONÉ : (status, detail).

    Les apps du parc sont construites `FROM poulidor/socle-api:<V>` (image
    locale, jamais sur un registry : `./hubctl socle build`). Absente du Docker
    local, le build meurt plus loin sur un « pull access denied » sans remède.

      - fail : image attendue absente — nommée, remède actionnable ;
      - warn : référence non résoluble ou démon injoignable — non bloquant ;
      - ok  : images présentes ; sans détail si le dépôt n'utilise pas le socle.

    Ne lève jamais (un précheck ne doit pas casser un déploiement).
    """
    try:
        return _check_socle_images(Path(repo_dir))
    except Exception as e:  # défensif
        logger.warning("Precheck image socle %s : %s", repo_dir, e)
        return "warn", f"Image socle non vérifiée (erreur du précheck : {e})"
