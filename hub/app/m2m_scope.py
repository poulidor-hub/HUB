"""
Périmètre M2M des service accounts (AN-2, banc 2026-09-10, Option B validée).

Trois primitives centrales :

1. `grant_shared_apps_to_sa` : associe les apps partagées
   (`is_shared_service=True`, approuvées) à un SA avec **parité de contexte
   atelier** — un SA prod ne reçoit que des services partagés prod, un SA
   atelier que des ateliers partagés. Remplace les copies inline
   hétérogènes (6 sites n'appliquaient aucun filtre atelier).

2. `sa_apps_claim` : construit le claim JWT `apps` d'un SA en ne retenant
   que les apps `is_active` et `approval_status=approved` — une app
   désactivée/déapprouvée (ou une fuite atelier purgée en base) ne doit
   plus figurer dans les tokens.

3. `purge_workshop_leaks` : purge one-shot des liens N-N croisés
   atelier↔prod existants. Les SA dont le propriétaire n'est pas
   identifiable ET dont les contextes sont mixtes sont laissés intacts
   (décision manuelle requise) et signalés.

La convention d'ownership est celle du reste du code : le nom du SA
contient le slug ou le nom de son app propriétaire (`SA - {app.name}`).
"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, ServiceAccount, ApprovalStatus

logger = logging.getLogger(__name__)


def _is_owner_app(app: Application, sa: ServiceAccount) -> bool:
    """Convention d'ownership SA↔app (cf. apps.is_owner_service_account)."""
    sa_name_lower = sa.name.lower()
    return (
        app.slug in sa_name_lower
        or app.name.lower() in sa_name_lower
        or sa.name == f"SA - {app.name}"
    )


def sa_workshop_context(sa: ServiceAccount) -> Optional[bool]:
    """Contexte atelier du SA : celui de son app propriétaire si identifiable
    (convention de nommage), sinon de sa première app liée, sinon None."""
    apps = list(sa.applications or [])
    if not apps:
        return None
    for app in apps:
        if _is_owner_app(app, sa):
            return bool(app.is_workshop)
    return bool(apps[0].is_workshop)


async def grant_shared_apps_to_sa(
    db: AsyncSession,
    sa: ServiceAccount,
    sa_is_workshop: Optional[bool] = None,
) -> list[str]:
    """Associe les services partagés (is_shared_service=True, approuvées) au
    SA, avec parité de contexte atelier (AN-2).

    Args:
        db: session async.
        sa: service account, `applications` déjà chargé (selectinload).
        sa_is_workshop: contexte à appliquer ; dérivé du SA si None
            (fallback : contexte prod, comme la création manuelle).

    Returns:
        Les slugs des apps effectivement ajoutées.
    """
    # Charger explicitement la relation (lazy='raise' sur ServiceAccount.applications) :
    # l'identity map SQLAlchemy retourne la même instance, pré-chargée.
    if sa.id is not None:
        sa_result = await db.execute(
            select(ServiceAccount)
            .options(selectinload(ServiceAccount.applications))
            .where(ServiceAccount.id == sa.id)
        )
        sa = sa_result.scalar_one()

    if sa_is_workshop is None:
        sa_is_workshop = sa_workshop_context(sa) or False

    result = await db.execute(
        select(Application)
        .where(Application.is_shared_service == True)  # noqa: E712
        .where(Application.approval_status == ApprovalStatus.approved)
    )

    added: list[str] = []
    for shared_app in result.scalars().all():
        if shared_app in sa.applications:
            continue
        if bool(shared_app.is_workshop) != bool(sa_is_workshop):
            # AN-2 : isolation atelier — pas de cross-contexte
            logger.debug(
                "🔒 Service partagé '%s' non accordé à '%s' (contexte atelier %s ≠ %s)",
                shared_app.slug, sa.name, shared_app.is_workshop, sa_is_workshop,
            )
            continue
        sa.applications.append(shared_app)
        added.append(shared_app.slug)
    return added


def _claim_applications(service_account: ServiceAccount) -> list[Application]:
    """Apps éligibles au claim JWT : actives, approuvées et du même contexte
    atelier que le SA (défense en profondeur AN-2 : même si une fuite
    cross-contexte subsiste en base — restauration, import — elle n'est
    jamais émise dans un token)."""
    context = sa_workshop_context(service_account)
    return [
        app for app in (service_account.applications or [])
        if app.is_active
        and getattr(app, "approval_status", None) == ApprovalStatus.approved
        and (context is None or bool(app.is_workshop) == context)
    ]


def sa_apps_claim(service_account: ServiceAccount) -> dict:
    """Claim JWT `apps` d'un SA : {slug: "service"} limité aux apps actives,
    approuvées et du contexte atelier du SA (AN-2)."""
    return {app.slug: "service" for app in _claim_applications(service_account)}


def sa_claim_is_workshop(service_account: ServiceAccount) -> bool:
    """Valeur du flag `is_workshop` du JWT : True si toutes les apps du claim
    sont des ateliers (False si claim vide, cf. comportement historique)."""
    apps = _claim_applications(service_account)
    return all(bool(app.is_workshop) for app in apps) if apps else False


async def purge_workshop_leaks(db: AsyncSession) -> dict:
    """Retire les liens SA↔app croisés atelier↔prod (AN-2, purge one-shot).

    - Contexte du SA : celui de son app propriétaire si identifiable, sinon
      le contexte unique de ses apps (si homogène).
    - SA ambigu (contextes mixtes, propriétaire non identifiable) : laissé
      intact et signalé dans `skipped_ambiguous`.

    Returns:
        {"removed": ["{sa.name} (client): -{slug}", ...],
         "skipped_ambiguous": ["{sa.name} (client)", ...]}
    """
    result = await db.execute(
        select(ServiceAccount).options(selectinload(ServiceAccount.applications))
    )
    removed: list[str] = []
    skipped_ambiguous: list[str] = []

    for sa in result.scalars().unique().all():
        apps = list(sa.applications or [])
        if not apps:
            continue

        owner_app = next((a for a in apps if _is_owner_app(a, sa)), None)
        contexts = {bool(a.is_workshop) for a in apps}
        if owner_app is not None:
            context = bool(owner_app.is_workshop)
        elif len(contexts) == 1:
            context = contexts.pop()
        else:
            skipped_ambiguous.append(f"{sa.name} ({sa.client_id})")
            continue

        leaked = [a for a in apps if bool(a.is_workshop) != context]
        for app in leaked:
            sa.applications.remove(app)
            removed.append(f"{sa.name} ({sa.client_id}): -{app.slug}")

    return {"removed": removed, "skipped_ambiguous": skipped_ambiguous}


def sa_can_admin_read(service_account: ServiceAccount) -> bool:
    """Flag « lecture admin » d'un SA (phase 3). Autorité : DB, re-vérifiée à chaque requête."""
    return bool(service_account.can_admin_read)
