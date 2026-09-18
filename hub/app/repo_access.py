"""Préflight d'accès Git : vérifier qu'un dépôt est clonable AVANT de créer l'app.

Test réel = `git ls-remote` avec le PAT (même mécanique extraheader que le
clone du déploiement : le token ne transite ni en ligne de commande ni en log).
Diagnostic API GitHub uniquement en cas d'échec, pour un message actionnable
(SSO organisation, scope, token absent/invalide, GitHub inaccessible).
"""
import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.deploy_service import _git_auth_args
from app.github_client import parse_github_url

logger = logging.getLogger(__name__)

TIMEOUT_LS_REMOTE = 10
TIMEOUT_API = 5


@dataclass
class RepoAccessResult:
    ok: bool
    reason_key: str | None = None  # clé machine (branchement frontend)
    reason: str | None = None      # message humain FR prêt à afficher


_MESSAGES = {
    "ok": "Accès au dépôt vérifié.",
    "no_token_private": "Dépôt privé ou introuvable : aucun token GitHub configuré. "
                        "Configurez un token (classic) avec le droit repo.",
    "access_denied": "Le token configuré ne peut pas accéder à ce dépôt privé. "
                     "Cause probable : SSO de l'organisation non autorisé pour ce token, "
                     "ou droit repo manquant.",
    "token_invalid": "Token GitHub invalide ou expiré (401). Reconfigurez-le.",
    "git_auth_error": "Accès API OK mais clone refusé (cas rare). Réessayez ; "
                      "si cela persiste, vérifiez le token.",
    "github_unreachable": "GitHub inaccessible (réseau ou timeout).",
    "non_github": "URL non GitHub : vérification automatique non applicable.",
}


def _message_for(reason_key: str) -> str:
    return _MESSAGES.get(reason_key, "Accès au dépôt non vérifié.")


async def _ls_remote(clone_url: str, token: str | None) -> tuple[int, str]:
    """`git ls-remote` (lecture seule, aucun checkout). Retourne (rc, sortie)."""
    cmd = ["git", "-c", "safe.directory=*"] + _git_auth_args(token) + [
        "ls-remote", clone_url, "HEAD",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_LS_REMOTE)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def _api_repo_status(owner_repo: tuple[str, str], token: str | None) -> int | None:
    """Statut HTTP de GET /repos/{owner}/{repo} (None si réseau KO)."""
    owner, repo = owner_repo
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_API), verify=False) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}", headers=headers
            )
            return resp.status_code
    except httpx.HTTPError:
        return None


async def check_repo_access(clone_url: str, token: str | None) -> RepoAccessResult:
    owner_repo = parse_github_url(clone_url)
    if owner_repo is None:
        return RepoAccessResult(True, "non_github", _message_for("non_github"))

    rc, output = await _ls_remote(clone_url, token)
    if rc == 0:
        return RepoAccessResult(True, "ok", _message_for("ok"))

    logger.info(
        "[repo_access] ls-remote rc=%s pour %s/%s : %s",
        rc, owner_repo[0], owner_repo[1], output[-300:],
    )
    status = await _api_repo_status(owner_repo, token)
    if status == 200:
        reason_key = "git_auth_error"
    elif status == 401:
        reason_key = "token_invalid"
    elif status == 404:
        reason_key = "access_denied" if token else "no_token_private"
    else:
        reason_key = "github_unreachable"
    return RepoAccessResult(False, reason_key, _message_for(reason_key))
