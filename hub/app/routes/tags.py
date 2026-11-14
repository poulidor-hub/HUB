# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Routes pour la gestion des tags utilisateur globaux.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from datetime import timedelta
import httpx
import time

from app.database import get_db
from app.auth import get_current_admin, get_current_user, get_current_entity, create_access_token
from app.models import User, Application, TagCategory, UserTag, UserTagSeen
from app.schemas import (
    TagCategoryCreate,
    TagCategoryUpdate,
    TagCategoryResponse,
    TagCategoryListResponse,
    UserTagsResponse,
    UserTagsUpdate,
    UserTagsAcknowledge,
    UserTagsAcknowledgeResponse,
)
from app.routes.events import notify_event

router = APIRouter(prefix="/api/tags", tags=["Tags"])


# ─── Helpers ────────────────────────────────────────────────────────


async def _get_category_or_404(db: AsyncSession, slug: str) -> TagCategory:
    result = await db.execute(
        select(TagCategory).where(TagCategory.slug == slug)
    )
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail=f"Catégorie '{slug}' introuvable")
    return category


async def _get_user_tags(db: AsyncSession, user_id: int) -> dict[str, list[str]]:
    result = await db.execute(
        select(UserTag)
        .options(selectinload(UserTag.category))
        .where(UserTag.user_id == user_id)
        .order_by(UserTag.id)  # ordre d'insertion : stable avec l'upsert diff
    )
    tags: dict[str, list[str]] = {}
    for ut in result.scalars():
        if ut.category.is_active:
            tags.setdefault(ut.category.slug, []).append(ut.value)
    return tags


async def _get_user_seen(db: AsyncSession, user_id: int) -> list[str]:
    """Slugs des catégories actives déjà présentées (ack) à l'utilisateur."""
    result = await db.execute(
        select(TagCategory.slug)
        .join(UserTagSeen, UserTagSeen.category_id == TagCategory.id)
        .where(UserTagSeen.user_id == user_id, TagCategory.is_active == True)
    )
    return [row[0] for row in result.all()]


async def _get_user_primary(db: AsyncSession, user_id: int) -> dict[str, str]:
    """Secteur principal de l'utilisateur : {category_slug: value}.

    Catégories actives + primary_enabled uniquement (cohérent avec
    User.get_tags_primary côté claims JWT).
    """
    result = await db.execute(
        select(UserTag)
        .options(selectinload(UserTag.category))
        .where(UserTag.user_id == user_id, UserTag.is_primary == True)  # noqa: E712
    )
    primary: dict[str, str] = {}
    for ut in result.scalars():
        if ut.category.is_active and ut.category.primary_enabled:
            primary[ut.category.slug] = ut.value
    return primary


async def _apply_user_tags(
    db: AsyncSession,
    user_id: int,
    tags: dict[str, list[str]],
    valid_categories: dict[str, TagCategory],
    primary: dict[str, str | None] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Upsert diff des tags d'un utilisateur + pose de la primaire.

    - Valeurs hors snapshot (`cat.values`) → `ignored[slug]`, jamais d'erreur
      (valeurs obsolètes suite à un renommage/réimport côté source) ;
    - Catégorie inconnue ou inactive → ignorée silencieusement (les choix
      existants sur catégories désactivées sont préservés — pas de delete-all) ;
    - Écriture ciblée : seules les lignes ajoutées/retirées sont touchées
      (`created_at` préservé sur les lignes inchangées) ;
    - `primary[slug]` : reset `is_primary` sur la (user, catégorie) puis pose
      sur la valeur désignée (respecte l'index partiel unique) ;
      `primary[slug] = None` → suppression explicite.

    Retourne (stored_tags, ignored). Ne commite pas : la route commite.
    """
    stored: dict[str, list[str]] = {}
    ignored: dict[str, list[str]] = {}

    for slug, selected_values in (tags or {}).items():
        cat = valid_categories.get(slug)
        if cat is None:
            # Catégorie inconnue ou inactive : ignorée silencieusement.
            continue
        allowed = set(cat.values or [])
        kept: list[str] = []
        rejected: list[str] = []
        for val in selected_values:
            (kept.append(val) if val in allowed else rejected.append(val))
        if rejected:
            ignored[slug] = rejected
        if kept:
            stored[slug] = kept

    # État existant : {category_id: {value: UserTag}}
    result = await db.execute(select(UserTag).where(UserTag.user_id == user_id))
    by_cat: dict[int, dict[str, UserTag]] = {}
    for ut in result.scalars():
        by_cat.setdefault(ut.category_id, {})[ut.value] = ut

    # 1. Diff par catégorie : retraits ciblés + ajouts.
    for slug, kept in stored.items():
        cat = valid_categories[slug]
        current = by_cat.get(cat.id, {})
        for val, ut in current.items():
            if val not in kept:
                await db.delete(ut)
        for val in kept:
            if val not in current:
                db.add(UserTag(user_id=user_id, category_id=cat.id, value=val))

    # 2. Reset des primaires sur les lignes qui survivent (catégories visées).
    primary_targets: dict[str, str | None] = {
        slug: value for slug, value in (primary or {}).items()
        if slug in valid_categories
    }
    for slug in primary_targets:
        cat = valid_categories[slug]
        current = by_cat.get(cat.id, {})
        for val, ut in current.items():
            if val in stored.get(slug, []) and ut.is_primary:
                ut.is_primary = False

    await db.flush()

    # 3. Pose / suppression de la primaire (lignes fraîchement flushées).
    for slug, value in primary_targets.items():
        cat = valid_categories[slug]
        if value is None:
            # Suppression explicite : plus aucune ligne is_primary.
            await db.execute(
                update(UserTag)
                .where(
                    UserTag.user_id == user_id,
                    UserTag.category_id == cat.id,
                    UserTag.is_primary == True,  # noqa: E712
                )
                .values(is_primary=False)
            )
            continue
        result = await db.execute(
            select(UserTag).where(
                UserTag.user_id == user_id,
                UserTag.category_id == cat.id,
                UserTag.value == value,
            )
        )
        target = result.scalar_one_or_none()
        if target is not None:
            # Ligne absente (valeur filtrée comme obsolète) → primaire non posée.
            target.is_primary = True

    return stored, ignored


# ─── Résolveur valeurs live (exemple-app) ────────────────────────

# Cache mémoire process : {category_id: (monotonic, [valeurs])}. TTL court :
# un redémarrage ou une invalidation (import-m2m, PATCH/POST catégorie)
# suffit à resynchroniser.
_VALUES_TTL = 60
_values_cache: dict[int, tuple[float, list[str]]] = {}


def _invalidate_values_cache(category_id: int) -> None:
    _values_cache.pop(category_id, None)


def _dedup_preserve_order(raw_values: list) -> list[str]:
    """Dédupliqué ordre-préservé, valeurs normalisées en str."""
    seen: set[str] = set()
    values: list[str] = []
    for v in raw_values:
        sv = str(v)
        if sv not in seen:
            seen.add(sv)
            values.append(sv)
    return values


async def _resolve_active_values(
    db: AsyncSession, category: TagCategory
) -> tuple[list[str], str]:
    """Valeurs actives d'une catégorie : fetch live M2M → cache TTL 60 s → snapshot.

    Retourne (valeurs, flag) où flag vaut :
    - "live"  : fetch M2M réussi (snapshot DB rafraîchi au passage) ;
    - "cache" : source injoignable/erreur → dernières valeurs connues
      (cache mémoire si fraîches, sinon snapshot DB) ;
    - "csv"   : catégorie non M2M (pas de fetch, valeurs du snapshot).

    Formats source tolérés (identiques à import_m2m) : liste directe ou
    {"values": [...]}. Les doublons sont dédoublonnés ordre-préservé.
    """
    if category.source != "m2m" or not category.source_app_id:
        return list(category.values or []), "csv"

    now = time.monotonic()
    cached = _values_cache.get(category.id)
    if cached is not None and (now - cached[0]) < _VALUES_TTL:
        return list(cached[1]), "cache"

    app_result = await db.execute(
        select(Application).where(Application.id == category.source_app_id)
    )
    source_app = app_result.scalar_one_or_none()
    if not source_app or not source_app.api_url:
        return list(category.values or []), "cache"

    try:
        target_url = (
            f"{source_app.api_url.rstrip('/')}"
            f"{(category.m2m_endpoint or '/api/tags/export')}"
        )
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                target_url,
                headers=_m2m_headers(source_app.slug),
            )
            if response.status_code != 200:
                raise httpx.RequestError(
                    f"L'app source a retourné {response.status_code}"
                )
            exported = response.json()
            if isinstance(exported, dict) and isinstance(exported.get("values"), list):
                exported_values = exported["values"]
            elif isinstance(exported, list):
                exported_values = exported
            else:
                raise ValueError("Format de réponse M2M invalide")
    except (httpx.RequestError, ValueError, TypeError):
        # Source injoignable ou réponse invalide : repli sur les dernières
        # valeurs connues (snapshot DB), jamais d'erreur exposée au user.
        return list(category.values or []), "cache"

    values = _dedup_preserve_order(exported_values)
    category.values = values
    _values_cache[category.id] = (time.monotonic(), values)
    await db.commit()
    return values, "live"


# ─── Découverte M2M (proxy vers l'app source) ─────────────────────


def _m2m_headers(slug: str) -> dict[str, str]:
    """Mint un token de service éphémère pour appeler l'app source en M2M.

    Même pattern que app_socle_check.py : token court (5 min), périmètre
    minimal (app cible, rôle admin). Les apps du socle exigent un Bearer
    sur /api/hub/data/* (require_role("admin")).
    """
    token, _, _ = create_access_token(
        username="hub-tags-sync",
        apps={slug: "admin"},
        expires_delta=timedelta(minutes=5),
        is_service_account=True,
    )
    return {"Authorization": f"Bearer {token}"}


@router.get("/discover/tables")
async def discover_tables(
    app_id: int = Query(...),
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Proxy : liste les tables de l'app source."""
    app_result = await db.execute(select(Application).where(Application.id == app_id))
    app = app_result.scalar_one_or_none()
    if not app or not app.api_url:
        raise HTTPException(status_code=400, detail="Application introuvable ou sans api_url")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{app.api_url.rstrip('/')}/api/hub/data/tables",
                headers=_m2m_headers(app.slug),
            )
            r.raise_for_status()
            return r.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Erreur de connexion: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"L'application a retourné {e.response.status_code}")


@router.get("/discover/columns")
async def discover_columns(
    app_id: int = Query(...),
    table: str = Query(...),
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Proxy : liste les colonnes d'une table de l'app source."""
    app_result = await db.execute(select(Application).where(Application.id == app_id))
    app = app_result.scalar_one_or_none()
    if not app or not app.api_url:
        raise HTTPException(status_code=400, detail="Application introuvable ou sans api_url")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{app.api_url.rstrip('/')}/api/hub/data/columns",
                params={"table": table},
                headers=_m2m_headers(app.slug),
            )
            r.raise_for_status()
            return r.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Erreur de connexion: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"L'application a retourné {e.response.status_code}")


# ─── Admin endpoints ───────────────────────────────────────────────


@router.get("/categories", response_model=TagCategoryListResponse)
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    result = await db.execute(select(TagCategory).order_by(TagCategory.label))
    categories = result.scalars().all()
    return TagCategoryListResponse(
        categories=[TagCategoryResponse.model_validate(c) for c in categories],
        total=len(categories),
    )


@router.get("/categories/active", response_model=TagCategoryListResponse)
async def list_active_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retourne uniquement les catégories actives (pour le widget utilisateur)."""
    result = await db.execute(
        select(TagCategory).where(TagCategory.is_active == True).order_by(TagCategory.label)
    )
    categories = result.scalars().all()
    return TagCategoryListResponse(
        categories=[TagCategoryResponse.model_validate(c) for c in categories],
        total=len(categories),
    )


@router.post("/categories", response_model=TagCategoryResponse, status_code=201)
async def create_category(
    data: TagCategoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    existing = await db.execute(
        select(TagCategory).where(TagCategory.slug == data.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Le slug '{data.slug}' existe déjà")

    if data.source == "m2m" and data.source_app_id:
        app_result = await db.execute(
            select(Application).where(Application.id == data.source_app_id)
        )
        if not app_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Application source introuvable")

    category = TagCategory(
        slug=data.slug,
        label=data.label,
        is_multiple=data.is_multiple,
        source=data.source,
        source_app_id=data.source_app_id,
        m2m_endpoint=data.m2m_endpoint,
        values=data.values,
        primary_enabled=data.primary_enabled,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)

    _invalidate_values_cache(category.id)

    notify_event("tags_updated", {"action": "category_created", "slug": category.slug})

    return TagCategoryResponse.model_validate(category)


@router.patch("/categories/{slug}", response_model=TagCategoryResponse)
async def update_category(
    slug: str,
    data: TagCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    category = await _get_category_or_404(db, slug)

    if data.label is not None:
        category.label = data.label
    if data.is_multiple is not None:
        category.is_multiple = data.is_multiple
    if data.values is not None:
        category.values = data.values
    if data.is_active is not None:
        category.is_active = data.is_active
    if data.m2m_endpoint is not None:
        category.m2m_endpoint = data.m2m_endpoint

    await db.commit()
    await db.refresh(category)

    _invalidate_values_cache(category.id)

    notify_event("tags_updated", {"action": "category_updated", "slug": slug})

    return TagCategoryResponse.model_validate(category)


@router.delete("/categories/{slug}", status_code=204)
async def delete_category(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    category = await _get_category_or_404(db, slug)
    await db.delete(category)
    await db.commit()

    notify_event("tags_updated", {"action": "category_deleted", "slug": slug})


@router.post("/categories/{slug}/import-csv", response_model=TagCategoryResponse)
async def import_csv(
    slug: str,
    file: UploadFile = File(...),
    mode: str = Query("replace", pattern=r"^(replace|merge)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    category = await _get_category_or_404(db, slug)

    content = await file.read()
    text = content.decode("utf-8-sig").strip()
    new_values = [line.strip() for line in text.splitlines() if line.strip()]

    if not new_values:
        raise HTTPException(status_code=400, detail="Le fichier CSV est vide")

    if mode == "replace":
        category.values = new_values
    else:
        existing = set(category.values or [])
        for v in new_values:
            existing.add(v)
        category.values = sorted(existing)

    await db.commit()
    await db.refresh(category)

    _invalidate_values_cache(category.id)

    notify_event("tags_updated", {"action": "category_csv_import", "slug": slug})

    return TagCategoryResponse.model_validate(category)


@router.post("/categories/{slug}/import-m2m", response_model=TagCategoryResponse)
async def import_m2m(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    category = await _get_category_or_404(db, slug)

    if category.source != "m2m" or not category.source_app_id:
        raise HTTPException(status_code=400, detail="Cette catégorie n'est pas configurée en source M2M")

    app_result = await db.execute(
        select(Application).where(Application.id == category.source_app_id)
    )
    source_app = app_result.scalar_one_or_none()
    if not source_app or not source_app.api_url:
        raise HTTPException(status_code=502, detail="Application source introuvable ou sans api_url")

    import httpx
    try:
        target_url = f"{source_app.api_url.rstrip('/')}{(category.m2m_endpoint or '/api/tags/export')}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                target_url,
                headers=_m2m_headers(source_app.slug),
            )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"L'app source a retourné {response.status_code}"
                )
            exported = response.json()
            # Formats tolérés : liste directe, ou objet {"values": [...]}
            # (format des routes /api/hub/data/values des apps du socle)
            if isinstance(exported, dict) and isinstance(exported.get("values"), list):
                exported_values = exported["values"]
            elif isinstance(exported, list):
                exported_values = exported
            else:
                raise HTTPException(
                    status_code=502,
                    detail="Format de réponse M2M invalide (attendu: liste ou {values: [...]})"
                )
            category.values = [str(v) for v in exported_values]
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Erreur de connexion à l'app source: {e}")

    await db.commit()
    await db.refresh(category)

    _invalidate_values_cache(category.id)

    notify_event("tags_updated", {"action": "category_m2m_import", "slug": slug})

    return TagCategoryResponse.model_validate(category)


# ─── User endpoints ────────────────────────────────────────────────


async def _compute_onboarding_reason(db: AsyncSession, user_id: int) -> str | None:
    """Raison d'affichage de la modale de préférences, évaluée serveur (spéc §5).

    Priorités : newcat → primary_lost → primary_missing → onboarding → None.
    L'évaluation utilise le résolveur live→cache pour la validité de la
    primaire (aucune logique de décision côté client).
    """
    result = await db.execute(
        select(TagCategory)
        .where(TagCategory.is_active == True)  # noqa: E712
        .order_by(TagCategory.label)
    )
    active_cats = result.scalars().all()
    seen = set(await _get_user_seen(db, user_id))
    cats_by_slug = {c.slug: c for c in active_cats}

    # 1. newcat : catégories actives jamais présentées (ack).
    if any(c.slug not in seen for c in active_cats):
        return "newcat"

    tags = await _get_user_tags(db, user_id)
    primary = await _get_user_primary(db, user_id)

    # 2. primary_lost : primaire stockée dont la valeur n'existe plus dans
    #    les valeurs actives de la source (résolveur live → cache).
    for slug, value in primary.items():
        cat = cats_by_slug.get(slug)
        if cat is None:
            return "primary_lost"
        values, _ = await _resolve_active_values(db, cat)
        if value not in values:
            return "primary_lost"

    # 3. primary_missing : catégorie primary_enabled avec tags posés, sans primaire.
    for cat in active_cats:
        if cat.primary_enabled and tags.get(cat.slug) and cat.slug not in primary:
            return "primary_missing"

    # 4. onboarding : aucune valeur dans une catégorie active.
    if not tags:
        return "onboarding"

    return None


@router.get("/my", response_model=UserTagsResponse)
async def get_my_tags(
    with_reason: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tags = await _get_user_tags(db, current_user.id)
    primary = await _get_user_primary(db, current_user.id)
    seen = await _get_user_seen(db, current_user.id)
    reason = (
        await _compute_onboarding_reason(db, current_user.id)
        if with_reason
        else None
    )
    return UserTagsResponse(
        tags=tags, primary=primary, onboarding_reason=reason, seen_categories=seen
    )


@router.put("/my", response_model=UserTagsResponse)
async def update_my_tags(
    data: UserTagsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Garde structurelle inchangée : une catégorie single-select n'accepte qu'une valeur.
    all_categories = await db.execute(
        select(TagCategory).where(TagCategory.is_active == True)
    )
    valid_categories = {c.slug: c for c in all_categories.scalars()}

    for slug, selected_values in data.tags.items():
        cat = valid_categories.get(slug)
        if cat is not None and not cat.is_multiple and len(selected_values) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"La catégorie '{slug}' n'accepte qu'une seule valeur"
            )

    # Validation primaire — seul cas 400 métier avec la garde single-select :
    # (a) la valeur doit figurer dans tags[slug] du même payload,
    # (b) la catégorie doit être primary_enabled.
    if data.primary:
        for slug, value in data.primary.items():
            if value is None:
                continue  # suppression explicite : rien à valider
            cat = valid_categories.get(slug)
            if cat is None or not cat.primary_enabled:
                raise HTTPException(
                    status_code=400,
                    detail=f"La catégorie '{slug}' n'accepte pas de secteur principal"
                )
            if slug not in data.tags or value not in data.tags[slug]:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Le secteur principal '{value}' doit faire partie des "
                        f"valeurs cochées pour '{slug}'"
                    )
                )

    # Upsert diff : valeurs inconnues filtrées dans `ignored`, catégories
    # inactives préservées (jamais de delete-all).
    stored, ignored = await _apply_user_tags(
        db, current_user.id, data.tags, valid_categories, data.primary
    )
    await db.commit()

    tags = await _get_user_tags(db, current_user.id)
    primary = await _get_user_primary(db, current_user.id)
    seen = await _get_user_seen(db, current_user.id)

    notify_event("tags_updated", {
        "action": "user_tags_changed",
        "user_id": current_user.id,
        "username": current_user.username,
    })

    return UserTagsResponse(
        tags=tags, primary=primary, ignored=ignored, seen_categories=seen
    )


@router.post("/my/acknowledge", response_model=UserTagsAcknowledgeResponse)
async def acknowledge_my_tags(
    data: UserTagsAcknowledge,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Marque des catégories comme déjà présentées au user (popup ack). Idempotent."""
    if not data.categories:
        return UserTagsAcknowledgeResponse(acknowledged=[])

    result = await db.execute(
        select(TagCategory).where(
            TagCategory.slug.in_(data.categories), TagCategory.is_active == True
        )
    )
    cats = result.scalars().all()
    if not cats:
        return UserTagsAcknowledgeResponse(acknowledged=[])

    existing = await db.execute(
        select(UserTagSeen.category_id).where(UserTagSeen.user_id == current_user.id)
    )
    already_seen = {row[0] for row in existing.all()}

    inserted_slugs: list[str] = []
    for cat in cats:
        if cat.id in already_seen:
            inserted_slugs.append(cat.slug)
            continue
        db.add(UserTagSeen(user_id=current_user.id, category_id=cat.id))
        inserted_slugs.append(cat.slug)

    await db.commit()

    notify_event("tags_updated", {
        "action": "user_tag_ack",
        "user_id": current_user.id,
        "username": current_user.username,
    })

    return UserTagsAcknowledgeResponse(acknowledged=inserted_slugs)


# ─── M2M endpoint (pour les apps) ──────────────────────────────────


@router.get("/user/{username}", response_model=UserTagsResponse)
async def get_user_tags_m2m(
    username: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_entity),
):
    result = await db.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=f"Utilisateur '{username}' introuvable")

    tags_result = await db.execute(
        select(UserTag)
        .options(selectinload(UserTag.category))
        .where(UserTag.user_id == user.id)
    )
    tags: dict[str, list[str]] = {}
    for ut in tags_result.scalars():
        if ut.category.is_active:
            tags.setdefault(ut.category.slug, []).append(ut.value)

    return UserTagsResponse(tags=tags)


# ─── Admin : gestion des préférences par utilisateur ──────────────


@router.get("/users")
async def list_users_with_tags(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Liste tous les utilisateurs avec leurs préférences actuelles."""
    result = await db.execute(
        select(User)
        .options(selectinload(User.tags).selectinload(UserTag.category))
        .order_by(User.username)
    )
    users = result.scalars().all()

    out = []
    for u in users:
        tags: dict[str, list[str]] = {}
        for ut in u.tags:
            if ut.category.is_active:
                tags.setdefault(ut.category.slug, []).append(ut.value)
        out.append({"id": u.id, "username": u.username, "tags": tags})
    return out


@router.put("/user/{username}", response_model=UserTagsResponse)
async def set_user_tags(
    username: str,
    data: UserTagsUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Admin définit les préférences d'un utilisateur."""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail=f"Utilisateur '{username}' introuvable")

    # Valider catégories et valeurs
    all_categories = await db.execute(
        select(TagCategory).where(TagCategory.is_active == True)
    )
    valid_categories = {c.slug: c for c in all_categories.scalars()}

    for slug in data.tags:
        if slug not in valid_categories:
            raise HTTPException(status_code=400, detail=f"Catégorie '{slug}' introuvable ou inactive")

    for slug, selected_values in data.tags.items():
        cat = valid_categories[slug]
        allowed = set(cat.values or [])
        for val in selected_values:
            if val not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"La valeur '{val}' n'est pas autorisée pour '{slug}'"
                )
        if not cat.is_multiple and len(selected_values) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"La catégorie '{slug}' n'accepte qu'une seule valeur"
            )

    await db.execute(delete(UserTag).where(UserTag.user_id == user.id))

    for slug, selected_values in data.tags.items():
        cat = valid_categories[slug]
        for val in selected_values:
            db.add(UserTag(user_id=user.id, category_id=cat.id, value=val))

    await db.commit()

    tags = await _get_user_tags(db, user.id)

    notify_event("tags_updated", {
        "action": "user_tags_changed",
        "user_id": user.id,
        "username": user.username,
    })

    return UserTagsResponse(tags=tags)
