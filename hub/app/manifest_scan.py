# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Scan de fond des manifestes `hub-app.yaml` (certification GitHub).

Déclenché à la fin d'un refresh réussi du cache (`POST /repos/refresh`),
NON bloquant : le refresh ne doit jamais échouer à cause du scan. Un seul
scan à la fois (guard tâche module-level, pattern `app/routes/events.py`).

Règle de revalidation (ids sélectionnés) :
- `manifest_checked_at IS NULL` (jamais vérifié), OU
- `updated_at_github` non NULL ET plus récent que `manifest_checked_at`
  (le repo a bougé depuis le dernier certificat), OU
- `manifest_checked_at` plus vieux que 7 jours (staleness).

Les lignes avec `default_branch` NULL sont incluses : elles ressortent
« inconnues » (valid=None) SANS avancer `manifest_checked_at` — re-test
automatique quand la branche par défaut sera connue.

État exposé par `GET /api/apps/github/repos/manifest-scan` (admin, polling).
"""
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, or_, select

from app.hub_app_manifest import (
    ManifestCheckResult,
    check_manifest,
    update_cache_manifest,
)
from app.models import GithubRepoCache, utcnow

logger = logging.getLogger(__name__)

SCAN_CONCURRENCY = 5          # sémaphore : 5 vérifications parallèles max
REVALIDATE_AFTER_DAYS = 7     # re-certification minimale hebdomadaire

# État du scan en cours (module-level, consommé par la route de statut).
# Datetimes naïfs UTC (convention utcnow() du codebase).
_scan_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}

# Tâche du scan en cours (guard : ne jamais empiler deux scans).
_scan_task: Optional[asyncio.Task] = None


def get_manifest_scan_status() -> dict:
    """Copie de l'état du scan, datetimes convertis en ISO (ou None).

    La conversion se fait au moment de la réponse : l'état interne reste
    en datetime naïfs, le JSON de la route en chaînes ISO.
    """
    started, finished = _scan_state["started_at"], _scan_state["finished_at"]
    return {
        "running": _scan_state["running"],
        "total": _scan_state["total"],
        "done": _scan_state["done"],
        "errors": _scan_state["errors"],
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
    }


def start_manifest_scan() -> bool:
    """Démarre un scan de fond. False (sans effet) si un scan est déjà actif.

    `_scan_state["running"]` est posé AVANT `create_task` (aucun await entre
    les deux : pas d'entrelacement) pour qu'un double appel consécutif ne
    démarre jamais deux scans.
    """
    global _scan_task
    if _scan_state["running"]:
        return False
    if _scan_task is not None and not _scan_task.done():
        # Filet de sécurité : tâche vivante mais état désynchronisé.
        return False
    _scan_state["running"] = True
    try:
        _scan_task = asyncio.create_task(_scan_worker())
    except Exception:
        _scan_state["running"] = False
        raise
    return True


async def _select_repo_ids_to_revalidate() -> list[int]:
    """Ids des dépôts du cache à re-certifier (règle de revalidation).

    `default_branch` est volontairement NON filtrant : les NULL sont
    sélectionnés et ressortiront « inconnus » sans avancer leur certificat.
    """
    from app.database import async_session_maker

    week_ago = utcnow() - timedelta(days=REVALIDATE_AFTER_DAYS)
    async with async_session_maker() as db:
        res = await db.execute(
            select(GithubRepoCache.repo_id).where(or_(
                GithubRepoCache.manifest_checked_at.is_(None),
                and_(
                    GithubRepoCache.updated_at_github.is_not(None),
                    GithubRepoCache.updated_at_github
                    > GithubRepoCache.manifest_checked_at,
                ),
                GithubRepoCache.manifest_checked_at < week_ago,
            ))
        )
        return [row.repo_id for row in res.all()]


async def _scan_worker() -> None:
    """Corps de la tâche : sélection des ids puis scan. La sélection ne doit
    jamais laisser l'état bloqué en running=True (reset dans le except)."""
    try:
        repo_ids = await _select_repo_ids_to_revalidate()
    except asyncio.CancelledError:
        _scan_state["running"] = False
        _scan_state["finished_at"] = utcnow()
        raise
    except Exception:
        logger.exception("Scan des manifestes : échec de la sélection des ids")
        _scan_state["running"] = False
        _scan_state["finished_at"] = utcnow()
        return
    await run_manifest_scan(repo_ids)


async def run_manifest_scan(repo_ids: list[int]) -> None:
    """Scanne les manifestes des repo_ids donnés (sémaphore 5).

    Une session par item (pattern `github_token_monitor` via
    `async_session_maker`, redirigé vers la DB de test en pytest par la
    fixture `_patch_session_manager`/`_patch_session_maker`). État final
    `running=False` garanti par try/finally, même en cas d'exception.
    """
    _scan_state["running"] = True
    _scan_state["total"] = len(repo_ids)
    _scan_state["done"] = 0
    _scan_state["errors"] = 0
    _scan_state["started_at"] = utcnow()
    _scan_state["finished_at"] = None

    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _scan_one(repo_id: int) -> None:
        async with semaphore:
            from app.database import async_session_maker

            try:
                async with async_session_maker() as db:
                    row = (await db.execute(
                        select(GithubRepoCache)
                        .where(GithubRepoCache.repo_id == repo_id)
                    )).scalar_one_or_none()
                    if row is None:
                        # Sorti du cache entre sélection et scan : rien à faire.
                        return
                    if row.default_branch:
                        result = await check_manifest(
                            row.owner, row.name, row.default_branch, db
                        )
                    else:
                        # Branche par défaut inconnue : résultat INCONNU —
                        # ne décertifie pas et n'avance pas checked_at
                        # (re-test quand la branche sera connue).
                        result = ManifestCheckResult(valid=None, reason_key=None)
                    await update_cache_manifest(db, repo_id, result)
                    if result.valid is None:
                        _scan_state["errors"] += 1
            except Exception:
                _scan_state["errors"] += 1
                logger.exception(
                    "Scan des manifestes : échec du repo_id=%s", repo_id
                )
            finally:
                _scan_state["done"] += 1

    try:
        await asyncio.gather(*[_scan_one(rid) for rid in repo_ids])
    finally:
        _scan_state["running"] = False
        _scan_state["finished_at"] = utcnow()
