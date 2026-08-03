"""
Routes pour la gestion des tags utilisateur globaux.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.auth import get_current_admin, get_current_user
from app.models import User, Application, TagCategory, UserTag
from app.schemas import (
    TagCategoryCreate,
    TagCategoryUpdate,
    TagCategoryResponse,
    TagCategoryListResponse,
    UserTagsResponse,
    UserTagsUpdate,
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
    )
    tags: dict[str, list[str]] = {}
    for ut in result.scalars():
        if ut.category.is_active:
            tags.setdefault(ut.category.slug, []).append(ut.value)
    return tags


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
        values=data.values,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)

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

    await db.commit()
    await db.refresh(category)

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
        target_url = f"{source_app.api_url.rstrip('/')}/api/tags/export"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(target_url)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"L'app source a retourné {response.status_code}"
                )
            exported_values = response.json()
            if not isinstance(exported_values, list):
                raise HTTPException(status_code=502, detail="Format de réponse M2M invalide (attendu: liste)")
            category.values = [str(v) for v in exported_values]
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Erreur de connexion à l'app source: {e}")

    await db.commit()
    await db.refresh(category)

    notify_event("tags_updated", {"action": "category_m2m_import", "slug": slug})

    return TagCategoryResponse.model_validate(category)


# ─── User endpoints ────────────────────────────────────────────────


@router.get("/my", response_model=UserTagsResponse)
async def get_my_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tags = await _get_user_tags(db, current_user.id)
    return UserTagsResponse(tags=tags)


@router.put("/my", response_model=UserTagsResponse)
async def update_my_tags(
    data: UserTagsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Valider que toutes les catégories et valeurs existent
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
                    detail=f"La valeur '{val}' n'est pas autorisée pour la catégorie '{slug}'"
                )
        if not cat.is_multiple and len(selected_values) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"La catégorie '{slug}' n'accepte qu'une seule valeur"
            )

    await db.execute(
        delete(UserTag).where(UserTag.user_id == current_user.id)
    )

    for slug, selected_values in data.tags.items():
        cat = valid_categories[slug]
        for val in selected_values:
            db.add(UserTag(user_id=current_user.id, category_id=cat.id, value=val))

    await db.commit()

    tags = await _get_user_tags(db, current_user.id)

    notify_event("tags_updated", {
        "action": "user_tags_changed",
        "user_id": current_user.id,
        "username": current_user.username,
    })

    return UserTagsResponse(tags=tags)


# ─── M2M endpoint (pour les apps) ──────────────────────────────────


@router.get("/user/{username}", response_model=UserTagsResponse)
async def get_user_tags_m2m(
    username: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
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
