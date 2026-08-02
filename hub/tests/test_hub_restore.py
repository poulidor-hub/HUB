"""Tests pour la restauration self-backup du HUB (import environment.json)."""
import warnings
warnings.filterwarnings("ignore", message="'crypt' is deprecated", category=DeprecationWarning)

import pytest
import zipfile
import json
import io
from datetime import datetime

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.main import app
from app.database import Base, get_db
from app.models import User, Application, ServiceAccount, UserAppRole
from app.auth import get_password_hash, create_access_token
from app.schemas import BackupImport, UserExport, ImportResult
from app.routes.hub_restore import import_environment, build_backup_import_from_zip_bytes


@pytest.fixture
async def client(async_engine, async_session):
    async def override_get_db():
        yield async_session
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _make_zip(environment_dict: dict) -> bytes:
    """Construit un ZIP contenant environment.json à partir d'un dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("environment.json", json.dumps(environment_dict, indent=2))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_users_create(async_session):
    """Nouveaux users (username inexistant) → créés."""
    import_data = BackupImport(
        users=[UserExport(
            username="newuser",
            email="new@test.com",
            hashed_password=get_password_hash("pass123"),
            is_active=True,
            is_admin=False,
        )],
    )
    result = await import_environment(async_session, import_data)
    assert result.users_created == 1
    assert result.users_updated == 0
    db_user = (await async_session.execute(
        select(User).where(User.username == "newuser")
    )).scalar_one()
    assert db_user.email == "new@test.com"
    assert db_user.is_active is True
