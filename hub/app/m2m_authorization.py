"""Garde SA/slug : contrôle que le service account est autorisé pour une application."""
from fastapi import Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import ServiceAccount, Application


async def authorize_service_account_slug(
    *,
    request: Request,
    service_account: ServiceAccount,
    requested_slug: str | None,
    db: AsyncSession,
) -> str:
    """Vérifie que le X-Slug demandé appartient au SA. Retourne le slug résolu.

    En mode compat (défaut), les slugs non possédés sont acceptés avec audit.
    En mode enforced, ils sont refusés (403).
    """
    # Résoudre le slug : X-Slug > premier slug du SA
    resolved = requested_slug
    if not resolved:
        if not service_account.applications:
            raise HTTPException(status_code=403, detail="Service account not linked to any application")
        resolved = service_account.applications[0].slug

    owned = {app.slug for app in service_account.applications}
    if resolved in owned:
        return resolved

    # Slug non possédé — vérifier le mode de l'application ciblée
    result = await db.execute(select(Application).where(Application.slug == resolved))
    target_app = result.scalar_one_or_none()

    if target_app is None or target_app.m2m_enforcement_mode == "enforced":
        raise HTTPException(status_code=403, detail="Service account not authorized for this application")

    # Mode compat : accepter avec audit
    from app.models import AuditLog, utcnow
    db.add(AuditLog(
        action="m2m_slug_mismatch_compat",
        details=f"SA {service_account.client_id} used X-Slug {resolved} without ownership (compat accepted)",
        ip_address=request.client.host if request.client else None,
        created_at=utcnow(),
    ))
    return resolved
