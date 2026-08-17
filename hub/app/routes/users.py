from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Union
from pydantic import BaseModel
import math
import csv
import io

from app.database import get_db
from app.routes.events import notify_event, notify_stats_update
from app.models import (
    User, Application, UserAppRole, ServiceAccount,
    AppRole, DEFAULT_ROLES, Profile, ProfileAppRole, user_profiles, AuditLog,
    highest_role,
)
from app.schemas import (
    UserCreate, UserUpdate, UserResponse, PaginatedUsersResponse,
    BulkUsersCreateRequest, BulkUsersCreateResponse, BulkUserCreateResult,
    BulkUsersRolesUpdateRequest, BulkUsersRolesUpdateResponse, BulkUserRoleUpdateResult,
    BulkUsersDeleteRequest, BulkUsersDeleteResponse, BulkUserDeleteResult,
    BulkAppRolesAssignRequest, BulkAppRolesAssignResponse, BulkAppRoleAssignResult,
    BulkRemoveAppAccessRequest, BulkRemoveAppAccessResponse, BulkRemoveAppAccessResult,
    CsvUserPreviewLine, CsvPreviewResponse, ProfileBriefResponse,
    M2MObservedUsersRolesRequest, M2MObservedAppRole, M2MObservedUserData,
    M2MUserSearchItem, M2MUserSearchResponse
)
from app.auth import get_current_admin, get_current_entity, get_password_hash, revoke_all_user_tokens

router = APIRouter(prefix="/api/users", tags=["Users"])


class UserAppRoleAssignment(BaseModel):
    """Attribution d'un rôle à un utilisateur pour une application"""
    app_id: int
    role: str = "user"
    reset: bool = False  # True : réinitialiser au rôle attendu par le profil


class UserAppsUpdate(BaseModel):
    """Liste des applications avec rôles pour un utilisateur"""
    assignments: List[UserAppRoleAssignment]


@router.post("/m2m/app-roles/bulk", response_model=Dict[str, M2MObservedUserData])
async def m2m_bulk_user_app_roles(
    request: M2MObservedUsersRolesRequest,
    db: AsyncSession = Depends(get_db),
    current_entity: Union[User, ServiceAccount] = Depends(get_current_entity)
):
    """
    Retourne les rôles applicatifs observables pour une liste d'identifiants utilisateur.

    Endpoint réservé aux Service Accounts (authentification M2M) et limité :
    - aux applications actives
    - aux applications autorisées pour le service account appelant
    """
    if isinstance(current_entity, User):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cet endpoint est réservé aux Service Accounts (authentification M2M)"
        )

    identifiers: List[str] = []
    seen = set()
    for identifier in request.identifiers:
        if identifier in seen:
            continue
        seen.add(identifier)
        identifiers.append(identifier)

    response_map: Dict[str, M2MObservedUserData] = {
        identifier: M2MObservedUserData(profiles=[], apps=[])
        for identifier in identifiers
    }

    if not identifiers:
        return response_map

    users_result = await db.execute(
        select(User.username, Profile.id, Profile.name, Profile.color)
        .join(user_profiles, user_profiles.c.user_id == User.id)
        .join(Profile, user_profiles.c.profile_id == Profile.id)
        .where(User.username.in_(identifiers))
    )
    for username, profile_id, profile_name, profile_color in users_result.all():
        response_map[username].profiles.append(ProfileBriefResponse(
            id=profile_id,
            name=profile_name,
            color=profile_color or "#6c757d"
        ))

    allowed_app_slugs = {
        app.slug for app in current_entity.applications
    }
    if not allowed_app_slugs:
        return response_map

    result = await db.execute(
        select(User.username, Application.slug, UserAppRole.role)
        .join(UserAppRole, User.id == UserAppRole.user_id)
        .join(Application, Application.id == UserAppRole.app_id)
        .where(User.username.in_(identifiers))
        .where(Application.is_active == True)
        .where(Application.slug.in_(allowed_app_slugs))
        .order_by(User.username, Application.slug)
    )

    for username, app_slug, role in result.all():
        response_map[username].apps.append(
            M2MObservedAppRole(app_slug=app_slug, role=role)
        )

    return response_map


@router.get("/m2m/search", response_model=M2MUserSearchResponse)
async def m2m_search_users(
    q: Optional[str] = Query(default=None, max_length=100, description="Filtre prefixe/contient sur username ou email"),
    app_slug: Optional[str] = Query(default=None, description="Restreindre à une application précise (doit être autorisée pour le SA)"),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_entity: Union[User, ServiceAccount] = Depends(get_current_entity)
):
    """
    Recherche d'utilisateurs Hub pour les Service Accounts.

    Retourne uniquement les utilisateurs actifs ayant un rôle sur l'une des
    applications autorisées pour le service account appelant. Si `app_slug`
    est fourni, restreint aux utilisateurs de cette application (et refuse
    si l'app n'est pas autorisée pour le SA).
    """
    if isinstance(current_entity, User):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cet endpoint est réservé aux Service Accounts (authentification M2M)"
        )

    allowed_app_slugs = {app.slug for app in current_entity.applications if app.is_active}
    if not allowed_app_slugs:
        return M2MUserSearchResponse(count=0, users=[])

    target_slugs = allowed_app_slugs
    if app_slug:
        if app_slug not in allowed_app_slugs:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Application '{app_slug}' non autorisée pour ce service account"
            )
        target_slugs = {app_slug}

    stmt = (
        select(User.username, User.email, Application.slug, UserAppRole.role)
        .join(UserAppRole, UserAppRole.user_id == User.id)
        .join(Application, Application.id == UserAppRole.app_id)
        .where(User.is_active == True)
        .where(Application.is_active == True)
        .where(Application.slug.in_(target_slugs))
    )

    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.email.ilike(like)))

    stmt = stmt.order_by(User.username, Application.slug).limit(limit * 4)

    result = await db.execute(stmt)
    seen_usernames = set()
    items: List[M2MUserSearchItem] = []
    for username, email, slug, role in result.all():
        if username in seen_usernames:
            continue
        seen_usernames.add(username)
        items.append(M2MUserSearchItem(
            username=username,
            email=email,
            app_slug=slug,
            role=role,
        ))
        if len(items) >= limit:
            break

    return M2MUserSearchResponse(count=len(items), users=items)


@router.get("/")
async def list_users(
    page: int = Query(default=0, ge=0, description="Page (0 = pas de pagination, retourne tout)"),
    per_page: int = Query(default=50, ge=10, le=200),
    search: Optional[str] = Query(default=None, description="Recherche dans username, email, noms d'apps"),
    role: Optional[str] = Query(default=None, description="Filtrer par rôle app (admin, manager, user, viewer)"),
    app_slug: Optional[str] = Query(default=None, description="Filtrer par slug d'application"),
    profile_id: Optional[int] = Query(default=None, description="Filtrer par profil"),
    is_active: Optional[bool] = Query(default=None, description="Filtrer par statut actif"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Liste les utilisateurs avec pagination et filtres.
    - page=0 : rétro-compatible, retourne TOUS les utilisateurs (List[UserResponse])
    - page>=1 : pagination serveur avec filtres, retourne PaginatedUsersResponse
    """
    # Requête de base avec options de chargement
    load_options = [
        selectinload(User.app_roles).selectinload(UserAppRole.application),
        selectinload(User.profiles)
    ]

    # --- Mode rétro-compatible (page=0) : tout retourner ---
    if page == 0:
        result = await db.execute(
            select(User).options(*load_options)
        )
        users = result.scalars().all()
        return [UserResponse.from_orm_model(u) for u in users]

    # --- Mode paginé (page >= 1) ---
    # Construire la requête avec filtres
    base_query = select(User)

    # Filtre par statut actif
    if is_active is not None:
        base_query = base_query.where(User.is_active == is_active)

    # Filtre par profil (via table de jonction)
    if profile_id is not None:
        if profile_id == -1:
            # -1 = utilisateurs sans profil
            base_query = base_query.where(
                ~User.id.in_(
                    select(user_profiles.c.user_id)
                )
            )
        else:
            base_query = base_query.where(
                User.id.in_(
                    select(user_profiles.c.user_id).where(
                        user_profiles.c.profile_id == profile_id
                    )
                )
            )

    # Filtre par app_slug et/ou rôle (nécessite un join)
    if app_slug or role:
        base_query = base_query.join(UserAppRole, User.id == UserAppRole.user_id)
        if app_slug:
            base_query = base_query.join(
                Application, UserAppRole.app_id == Application.id
            ).where(Application.slug == app_slug)
        if role:
            base_query = base_query.where(UserAppRole.role == role)
        # Distinct car le join peut dupliquer les users
        base_query = base_query.distinct()

    # Recherche texte (username, email, noms d'apps)
    if search:
        search_term = f"%{search}%"
        # Sous-requête pour les users qui matchent par app_name
        app_match_subq = (
            select(UserAppRole.user_id)
            .join(Application, UserAppRole.app_id == Application.id)
            .where(Application.name.ilike(search_term))
        ).scalar_subquery()
        
        base_query = base_query.where(
            or_(
                User.username.ilike(search_term),
                User.email.ilike(search_term),
                User.id.in_(app_match_subq)
            )
        )

    # Compter le total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    pages = max(1, math.ceil(total / per_page))

    # Paginer et charger
    paginated_query = (
        base_query
        .options(*load_options)
        .order_by(User.username)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(paginated_query)
    users = result.scalars().unique().all()

    return PaginatedUsersResponse(
        users=[UserResponse.from_orm_model(u) for u in users],
        total=total,
        page=page,
        per_page=per_page,
        pages=pages
    )


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Crée un nouvel utilisateur (admin uniquement)"""
    # Vérifier si l'utilisateur existe
    existing = await db.execute(
        select(User).where(User.username == user_data.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce nom d'utilisateur existe déjà"
        )
    
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        is_admin=user_data.is_admin,
        must_change_password=user_data.must_change_password,
        can_change_password=user_data.can_change_password
    )
    db.add(user)
    await db.commit()
    
    # Recharger avec les relations (vide pour un nouvel utilisateur)
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles)
        )
        .where(User.id == user.id)
    )
    user = result.scalar_one()
    
    # Notifier les clients SSE du nouvel utilisateur
    notify_event("users_updated", {"action": "created", "user_id": user.id, "username": user.username})
    notify_stats_update()
    
    return UserResponse.from_orm_model(user)


# ===== Import CSV =====

@router.get("/csv/template")
async def download_csv_template(
    _: User = Depends(get_current_admin)
):
    """
    Télécharge un template CSV pour l'import d'utilisateurs en masse.
    Colonnes : username, password, profile
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["username", "password", "profile"])
    writer.writerow(["# jean.dupont", "MotDePasse123", "Cadre"])
    writer.writerow(["# marie.martin", "Secret456!", "Agent"])
    content = output.getvalue()
    
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import_users_template.csv"}
    )


@router.post("/csv/preview", response_model=CsvPreviewResponse)
async def preview_csv_import(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Analyse un fichier CSV et retourne un aperçu de ce qui sera créé.
    Ne crée aucun utilisateur — lecture seule.
    
    Le CSV doit avoir 3 colonnes : username, password, profile
    - Le profil doit correspondre exactement à un profil existant (case-insensitive)
    - Si le profil n'est pas trouvé, l'utilisateur sera créé sans profil (warning)
    - Tous les utilisateurs seront créés avec le rôle "user" et must_change_password=true
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier doit être au format CSV (.csv)"
        )
    
    # Lire le contenu du fichier (limite 1 Mo)
    content_bytes = await file.read()
    if len(content_bytes) > 1_048_576:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier CSV ne doit pas dépasser 1 Mo"
        )
    
    # Décoder le contenu (supporter UTF-8 BOM)
    try:
        content = content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = content_bytes.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Encodage du fichier non supporté. Utilisez UTF-8 ou Latin-1."
            )
    
    # Parser le CSV (supporter , et ;)
    if ";" in content.split("\n")[0]:
        dialect_delimiter = ";"
    else:
        dialect_delimiter = ","
    
    reader = csv.reader(io.StringIO(content), delimiter=dialect_delimiter)
    rows = list(reader)
    
    if len(rows) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier CSV est vide"
        )
    
    # Vérifier les headers
    headers = [h.strip().lower() for h in rows[0]]
    if headers[:3] != ["username", "password", "profile"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier CSV doit avoir les colonnes : username, password, profile"
        )
    
    # Filtrer les lignes de données (ignorer les commentaires et les lignes vides)
    data_rows = []
    for row in rows[1:]:
        if not row or all(cell.strip() == "" for cell in row):
            continue
        if row[0].strip().startswith("#"):
            continue
        data_rows.append(row)
    
    if len(data_rows) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier CSV ne contient aucune ligne de données"
        )
    
    if len(data_rows) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier CSV ne doit pas dépasser 100 lignes de données"
        )
    
    # Charger les profils existants
    profiles_result = await db.execute(select(Profile))
    profiles = profiles_result.scalars().all()
    profiles_by_name = {p.name.lower(): p for p in profiles}
    
    # Charger les usernames existants
    existing_result = await db.execute(select(User.username))
    existing_usernames = {u.lower() for u in existing_result.scalars().all()}
    
    # Tracker les doublons dans le CSV
    csv_usernames_seen = {}
    
    lines = []
    total_valid = 0
    total_errors = 0
    total_warnings = 0
    
    for idx, row in enumerate(data_rows, start=1):
        errors = []
        warnings = []
        
        # Extraire les valeurs
        username = row[0].strip() if len(row) > 0 else ""
        password = row[1].strip() if len(row) > 1 else ""
        profile_name = row[2].strip() if len(row) > 2 else ""
        
        # Valider username
        if not username:
            errors.append("Nom d'utilisateur requis")
        elif len(username) < 3:
            errors.append("Nom d'utilisateur trop court (min 3 caractères)")
        elif len(username) > 50:
            errors.append("Nom d'utilisateur trop long (max 50 caractères)")
        elif username.lower() in existing_usernames:
            errors.append(f"L'utilisateur '{username}' existe déjà")
        elif username.lower() in csv_usernames_seen:
            errors.append(f"Doublon dans le CSV (ligne {csv_usernames_seen[username.lower()]})")
        
        if username and username.lower() not in csv_usernames_seen:
            csv_usernames_seen[username.lower()] = idx
        
        # Valider password
        if not password:
            errors.append("Mot de passe requis")
        elif len(password) < 8:
            errors.append("Mot de passe trop court (min 8 caractères)")
        
        # Résoudre le profil
        profile_matched = None
        profile_ignored = False
        if profile_name:
            profile_obj = profiles_by_name.get(profile_name.lower())
            if profile_obj:
                profile_matched = ProfileBriefResponse(
                    id=profile_obj.id,
                    name=profile_obj.name,
                    color=profile_obj.color
                )
            else:
                profile_ignored = True
                warnings.append(f"Profil '{profile_name}' non trouvé, sera ignoré")
        
        is_valid = len(errors) == 0
        if is_valid:
            total_valid += 1
        else:
            total_errors += 1
        if warnings:
            total_warnings += len(warnings)
        
        lines.append(CsvUserPreviewLine(
            line_number=idx,
            username=username,
            password_length=len(password),
            profile_requested=profile_name if profile_name else None,
            profile_matched=profile_matched,
            profile_ignored=profile_ignored,
            is_valid=is_valid,
            errors=errors,
            warnings=warnings
        ))
    
    available_profiles = [
        ProfileBriefResponse(id=p.id, name=p.name, color=p.color)
        for p in profiles
    ]
    
    return CsvPreviewResponse(
        lines=lines,
        total_lines=len(lines),
        total_valid=total_valid,
        total_errors=total_errors,
        total_warnings=total_warnings,
        available_profiles=available_profiles
    )


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère un utilisateur par ID (admin uniquement)"""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles)
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    return UserResponse.from_orm_model(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Met à jour un utilisateur (admin uniquement)"""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles)
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    update_data = user_data.model_dump(exclude_unset=True)
    
    # Extraire profile_ids pour traitement séparé
    profile_ids = update_data.pop("profile_ids", None)
    
    # Détecter si l'utilisateur est en train d'être désactivé
    is_being_deactivated = "is_active" in update_data and update_data["is_active"] == False and user.is_active == True
    
    if "password" in update_data:
        update_data["hashed_password"] = get_password_hash(update_data.pop("password"))
    
    for field, value in update_data.items():
        setattr(user, field, value)
    
    # Gérer les profils si fournis
    if profile_ids is not None:
        from app.routes.profiles import _sync_user_profile_roles
        # Supprimer toutes les associations existantes
        await db.execute(
            delete(user_profiles).where(user_profiles.c.user_id == user_id)
        )
        # Ajouter les nouvelles associations
        if profile_ids:
            for pid in profile_ids:
                await db.execute(
                    user_profiles.insert().values(user_id=user_id, profile_id=pid)
                )
        # Recalculer les rôles
        await _sync_user_profile_roles(db, user_id)
    
    await db.commit()
    
    # Si l'utilisateur est désactivé, révoquer tous ses tokens et notifier sa déconnexion forcée
    if is_being_deactivated:
        await revoke_all_user_tokens(db, user_id)
        # Notifier via SSE pour forcer la déconnexion côté client
        notify_event("force_logout", {"user_id": user_id, "username": user.username, "reason": "account_disabled"})
    
    # Recharger avec les relations
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles)
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one()
    
    # Notifier les clients SSE de la modification
    notify_event("users_updated", {"action": "updated", "user_id": user.id, "username": user.username})
    
    return UserResponse.from_orm_model(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """Supprime un utilisateur (admin uniquement)"""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas vous supprimer vous-même"
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    username = user.username  # Sauvegarder avant suppression
    await db.delete(user)
    await db.commit()
    
    # Notifier les clients SSE de la suppression
    notify_event("users_updated", {"action": "deleted", "user_id": user_id, "username": username})
    notify_stats_update()


def _best_profile_roles_by_app(user: User) -> Dict[int, Dict]:
    """Rôle attendu par app en combinant tous les profils de l'utilisateur.

    Nécessite user.profiles chargé avec Profile.app_roles (selectinload).
    Retourne {app_id: {"role": <max>, "profile_id": <id du profil porteur>}}.
    """
    best: Dict[int, Dict] = {}
    for profile in user.profiles:
        for par in profile.app_roles:
            existing = best.get(par.app_id)
            if existing is None:
                best[par.app_id] = {"role": par.role, "profile_id": profile.id}
            else:
                top = highest_role([existing["role"], par.role])
                if top != existing["role"]:
                    best[par.app_id] = {"role": top, "profile_id": profile.id}
    return best


async def _apply_user_app_assignments(
    db: AsyncSession,
    request: Request,
    admin: User,
    user: User,
    assignments: List[UserAppRoleAssignment],
    apps_by_id: Dict[int, Application],
) -> List[Dict]:
    """Applique des assignments d'apps/rôles pour un utilisateur (sémantique override).

    - Lignes source='manual' absentes du payload : supprimées (remplacement).
    - App couverte par un profil, reset=False → ligne 'override:<pid>' au rôle choisi.
    - App couverte par un profil, reset=True  → rôle attendu du profil, 'profile:<pid>'.
    - App non couverte → ligne 'manual' (reset ignoré).

    Nécessite user.profiles chargé avec Profile.app_roles.
    Retourne les assignments effectifs [{app, slug, role}].
    """
    # Supprimer uniquement les rôles manuels (préserver profile:% et override:%)
    await db.execute(
        delete(UserAppRole).where(
            UserAppRole.user_id == user.id,
            UserAppRole.source == "manual"
        )
    )

    best_profile = _best_profile_roles_by_app(user)

    # Relire les lignes restantes (profile/override) après suppression des manuelles
    remaining = await db.execute(
        select(UserAppRole).where(UserAppRole.user_id == user.id)
    )
    current_by_app = {r.app_id: r for r in remaining.scalars().all()}

    result_assignments: List[Dict] = []
    for assignment in assignments:
        expected = best_profile.get(assignment.app_id)
        current = current_by_app.get(assignment.app_id)

        if expected is not None:
            new_role = expected["role"] if assignment.reset else assignment.role
            new_source = (
                f"profile:{expected['profile_id']}" if assignment.reset
                else f"override:{expected['profile_id']}"
            )
        else:
            new_role = assignment.role
            new_source = "manual"

        old_role = current.role if current is not None else None
        old_source = current.source if current is not None else None
        changed = current is None or current.role != new_role or current.source != new_source

        if changed:
            if current is not None:
                current.role = new_role
                current.source = new_source
            else:
                db.add(UserAppRole(
                    user_id=user.id,
                    app_id=assignment.app_id,
                    role=new_role,
                    source=new_source
                ))

        # Audit des créations/suppressions d'override
        if changed and (
            new_source.startswith("override:") or (old_source or "").startswith("override:")
        ):
            db.add(AuditLog(
                user_id=admin.id,
                action=(
                    "app_role_override" if new_source.startswith("override:")
                    else "app_role_override_reset"
                ),
                target=user.username,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:255],
                details=(
                    f"app_id={assignment.app_id} "
                    f"{old_source or 'aucun'}/{old_role or 'aucun'} -> {new_source}/{new_role}"
                ),
            ))

        app = apps_by_id[assignment.app_id]
        result_assignments.append({
            "app": app.name,
            "slug": app.slug,
            "role": new_role
        })

    return result_assignments


@router.post("/{user_id}/apps")
async def set_user_apps(
    user_id: int,
    updates: UserAppsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """
    Définit les applications et rôles pour un utilisateur.

    Exemple de body:
    {
        "assignments": [
            {"app_id": 1, "role": "admin"},
            {"app_id": 2, "role": "user", "reset": true},
            {"app_id": 3, "role": "viewer"}
        ]
    }

    App couverte par un profil : le rôle choisi devient un override
    (source='override:<profile_id>'), visible côté admin et préservé par la
    sync profil. "reset": true réinitialise au rôle attendu par le profil.
    """
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles).selectinload(Profile.app_roles)
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )

    # Vérifier que les apps existent
    app_ids = [a.app_id for a in updates.assignments]
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(app_ids))
    )
    apps_by_id = {app.id: app for app in apps_result.scalars().all()}

    for assignment in updates.assignments:
        if assignment.app_id not in apps_by_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Application {assignment.app_id} non trouvée"
            )

    result_assignments = await _apply_user_app_assignments(
        db, request, current_admin, user, updates.assignments, apps_by_id
    )

    await db.commit()

    # Notifier SSE que les droits de l'utilisateur ont changé
    # Permet au frontend de rafraîchir automatiquement le token
    notify_event("user_permissions_changed", {
        "user_id": user_id,
        "username": user.username,
        "apps_count": len(result_assignments)
    })

    return {
        "message": f"Applications mises à jour pour {user.username}",
        "assignments": result_assignments
    }


@router.get("/{user_id}/apps")
async def get_user_apps(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """Récupère les applications et rôles d'un utilisateur"""
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.app_roles).selectinload(UserAppRole.application),
            selectinload(User.profiles)
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    return {
        "user": user.username,
        "apps": [
            {
                "app_id": ar.app_id,
                "app_name": ar.application.name,
                "app_slug": ar.application.slug,
                "role": ar.role,
                "source": ar.source
            }
            for ar in user.app_roles
        ]
    }


# === Gestion des rôles disponibles ===

@router.get("/roles/available")
async def get_available_roles():
    """Retourne la liste des rôles disponibles par défaut"""
    return {
        "roles": DEFAULT_ROLES,
        "descriptions": {
            "admin": "Administrateur - Tous les droits sur l'application",
            "manager": "Manager - Gestion des données et utilisateurs de l'app",
            "user": "Utilisateur - Accès standard en lecture/écriture",
            "viewer": "Lecteur - Accès en lecture seule",
            "service": "Service - Accès M2M (service accounts uniquement)"
        }
    }


# === Gestion des sessions utilisateur ===

@router.post("/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):
    """
    Révoque toutes les sessions d'un utilisateur (admin uniquement).
    Force la déconnexion de l'utilisateur sur tous ses appareils.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    # Révoquer tous les tokens
    await revoke_all_user_tokens(db, user_id)
    
    # Notifier via SSE pour forcer la déconnexion côté client
    notify_event("force_logout", {
        "user_id": user_id,
        "username": user.username,
        "reason": "admin_revoked",
        "revoked_by": current_admin.username
    })
    
    notify_stats_update()
    
    return {
        "message": f"Sessions révoquées pour {user.username}",
        "user_id": user_id
    }


# ===== Opérations en masse (Bulk Operations) =====

@router.post("/bulk/create", response_model=BulkUsersCreateResponse)
async def bulk_create_users(
    request: BulkUsersCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Crée plusieurs utilisateurs en une seule opération.
    
    Chaque utilisateur peut avoir des rôles sur des applications assignés directement.
    Les erreurs sur un utilisateur n'empêchent pas la création des autres.
    
    Exemple:
    ```json
    {
        "users": [
            {
                "username": "user1",
                "email": "user1@example.com",
                "password": "password123",
                "is_admin": false,
                "app_roles": [
                    {"app_id": 1, "role": "user"},
                    {"app_id": 2, "role": "viewer"}
                ]
            },
            {
                "username": "user2",
                "password": "password456"
            }
        ]
    }
    ```
    """
    results = []
    created_count = 0
    
    for user_data in request.users:
        try:
            # Vérifier si l'utilisateur existe déjà
            existing = await db.execute(
                select(User).where(User.username == user_data.username)
            )
            if existing.scalar_one_or_none():
                results.append(BulkUserCreateResult(
                    username=user_data.username,
                    success=False,
                    error="Ce nom d'utilisateur existe déjà"
                ))
                continue
            
            # Vérifier l'email si fourni
            if user_data.email:
                existing_email = await db.execute(
                    select(User).where(User.email == user_data.email)
                )
                if existing_email.scalar_one_or_none():
                    results.append(BulkUserCreateResult(
                        username=user_data.username,
                        success=False,
                        error="Cet email est déjà utilisé"
                    ))
                    continue
            
            # Vérifier les profils si fournis
            profiles_loaded = []
            if user_data.profile_ids:
                for pid in user_data.profile_ids:
                    profile_result = await db.execute(
                        select(Profile)
                        .options(selectinload(Profile.app_roles))
                        .where(Profile.id == pid)
                    )
                    p = profile_result.scalar_one_or_none()
                    if p:
                        profiles_loaded.append(p)

            # Créer l'utilisateur
            user = User(
                username=user_data.username,
                email=user_data.email,
                hashed_password=get_password_hash(
                    user_data.password
                ),
                is_admin=user_data.is_admin,
                is_active=user_data.is_active,
                must_change_password=(
                    user_data.must_change_password
                ),
                can_change_password=(
                    user_data.can_change_password
                )
            )
            db.add(user)
            await db.flush()  # Pour obtenir l'ID

            # Associer les profils via la table de jonction
            for profile in profiles_loaded:
                await db.execute(
                    user_profiles.insert().values(
                        user_id=user.id,
                        profile_id=profile.id
                    )
                )

            # Construire la map des rôles finaux (highest role wins)
            from app.models import highest_role
            profile_roles = {}  # app_id -> best role from profiles
            manual_roles = {}
            for profile in profiles_loaded:
                for par in profile.app_roles:
                    existing = profile_roles.get(par.app_id)
                    profile_roles[par.app_id] = highest_role(existing, par.role) if existing else par.role
            if user_data.app_roles:
                for ar in user_data.app_roles:
                    if ar.app_id not in profile_roles:
                        manual_roles[ar.app_id] = ar.role

            # Vérifier que les apps existent
            all_app_ids = set(profile_roles.keys()) | set(manual_roles.keys())
            if all_app_ids:
                apps_result = await db.execute(
                    select(Application).where(
                        Application.id.in_(
                            list(all_app_ids)
                        )
                    )
                )
                existing_apps = {
                    app.id
                    for app in apps_result.scalars().all()
                }
                # Map app_id -> profile_id that provides the best role
                profile_source = {}
                for profile in profiles_loaded:
                    for par in profile.app_roles:
                        if profile_roles.get(par.app_id) == par.role:
                            profile_source.setdefault(par.app_id, profile.id)

                for app_id, role in profile_roles.items():
                    if app_id in existing_apps:
                        db.add(UserAppRole(
                            user_id=user.id,
                            app_id=app_id,
                            role=role,
                            source=f"profile:{profile_source.get(app_id, 0)}"
                        ))
                for app_id, role in manual_roles.items():
                    if app_id in existing_apps:
                        db.add(UserAppRole(
                            user_id=user.id,
                            app_id=app_id,
                            role=role,
                            source="manual"
                        ))
            
            results.append(BulkUserCreateResult(
                username=user_data.username,
                success=True,
                user_id=user.id
            ))
            created_count += 1
            
        except Exception as e:
            results.append(BulkUserCreateResult(
                username=user_data.username,
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    # Notifier les clients SSE
    if created_count > 0:
        notify_event("users_updated", {"action": "bulk_created", "count": created_count})
        notify_stats_update()
    
    return BulkUsersCreateResponse(
        total_requested=len(request.users),
        total_created=created_count,
        total_failed=len(request.users) - created_count,
        results=results
    )


@router.post("/bulk/roles", response_model=BulkUsersRolesUpdateResponse)
async def bulk_update_user_roles(
    request: BulkUsersRolesUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Met à jour les rôles de plusieurs utilisateurs en une seule opération.
    
    Remplace TOUS les rôles existants de chaque utilisateur par les nouveaux.
    
    Exemple:
    ```json
    {
        "updates": [
            {
                "user_id": 1,
                "app_roles": [
                    {"app_id": 1, "role": "admin"},
                    {"app_id": 2, "role": "user"}
                ]
            },
            {
                "user_id": 2,
                "app_roles": [
                    {"app_id": 1, "role": "viewer"}
                ]
            }
        ]
    }
    ```
    """
    results = []
    updated_count = 0
    
    for update in request.updates:
        try:
            # Vérifier que l'utilisateur existe
            result = await db.execute(
                select(User).where(User.id == update.user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                results.append(BulkUserRoleUpdateResult(
                    user_id=update.user_id,
                    username="inconnu",
                    success=False,
                    error="Utilisateur non trouvé"
                ))
                continue
            
            # Vérifier que les applications existent
            app_ids = [ar.app_id for ar in update.app_roles]
            if app_ids:
                apps_result = await db.execute(
                    select(Application).where(Application.id.in_(app_ids))
                )
                existing_apps = {app.id for app in apps_result.scalars().all()}
                
                missing_apps = set(app_ids) - existing_apps
                if missing_apps:
                    results.append(BulkUserRoleUpdateResult(
                        user_id=update.user_id,
                        username=user.username,
                        success=False,
                        error=f"Applications non trouvées: {missing_apps}"
                    ))
                    continue
            
            # Supprimer uniquement les rôles manuels (préserver ceux du profil)
            await db.execute(
                delete(UserAppRole).where(
                    UserAppRole.user_id == update.user_id,
                    UserAppRole.source == "manual"
                )
            )
            
            # Récupérer les app_id déjà couverts par le profil
            profile_roles_result = await db.execute(
                select(UserAppRole.app_id).where(
                    UserAppRole.user_id == update.user_id,
                    UserAppRole.source == "profile"
                )
            )
            profile_app_ids = {row[0] for row in profile_roles_result.all()}
            
            # Créer les nouveaux rôles (ignorer ceux déjà gérés par le profil)
            roles_created = 0
            for ar in update.app_roles:
                if ar.app_id in profile_app_ids:
                    continue
                user_app_role = UserAppRole(
                    user_id=update.user_id,
                    app_id=ar.app_id,
                    role=ar.role,
                    source="manual"
                )
                db.add(user_app_role)
                roles_created += 1
            
            results.append(BulkUserRoleUpdateResult(
                user_id=update.user_id,
                username=user.username,
                success=True,
                roles_updated=roles_created
            ))
            updated_count += 1
            
            # Notifier le changement de permissions pour cet utilisateur
            notify_event("user_permissions_changed", {
                "user_id": update.user_id,
                "username": user.username,
                "apps_count": len(update.app_roles)
            })
            
        except Exception as e:
            results.append(BulkUserRoleUpdateResult(
                user_id=update.user_id,
                username="erreur",
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    if updated_count > 0:
        notify_event("users_updated", {"action": "bulk_roles_updated", "count": updated_count})
    
    return BulkUsersRolesUpdateResponse(
        total_requested=len(request.updates),
        total_updated=updated_count,
        total_failed=len(request.updates) - updated_count,
        results=results
    )


@router.post("/bulk/delete", response_model=BulkUsersDeleteResponse)
async def bulk_delete_users(
    request: BulkUsersDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Supprime plusieurs utilisateurs en une seule opération.
    
    ⚠️ ATTENTION: Cette opération est irréversible !
    Le champ "confirm" doit être à true pour valider la suppression.
    
    L'admin connecté ne peut pas se supprimer lui-même.
    
    Exemple:
    ```json
    {
        "user_ids": [2, 3, 4],
        "confirm": true
    }
    ```
    """
    if not request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous devez confirmer la suppression (confirm: true)"
        )
    
    results = []
    deleted_count = 0
    
    for user_id in request.user_ids:
        try:
            # Vérifier qu'on ne se supprime pas soi-même
            if user_id == current_user.id:
                results.append(BulkUserDeleteResult(
                    user_id=user_id,
                    username=current_user.username,
                    success=False,
                    error="Vous ne pouvez pas vous supprimer vous-même"
                ))
                continue
            
            # Vérifier que l'utilisateur existe
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                results.append(BulkUserDeleteResult(
                    user_id=user_id,
                    username="inconnu",
                    success=False,
                    error="Utilisateur non trouvé"
                ))
                continue
            
            username = user.username
            await db.delete(user)
            
            results.append(BulkUserDeleteResult(
                user_id=user_id,
                username=username,
                success=True
            ))
            deleted_count += 1
            
        except Exception as e:
            results.append(BulkUserDeleteResult(
                user_id=user_id,
                username="erreur",
                success=False,
                error=str(e)
            ))
    
    await db.commit()
    
    if deleted_count > 0:
        notify_event("users_updated", {"action": "bulk_deleted", "count": deleted_count})
        notify_stats_update()
    
    return BulkUsersDeleteResponse(
        total_requested=len(request.user_ids),
        total_deleted=deleted_count,
        total_failed=len(request.user_ids) - deleted_count,
        results=results
    )


@router.post("/bulk/assign-app-roles", response_model=BulkAppRolesAssignResponse)
async def bulk_assign_app_roles(
    request: BulkAppRolesAssignRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Assigne un rôle à plusieurs utilisateurs sur des applications.
    
    Permet d'ajouter ou modifier le rôle de plusieurs utilisateurs sur une ou plusieurs apps.
    
    Exemple:
    ```json
    {
        "assignments": [
            {
                "app_id": 1,
                "user_ids": [2, 3, 4],
                "role": "user"
            },
            {
                "app_id": 2,
                "user_ids": [2, 5],
                "role": "admin"
            }
        ]
    }
    ```
    """
    results = []
    success_count = 0
    
    # Pré-charger toutes les applications et utilisateurs nécessaires
    all_app_ids = {a.app_id for a in request.assignments}
    all_user_ids = set()
    for a in request.assignments:
        all_user_ids.update(a.user_ids)
    
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(all_app_ids))
    )
    apps_map = {app.id: app for app in apps_result.scalars().all()}
    
    users_result = await db.execute(
        select(User).where(User.id.in_(all_user_ids))
    )
    users_map = {user.id: user for user in users_result.scalars().all()}
    
    for assignment in request.assignments:
        app = apps_map.get(assignment.app_id)
        if not app:
            for user_id in assignment.user_ids:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name="inconnu",
                    user_id=user_id,
                    username=users_map.get(user_id, User()).username if users_map.get(user_id) else "inconnu",
                    role=assignment.role,
                    success=False,
                    error=f"Application {assignment.app_id} non trouvée"
                ))
            continue
        
        for user_id in assignment.user_ids:
            user = users_map.get(user_id)
            if not user:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username="inconnu",
                    role=assignment.role,
                    success=False,
                    error=f"Utilisateur {user_id} non trouvé"
                ))
                continue
            
            try:
                # Chercher un rôle existant
                existing_role = await db.execute(
                    select(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == assignment.app_id
                    )
                )
                existing = existing_role.scalar_one_or_none()
                
                if existing:
                    if existing.role == assignment.role:
                        results.append(BulkAppRoleAssignResult(
                            app_id=assignment.app_id,
                            app_name=app.name,
                            user_id=user_id,
                            username=user.username,
                            role=assignment.role,
                            success=True,
                            action="unchanged"
                        ))
                    else:
                        existing.role = assignment.role
                        results.append(BulkAppRoleAssignResult(
                            app_id=assignment.app_id,
                            app_name=app.name,
                            user_id=user_id,
                            username=user.username,
                            role=assignment.role,
                            success=True,
                            action="updated"
                        ))
                        success_count += 1
                else:
                    # Créer un nouveau rôle
                    new_role = UserAppRole(
                        user_id=user_id,
                        app_id=assignment.app_id,
                        role=assignment.role,
                        source="manual"
                    )
                    db.add(new_role)
                    results.append(BulkAppRoleAssignResult(
                        app_id=assignment.app_id,
                        app_name=app.name,
                        user_id=user_id,
                        username=user.username,
                        role=assignment.role,
                        success=True,
                        action="created"
                    ))
                    success_count += 1
                
                # Notifier le changement de permissions
                notify_event("user_permissions_changed", {
                    "user_id": user_id,
                    "username": user.username
                })
                
            except Exception as e:
                results.append(BulkAppRoleAssignResult(
                    app_id=assignment.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    role=assignment.role,
                    success=False,
                    error=str(e)
                ))
    
    await db.commit()
    
    if success_count > 0:
        notify_event("users_updated", {"action": "bulk_app_roles_assigned", "count": success_count})
    
    failed_count = len([r for r in results if not r.success])
    return BulkAppRolesAssignResponse(
        total_assignments=len(results),
        total_success=len(results) - failed_count,
        total_failed=failed_count,
        results=results
    )


@router.post("/bulk/remove-app-access", response_model=BulkRemoveAppAccessResponse)
async def bulk_remove_app_access(
    request: BulkRemoveAppAccessRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin)
):
    """
    Supprime l'accès à des applications pour plusieurs utilisateurs.
    
    Exemple:
    ```json
    {
        "removals": [
            {
                "app_id": 1,
                "user_ids": [2, 3, 4]
            },
            {
                "app_id": 2,
                "user_ids": [2, 5]
            }
        ]
    }
    ```
    """
    results = []
    success_count = 0
    
    # Pré-charger toutes les applications et utilisateurs nécessaires
    all_app_ids = {r.app_id for r in request.removals}
    all_user_ids = set()
    for r in request.removals:
        all_user_ids.update(r.user_ids)
    
    apps_result = await db.execute(
        select(Application).where(Application.id.in_(all_app_ids))
    )
    apps_map = {app.id: app for app in apps_result.scalars().all()}
    
    users_result = await db.execute(
        select(User).where(User.id.in_(all_user_ids))
    )
    users_map = {user.id: user for user in users_result.scalars().all()}
    
    for removal in request.removals:
        app = apps_map.get(removal.app_id)
        if not app:
            for user_id in removal.user_ids:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name="inconnu",
                    user_id=user_id,
                    username=users_map.get(user_id).username if users_map.get(user_id) else "inconnu",
                    success=False,
                    error=f"Application {removal.app_id} non trouvée"
                ))
            continue
        
        for user_id in removal.user_ids:
            user = users_map.get(user_id)
            if not user:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username="inconnu",
                    success=False,
                    error=f"Utilisateur {user_id} non trouvé"
                ))
                continue
            
            try:
                # Supprimer le rôle
                delete_result = await db.execute(
                    delete(UserAppRole).where(
                        UserAppRole.user_id == user_id,
                        UserAppRole.app_id == removal.app_id
                    )
                )
                
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    success=True
                ))
                success_count += 1
                
                # Notifier le changement de permissions
                notify_event("user_permissions_changed", {
                    "user_id": user_id,
                    "username": user.username
                })
                
            except Exception as e:
                results.append(BulkRemoveAppAccessResult(
                    app_id=removal.app_id,
                    app_name=app.name,
                    user_id=user_id,
                    username=user.username,
                    success=False,
                    error=str(e)
                ))
    
    await db.commit()
    
    if success_count > 0:
        notify_event("users_updated", {"action": "bulk_app_access_removed", "count": success_count})
    
    failed_count = len([r for r in results if not r.success])
    return BulkRemoveAppAccessResponse(
        total_removals=len(results),
        total_success=len(results) - failed_count,
        total_failed=failed_count,
        results=results
    )


@router.post("/{user_id}/totp/disable")
async def admin_disable_user_totp(
    user_id: int,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin : désactive l'authentificateur TOTP d'un utilisateur (secours perte d'appareil)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur non trouvé")

    user.totp_enabled = False
    user.totp_secret = None
    user.totp_confirmed_at = None
    user.totp_last_counter = None

    db.add(AuditLog(
        user_id=current_admin.id,
        action="totp_disabled_by_admin",
        target=user.username,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:255],
    ))
    await db.commit()
    notify_event("users_updated", {"action": "totp_disabled", "user_id": user.id})
    return {"message": "Authentification par token désactivée pour l'utilisateur"}
