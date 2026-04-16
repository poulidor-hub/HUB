"""
Routes de demande de compte — Inscription utilisateur avec validation admin.

Endpoints publics (sans auth) :
- POST /api/account-requests : Soumettre une demande
- GET /api/account-requests/{id}/status : Polling du statut (avec polling_token)

Endpoints admin :
- GET /api/account-requests : Lister les demandes
- GET /api/account-requests/count : Compter les demandes pending
- POST /api/account-requests/{id}/approve : Approuver une demande
- POST /api/account-requests/{id}/reject : Rejeter une demande
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Optional
import secrets
import logging

from app.database import get_db
from app.models import (
    User, UserAppRole, AccountRequest, AppSetting, UserMessage,
    user_profiles, utcnow,
)
from app.auth import get_password_hash, get_current_admin
from app.schemas import (
    AccountRequestCreate, AccountRequestResponse,
    AccountRequestStatusResponse, AccountRequestReview,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account-requests", tags=["Account Requests"])


# =============================================================================
# ENDPOINTS PUBLICS (sans authentification)
# =============================================================================

@router.post("/", status_code=status.HTTP_201_CREATED)
async def submit_account_request(
    payload: AccountRequestCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Soumet une demande de création de compte.
    Vérifie l'unicité du username parmi les users existants et les demandes pending.
    """
    # Vérifier que les demandes de compte sont activées
    setting_result = await db.execute(
        select(AppSetting).where(AppSetting.key == "account_requests_enabled")
    )
    setting = setting_result.scalar_one_or_none()
    if not setting or setting.value != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Les demandes de compte sont actuellement désactivées"
        )

    # Username déjà normalisé (strip + lower) par le schéma Pydantic
    clean_username = payload.username

    # Vérifier que le username n'existe pas déjà dans les users
    existing_user = await db.execute(
        select(User).where(User.username == clean_username)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cet identifiant est déjà utilisé par un compte existant"
        )

    # Vérifier qu'il n'y a pas de demande pending avec ce username
    existing_request = await db.execute(
        select(AccountRequest).where(
            AccountRequest.username == clean_username,
            AccountRequest.status == "pending"
        )
    )
    if existing_request.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Une demande est déjà en cours pour cet identifiant"
        )

    # Créer la demande
    polling_token = secrets.token_hex(32)
    request = AccountRequest(
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        username=clean_username,
        hashed_password=get_password_hash(payload.password),
        polling_token=polling_token,
    )
    db.add(request)
    await db.flush()

    request_id = request.id
    first_name = request.first_name
    last_name = request.last_name
    username = request.username

    await db.commit()

    # Notifier tous les admins
    await _notify_admins_new_request(db, request_id, first_name, last_name, username)

    # Émettre SSE pour rafraîchir l'onglet demandes côté admin
    try:
        from app.routes.events import broadcast_event
        await broadcast_event("account_requests_updated", {"action": "new_request", "request_id": request_id})
    except ImportError:
        pass

    return {"request_id": request_id, "polling_token": polling_token}


@router.get("/{request_id}/status")
async def poll_request_status(
    request_id: int,
    token: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db)
):
    """
    Polling du statut d'une demande (endpoint public, sécurisé par polling_token).
    """
    result = await db.execute(
        select(AccountRequest).where(
            AccountRequest.id == request_id,
            AccountRequest.polling_token == token
        )
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demande introuvable")

    return AccountRequestStatusResponse(
        status=request.status,
        rejection_reason=request.rejection_reason,
        auto_login_token=(
            request.auto_login_token
            if request.status == "approved" else None
        )
    )


@router.post("/auto-login")
async def auto_login_with_token(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Échange un auto_login_token à usage unique contre un JWT + refresh token.
    Remplace le stockage du mot de passe côté client.
    """
    from app.auth import create_access_token, create_refresh_token
    from app.routes.audit import log_action, start_session

    alt = payload.get("auto_login_token", "").strip()
    if not alt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="auto_login_token requis"
        )

    result = await db.execute(
        select(AccountRequest).where(
            AccountRequest.auto_login_token == alt,
            AccountRequest.status == "approved"
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton invalide ou déjà utilisé"
        )

    # Invalider le jeton (usage unique)
    req.auto_login_token = None
    await db.flush()

    # Récupérer l'utilisateur créé
    user_result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles)
            .selectinload(UserAppRole.application)
        )
        .where(User.username == req.username)
    )
    user = user_result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Compte introuvable ou désactivé"
        )

    apps = user.get_apps_with_roles()
    token, expire, jti = create_access_token(
        username=user.username,
        apps=apps
    )

    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")

    refresh = await create_refresh_token(
        db,
        user_id=user.id,
        ip_address=ip,
        user_agent=ua
    )

    await start_session(
        db,
        user_id=user.id,
        ip_address=ip,
        user_agent=ua
    )

    await log_action(
        db, user.id, "auto_login_after_approval",
        target=f"user:{user.username}",
        details=f"Connexion automatique après approbation"
            f" (demande #{req.id})",
        notify_sse=False
    )
    await db.commit()

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expire.isoformat(),
        "refresh_token": refresh,
        "must_change_password": user.must_change_password,
    }


# =============================================================================
# ENDPOINTS ADMIN
# =============================================================================

@router.get("/count")
async def get_pending_count(
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Retourne le nombre de demandes en attente."""
    result = await db.execute(
        select(func.count(AccountRequest.id)).where(AccountRequest.status == "pending")
    )
    return {"count": result.scalar() or 0}


@router.get("/")
async def list_account_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Liste les demandes de compte avec pagination et filtre par statut."""
    query = select(AccountRequest).options(selectinload(AccountRequest.reviewed_by))
    count_query = select(func.count(AccountRequest.id))

    if status_filter:
        query = query.where(AccountRequest.status == status_filter)
        count_query = count_query.where(AccountRequest.status == status_filter)

    # Total
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    import math
    pages = max(1, math.ceil(total / per_page))

    # Paginer
    result = await db.execute(
        query.order_by(AccountRequest.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    requests = result.scalars().all()

    items = []
    for req in requests:
        reviewed_by_name = None
        try:
            if req.reviewed_by:
                reviewed_by_name = req.reviewed_by.username
        except Exception:
            pass
        items.append(AccountRequestResponse(
            id=req.id,
            first_name=req.first_name,
            last_name=req.last_name,
            username=req.username,
            status=req.status,
            rejection_reason=req.rejection_reason,
            reviewed_by=reviewed_by_name,
            reviewed_at=req.reviewed_at,
            created_at=req.created_at,
        ))

    return {"requests": items, "total": total, "page": page, "pages": pages}


@router.post("/{request_id}/approve")
async def approve_account_request(
    request_id: int,
    payload: AccountRequestReview,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Approuve une demande de compte : crée le User, assigne les profils optionnels.
    """
    result = await db.execute(
        select(AccountRequest).where(AccountRequest.id == request_id)
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demande introuvable")
    if request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette demande a déjà été traitée")

    # Vérifier que le username n'a pas été pris entre-temps
    existing_user = await db.execute(
        select(User).where(User.username == request.username)
    )
    if existing_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un utilisateur avec cet identifiant existe déjà"
        )

    # Créer le User avec le mot de passe hashé de la demande
    user = User(
        username=request.username,
        hashed_password=request.hashed_password,
        is_admin=False,
        must_change_password=False,
        can_change_password=True,
    )
    db.add(user)
    await db.flush()

    # Assigner les profils si fournis
    if payload.profile_ids:
        from app.routes.profiles import _sync_user_profile_roles
        for pid in payload.profile_ids:
            await db.execute(
                user_profiles.insert().values(user_id=user.id, profile_id=pid)
            )
        await _sync_user_profile_roles(db, user.id)

    # Mettre à jour la demande
    request.status = "approved"
    request.reviewed_by_id = current_admin.id
    request.reviewed_at = utcnow()
    request.auto_login_token = secrets.token_hex(32)

    await db.commit()

    # Audit
    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "account_request_approved",
            target=f"user:{request.username}",
            details=f"Demande #{request_id} approuvée, user #{user.id} créé",
            notify_sse=False
        )
        await db.commit()
    except Exception:
        pass

    # SSE : notifier le rafraîchissement
    try:
        from app.routes.events import broadcast_event, notify_stats_update
        await broadcast_event("account_requests_updated", {
            "action": "reviewed", "request_id": request_id, "status": "approved"
        })
        from app.routes.events import notify_event
        notify_event("users_updated", {"action": "created", "user_id": user.id, "username": user.username})
        notify_stats_update()
    except ImportError:
        pass

    return {"message": "Demande approuvée", "user_id": user.id, "username": request.username}


@router.post("/{request_id}/reject")
async def reject_account_request(
    request_id: int,
    payload: AccountRequestReview,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Rejette une demande de compte avec une raison obligatoire."""
    if not payload.rejection_reason or not payload.rejection_reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La raison du refus est obligatoire"
        )

    result = await db.execute(
        select(AccountRequest).where(AccountRequest.id == request_id)
    )
    request = result.scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demande introuvable")
    if request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette demande a déjà été traitée")

    request.status = "rejected"
    request.rejection_reason = payload.rejection_reason.strip()
    request.reviewed_by_id = current_admin.id
    request.reviewed_at = utcnow()

    await db.commit()

    # Audit
    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "account_request_rejected",
            target=f"user:{request.username}",
            details=f"Demande #{request_id} refusée: {request.rejection_reason}",
            notify_sse=False
        )
        await db.commit()
    except Exception:
        pass

    # SSE : notifier le rafraîchissement
    try:
        from app.routes.events import broadcast_event
        await broadcast_event("account_requests_updated", {
            "action": "reviewed", "request_id": request_id, "status": "rejected"
        })
    except ImportError:
        pass

    return {"message": "Demande refusée", "request_id": request_id}


# =============================================================================
# HELPERS
# =============================================================================

async def _notify_admins_new_request(
    db: AsyncSession, request_id: int, first_name: str, last_name: str, username: str
):
    """Envoie une notification (UserMessage) à chaque admin pour une nouvelle demande."""
    try:
        from app.routes.events import send_to_user
        from app.routes.messages import _message_to_brief, _send_user_message_sse

        # Récupérer tous les admins actifs
        result = await db.execute(
            select(User).where(User.is_admin == True, User.is_active == True)
        )
        admins = result.scalars().all()

        for admin in admins:
            msg = UserMessage(
                sender_type="system",
                target_type="user",
                target_user_id=admin.id,
                target_username=admin.username,
                priority="warning",
                title="Nouvelle demande de compte",
                body=f"{first_name} {last_name} ({username}) demande la création d'un compte.",
                action_url="/users#requests",
                action_label="Voir les demandes",
            )
            db.add(msg)
            await db.flush()

            # SSE ciblé
            await _send_user_message_sse(admin.id, _message_to_brief(msg))

        await db.commit()
    except Exception as e:
        logger.warning(f"Erreur lors de la notification des admins: {e}")
