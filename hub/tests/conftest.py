"""
Configuration pytest et fixtures pour les tests du Hub
"""
import warnings
# Supprimer le DeprecationWarning de passlib/crypt (lib tierce, pas de fix disponible)
warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)

import pytest
import asyncio
from typing import AsyncGenerator, Generator
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.main import app
from app.database import Base, get_db
from app.models import User, Application, UserAppRole, ServiceAccount, RefreshToken
from app.auth import get_password_hash, create_access_token
from app.config import get_settings


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Réinitialise les rate limiters entre chaque test"""
    from app.routes.auth import _login_attempts
    from app.routes.oidc import _token_failures
    _login_attempts.clear()
    _token_failures.clear()
    yield
    _login_attempts.clear()
    _token_failures.clear()


@pytest.fixture(autouse=True)
def _isolated_jwt_keyring(tmp_path, monkeypatch):
    """Isole le trousseau JWT multi-kid pour CHAQUE test (jamais les vrais secrets/).

    Depuis la signature par trousseau (jwt_keyring), tout test qui émet un JWT
    sans fixture dédiée lirait `secrets/jwt_keys/` — absent sur l'hôte de test
    (le seed au démarrage du conteneur est fail-closed). On seede donc un
    trousseau jetable dans tmp_path à partir d'une COPIE en lecture de la paire
    legacy réelle : les tokens restent signés par la même clé que
    `settings.jwt_public_key` (mis en cache au chargement), kid = hub-jwt-key-1
    tant qu'aucune rotation n'a lieu. Les tests du trousseau lui-même
    (test_jwt_keyring.isolated_keyring) re-patchent par-dessus sans conflit.
    """
    import app.config as cfg
    from app import jwt_keyring

    secrets_dir = tmp_path / "secrets_keyring"
    secrets_dir.mkdir()
    (secrets_dir / "jwt_private_key.pem").write_text(get_settings().jwt_private_key)
    (secrets_dir / "jwt_public_key.pem").write_text(get_settings().jwt_public_key)
    monkeypatch.setattr(cfg, "_SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(jwt_keyring, "_KEYRING_DIR", secrets_dir / "jwt_keys")
    jwt_keyring.reload()
    jwt_keyring.ensure_seeded()
    yield
    jwt_keyring.reload()


@pytest.fixture(autouse=True)
def _hub_host_locality_neutralized(monkeypatch):
    """Comportement par défaut des tests : une IP configurée est toujours servie.

    get_hub_host() ignore désormais une IP configurée absente des interfaces
    locales (Hub déplacé/cloné — cf. test_hub_host_locality.py). La plupart des
    tests existants configurent des IP de documentation (RFC 5737) pour tester
    la MÉCANIQUE (SAN, propagation, VPN, env/DB) : on force _is_local_ip=True
    pour neutraliser le fallback. Les tests de localité surchargent à False.
    """
    monkeypatch.setattr("app.config._is_local_ip", lambda ip: True)


@pytest.fixture(autouse=True)
def _project_network_scan_neutralized(monkeypatch):
    """Par défaut, aucun réseau Docker de projet n'est visible des tests.

    La réconciliation subnet du pipeline de déploiement lit le démon Docker
    local : sans neutralisation, un réseau réel labellisé comme le slug d'un
    test (ex. exemple-app) fausserait les assertions de manière dépendante de
    l'environnement. Les tests de réconciliation surchargent ce patch.
    """
    monkeypatch.setattr(
        "app.port_allocator.docker_scanner.get_project_network_subnets",
        lambda project: set(),
    )


# Champs OIDC mutés par certains tests ; sauvegardés puis restaurés par la fixture `settings`.
_OIDC_FIELDS_TO_SNAPSHOT = (
    "oidc_issuer",
    "oidc_email_template",
    "oidc_id_token_ttl_minutes",
    "oidc_access_token_ttl_minutes",
    "oidc_backchannel_logout_enabled",
    "oidc_slo_enabled",
    "oidc_slo_iframe_timeout_ms",
)


@pytest.fixture
def settings():
    """Expose le singleton get_settings() et restaure les champs mutés par les tests.

    Les tests OIDC modifient en place des attributs (oidc_issuer, oidc_email_template,
    etc.) sur le singleton lru_cache. On snapshot ces champs avant le test et on les
    restaure après pour éviter toute fuite d'état entre tests.
    """
    s = get_settings()
    snapshot = {field: getattr(s, field) for field in _OIDC_FIELDS_TO_SNAPSHOT}
    yield s
    for field, original in snapshot.items():
        setattr(s, field, original)


# Configuration de test
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Crée un event loop pour toute la session de test"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def async_engine():
    """Crée un engine de test avec SQLite en mémoire"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest.fixture(scope="function")
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Crée une session de test"""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with async_session_maker() as session:
        yield session


@pytest.fixture(autouse=True)
def _patch_session_maker(async_engine, monkeypatch):
    """Redirige async_session_maker vers la DB de test.
    Les chemins 'background' (SSE, cleanup) ouvrent leur propre session via
    app.database.async_session_maker — sans ce patch ils écriraient dans la
    base réelle du conteneur au lieu de la SQLite de test."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import app.database as database
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", maker)
    yield


@pytest.fixture(autouse=True)
def _stop_session_cleanup_task():
    """Annule la tâche de cleanup des sessions après chaque test.
    En prod, cette tâche vit de `lifespan` à `force_stop_cleanup_task` (main.py).
    En test, la loop est session-scopée : si un test démarre le cleanup via
    /api/events/stream, la tâche immortelle survit au test et bloque le
    finalizer pytest-asyncio (hang sur le drop_all de la fixture async_engine)."""
    yield
    from app.routes.events import force_stop_cleanup_task
    force_stop_cleanup_task()


@pytest.fixture(scope="function")
async def client(async_engine, async_session) -> AsyncGenerator[AsyncClient, None]:
    """Client HTTP de test avec base de données en mémoire"""
    
    async def override_get_db():
        yield async_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(async_session: AsyncSession) -> User:
    """Crée un utilisateur de test standard"""
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=get_password_hash("testpass123"),
        is_active=True,
        is_admin=False
    )
    async_session.add(user)
    await async_session.commit()
    await async_session.refresh(user)
    return user


@pytest.fixture
async def admin_user(async_session: AsyncSession) -> User:
    """Crée un utilisateur admin de test"""
    admin = User(
        username="admin",
        email="admin@example.com",
        hashed_password=get_password_hash("admin123"),
        is_active=True,
        is_admin=True
    )
    async_session.add(admin)
    await async_session.commit()
    await async_session.refresh(admin)
    return admin


@pytest.fixture
async def test_app(async_session: AsyncSession) -> Application:
    """Crée une application de test"""
    application = Application(
        name="Test App",
        slug="test-app",
        description="Application de test",
        icon="bi-gear",
        color="#007bff",
        api_url="http://localhost:8001",
        frontend_url="http://localhost:3001",
        callback_url="http://localhost:3001/callback",
        is_active=True,
        requires_auth=True
    )
    async_session.add(application)
    await async_session.commit()
    await async_session.refresh(application)
    return application


@pytest.fixture
async def other_app(async_session: AsyncSession) -> Application:
    """Seconde application de test pour les scenarios multi-apps."""
    app = Application(
        name="Other App",
        slug="other-app",
        description="Deuxieme application",
        icon="bi-gear",
        color="#6c757d",
        api_url="http://localhost:8002",
        frontend_url="http://localhost:3002",
        callback_url="http://localhost:3002/callback",
        is_active=True,
        requires_auth=True,
    )
    async_session.add(app)
    await async_session.commit()
    await async_session.refresh(app)
    return app


@pytest.fixture
async def user_with_app_access(
    async_session: AsyncSession,
    test_user: User,
    test_app: Application
) -> tuple[User, Application]:
    """Crée un utilisateur avec accès à une application"""
    user_app_role = UserAppRole(
        user_id=test_user.id,
        app_id=test_app.id,
        role="user"
    )
    async_session.add(user_app_role)
    await async_session.commit()
    
    # Rafraîchir les objets
    await async_session.refresh(test_user)
    await async_session.refresh(test_app)
    
    return test_user, test_app


@pytest.fixture
async def admin_with_app(
    async_session: AsyncSession,
    admin_user: User,
    test_app: Application
) -> tuple[User, Application]:
    """Crée un admin avec accès admin à une application"""
    user_app_role = UserAppRole(
        user_id=admin_user.id,
        app_id=test_app.id,
        role="admin"
    )
    async_session.add(user_app_role)
    await async_session.commit()
    
    await async_session.refresh(admin_user)
    return admin_user, test_app


@pytest.fixture
def user_token(test_user: User) -> str:
    """Génère un token pour l'utilisateur de test"""
    token, _, _ = create_access_token(
        username=test_user.username,
        apps={}
    )
    return token


@pytest.fixture
def admin_token(admin_user: User) -> str:
    """Génère un token pour l'admin"""
    token, _, _ = create_access_token(
        username=admin_user.username,
        apps={}
    )
    return token


@pytest.fixture
async def service_account(async_session: AsyncSession, test_app: Application) -> ServiceAccount:
    """Crée un service account de test"""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models import service_account_app_access
    
    sa = ServiceAccount(
        name="Test Service Account",
        client_id="sa_test123456789",
        client_secret_hash=get_password_hash("secret123"),
        description="Service account de test",
        is_active=True
    )
    async_session.add(sa)
    await async_session.commit()
    await async_session.refresh(sa)
    
    # Associer à l'app de test via insert direct
    await async_session.execute(
        service_account_app_access.insert().values(
            service_account_id=sa.id,
            app_id=test_app.id
        )
    )
    await async_session.commit()

    # Reload avec eager loading pour que sa.applications soit disponible
    result = await async_session.execute(
        select(ServiceAccount).options(selectinload(ServiceAccount.applications)).where(ServiceAccount.id == sa.id)
    )
    sa = result.scalar_one()
    
    return sa


# ===== Helpers =====

def get_auth_header(token: str) -> dict:
    """Retourne le header d'authentification"""
    return {"Authorization": f"Bearer {token}"}


async def create_test_user_with_token(
    session: AsyncSession,
    username: str,
    is_admin: bool = False
) -> tuple[User, str]:
    """Helper pour créer un utilisateur et son token"""
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=is_admin
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    
    token, _, _ = create_access_token(
        username=user.username,
        apps={}
    )
    
    return user, token
