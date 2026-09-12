"""Routes atelier — cycle create/update/ship/remove piloté depuis la vue santé."""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.github_client import fetch_github_json, parse_github_url
from app.models import Application, User, WorkshopOperation
from app.workshop_service import (
    _alloc_subnet,
    _app_dir,
    _configure_remote,
    _git_state,
    get_workshop_state,
    has_active_workshop_operation,
    start_workshop_operation,
    test_git_connection,
    validate_ws_ref,
)

logger = logging.getLogger("hub.workshop")
router = APIRouter(prefix="/api/apps", tags=["atelier"])


class WorkshopRefIn(BaseModel):
    """Body optionnel : branche/tag à tester en atelier (branches → atelier)."""
    ref: str | None = None


async def _get_app_or_404(db: AsyncSession, slug: str) -> Application:
    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail=f"Application '{slug}' inconnue")
    return app


def _op_to_dict(op: WorkshopOperation) -> dict:
    return {
        "id": op.id, "app_slug": op.app_slug, "operation": op.operation,
        "status": op.status, "current_step": op.current_step, "logs": op.logs,
        "triggered_by": op.triggered_by,
        "started_at": op.started_at.isoformat() if op.started_at else None,
        "finished_at": op.finished_at.isoformat() if op.finished_at else None,
    }


async def _ensure_no_active(db: AsyncSession) -> None:
    """Garde-fou global : une seule opération atelier à la fois (toutes apps)."""
    if await has_active_workshop_operation(db):
        raise HTTPException(
            status_code=409,
            detail="Une opération atelier est déjà en cours. Réessayez une fois terminée.")


@router.get("/{slug}/workshop")
async def get_workshop(slug: str, db: AsyncSession = Depends(get_db),
                       _: User = Depends(get_current_admin)):
    """État atelier (contrat .workshop.env) + git_ok + opération active."""
    app = await _get_app_or_404(db, slug)
    state = get_workshop_state(slug)
    state["git_ok"] = (await _git_state(_app_dir(slug)))["ok"]
    state["active_operation"] = await has_active_workshop_operation(db, slug=slug)
    state["git_repo_url"] = app.git_repo_url  # valeur DB (pré-remplissage modale)
    return state


@router.post("/{slug}/workshop", status_code=201)
async def create_workshop(slug: str, body: WorkshopRefIn | None = None,
                          db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    try:
        ref = validate_ws_ref(body.ref if body else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Atelier déjà existant pour '{slug}'.")
    if not (await _git_state(_app_dir(slug)))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin — "
                                   "l'atelier ne peut pas être créé en ligne.")
    if _alloc_subnet() is None:
        raise HTTPException(status_code=503,
                            detail="Pool de subnets atelier épuisé (10.210.x).")
    op_id = await start_workshop_operation(slug, "create", admin.username, ref=ref)
    logger.info(f"Atelier create #{op_id} lancé pour {slug} (ref={ref or 'courante'}) "
                f"par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "create",
            "status": "pending", "ref": ref}


@router.post("/{slug}/workshop/update", status_code=201)
async def update_workshop(slug: str, body: WorkshopRefIn | None = None,
                          db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    try:
        ref = validate_ws_ref(body.ref if body else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if not state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}' — créez-le d'abord.")
    if not (await _git_state(_app_dir(slug)))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin.")
    op_id = await start_workshop_operation(slug, "update", admin.username, ref=ref)
    logger.info(f"Atelier update #{op_id} lancé pour {slug} (ref={ref or 'main'}) "
                f"par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "update",
            "status": "pending", "ref": ref or "main"}


@router.post("/{slug}/workshop/ship", status_code=201)
async def ship_workshop(slug: str, db: AsyncSession = Depends(get_db),
                        admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    state = get_workshop_state(slug)
    if not state["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}' — créez-le d'abord.")
    if not (await _git_state(_app_dir(slug)))["ok"]:
        raise HTTPException(status_code=400,
                            detail="Cette app n'a pas de repo git avec remote origin.")
    if state.get("ws_ref") != "main":
        raise HTTPException(
            status_code=409,
            detail=f"L'atelier teste « {state.get('ws_ref')} » — le déploiement ne "
                   "concerne que origin/main. Mergez (release-on-main), mettez à jour "
                   "l'atelier, puis réessayez.")
    op_id = await start_workshop_operation(slug, "ship", admin.username)
    logger.info(f"Atelier ship #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "ship", "status": "pending"}


@router.delete("/{slug}/workshop", status_code=201)
async def remove_workshop(slug: str, db: AsyncSession = Depends(get_db),
                          admin: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    await _ensure_no_active(db)
    if not get_workshop_state(slug)["exists"]:
        raise HTTPException(status_code=409,
                            detail=f"Aucun atelier pour '{slug}'.")
    op_id = await start_workshop_operation(slug, "remove", admin.username)
    logger.info(f"Atelier remove #{op_id} lancé pour {slug} par {admin.username}")
    return {"operation_id": op_id, "slug": slug, "operation": "remove", "status": "pending"}


@router.get("/{slug}/workshop/status")
async def get_workshop_status(slug: str, db: AsyncSession = Depends(get_db),
                              _: User = Depends(get_current_admin)):
    await _get_app_or_404(db, slug)
    result = await db.execute(
        select(WorkshopOperation).where(WorkshopOperation.app_slug == slug)
        .order_by(WorkshopOperation.id.desc()).limit(10))
    ops = list(result.scalars().all())
    return {"current": _op_to_dict(ops[0]) if ops else None,
            "history": [_op_to_dict(o) for o in ops]}


class GitConfigIn(BaseModel):
    git_repo_url: str


@router.post("/{slug}/git-config")
async def configure_app_git(slug: str, body: GitConfigIn,
                            db: AsyncSession = Depends(get_db),
                            admin: User = Depends(get_current_admin)):
    """Configure le dépôt git d'une app : DB + remote local + test ls-remote."""
    app = await _get_app_or_404(db, slug)
    url = body.git_repo_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL git requise")
    if url.startswith(("ssh://", "git@", "git://")):
        raise HTTPException(
            status_code=400,
            detail="SSH non supporté côté HUB — utilisez l'URL HTTPS (http:// ou https://)")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail="URL invalide — doit commencer par http:// ou https://")
    try:
        sync = await _configure_remote(_app_dir(slug), url)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    app.git_repo_url = url
    await db.commit()
    ok, message = await test_git_connection(url)
    logger.info(f"git-config {slug} → {url} (sync={sync}, test ok={ok}) par {admin.username}")
    return {"ok": ok, "message": message, "sync": sync}


# ─── Branches (lookup à la demande — branches → atelier) ───────────────

BRANCHES_TTL_S = 30            # convention anti-spam GitHub du parc
_branches_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _branches_error(status: int) -> str:
    if status == 401:
        return "Authentification GitHub refusée — vérifiez le token enregistré."
    if status == 403:
        return "Rate limit GitHub atteint — réessayez dans quelques minutes."
    if status == 404:
        return "Dépôt introuvable (privé sans token, ou URL obsolète)."
    return f"Erreur GitHub (HTTP {status})."


@router.get("/{slug}/branches")
async def list_branches(slug: str, db: AsyncSession = Depends(get_db),
                        _: User = Depends(get_current_admin)):
    """Liste les branches du dépôt GitHub de l'app (lookup à la demande).

    Rien n'est fetché au rendu de la page : c'est le bouton 🌿 du panneau
    branches qui déclenche l'appel. Cache mémoire 30 s par dépôt.
    """
    app = await _get_app_or_404(db, slug)
    base = {"repo_configured": bool(app.git_repo_url), "branches": [],
            "default_branch": None, "cached": False}
    if not app.git_repo_url:
        return {**base, "ok": False,
                "error": "Aucun dépôt git configuré pour cette application."}
    parsed = parse_github_url(app.git_repo_url)
    if not parsed:
        return {**base, "ok": False,
                "error": "URL du dépôt non GitHub — lookup de branches non supporté."}
    owner, repo = parsed
    now = time.monotonic()
    cached = _branches_cache.get((owner, repo))
    if cached and now - cached[0] < BRANCHES_TTL_S:
        return {**cached[1], "cached": True}

    repo_data, repo_status = await fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}", db)
    if repo_status != 200:
        return {**base, "ok": False, "error": _branches_error(repo_status)}
    branches_data, branches_status = await fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=100", db)
    if branches_status != 200:
        return {**base, "ok": False, "error": _branches_error(branches_status)}

    default_branch = repo_data.get("default_branch")
    branches = [{"name": b.get("name", ""),
                 "commit": (b.get("commit", {}) or {}).get("sha", "")[:8]}
                for b in branches_data if isinstance(b, dict) and b.get("name")]
    # Branche par défaut en tête, le reste trié alphabétiquement (tri stable)
    branches.sort(key=lambda b: (b["name"] != default_branch, b["name"].lower()))
    result = {"ok": True, "repo_configured": True, "default_branch": default_branch,
              "branches": branches, "error": None, "cached": False}
    _branches_cache[(owner, repo)] = (now, result)
    return result
