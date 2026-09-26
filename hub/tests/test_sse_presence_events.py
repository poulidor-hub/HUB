"""
Tests SSE présence/erreurs :
- notify_active_users_changed : cooldown 2 s
- touch_app_presence : True à l'ouverture, False si segment déjà ouvert
- _log_error_to_db : écrit un ErrorLog et émet audit_updated(action=error_logged)
"""
import pytest
from unittest.mock import MagicMock
from sqlalchemy import select

from app.models import ErrorLog, UserSession, utcnow
from app.usage_tracking import touch_app_presence
from .conftest import get_auth_header


@pytest.fixture(autouse=True)
def _reset_presence_cooldown():
    """Réinitialise le cooldown du helper entre les tests."""
    import app.routes.events as events_mod
    events_mod._active_users_last_notify = 0.0
    yield
    events_mod._active_users_last_notify = 0.0


@pytest.fixture
def captured_notify(monkeypatch):
    """Capture les appels à notify_event du module events."""
    import app.routes.events as events_mod
    calls = []
    monkeypatch.setattr(events_mod, "notify_event", lambda t, d=None: calls.append((t, d)))
    return calls


@pytest.mark.asyncio
async def test_notify_active_users_changed_cooldown(captured_notify):
    from app.routes.events import notify_active_users_changed
    notify_active_users_changed()
    notify_active_users_changed()
    notify_active_users_changed()
    assert len(captured_notify) == 1
    event_type, data = captured_notify[0]
    assert event_type == "active_users_changed"
    assert data == {}


@pytest.mark.asyncio
async def test_touch_app_presence_opens_once(async_session, test_user):
    session = UserSession(user_id=test_user.id, login_at=utcnow(), last_activity_at=utcnow())
    async_session.add(session)
    await async_session.commit()
    await async_session.refresh(session)

    assert await touch_app_presence(async_session, session, "test-app") is True
    assert await touch_app_presence(async_session, session, "test-app") is False


@pytest.mark.asyncio
async def test_log_error_to_db_emits_error_logged(async_session, monkeypatch):
    from app import main as main_mod
    import app.routes.audit as audit_mod

    calls = []
    monkeypatch.setattr(audit_mod, "_notify_audit",
                        lambda extra=None: calls.append(extra))

    request = MagicMock()
    request.state.request_id = "req-123"
    request.state.source_app_slug = "test-app"
    request.method = "GET"
    request.url.path = "/api/inexistant"
    request.client.host = "127.0.0.1"
    request.headers = {"User-Agent": "pytest"}

    await main_mod._log_error_to_db(request, 500, "internal", "boom")

    row = (await async_session.execute(
        select(ErrorLog).where(ErrorLog.request_id == "req-123")
    )).scalar_one()
    assert row.error_code == 500
    assert row.source_app_slug == "test-app"
    assert len(calls) == 1
    assert calls[0] == {"action": "error_logged", "error_code": 500, "source_app_slug": "test-app"}
