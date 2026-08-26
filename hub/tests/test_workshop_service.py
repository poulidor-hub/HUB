"""Tests du service atelier (workshop_service) — cycle pilotable depuis l'UI HUB.

Pattern : subprocess mockés (_FakeProc/_install_fake_exec, cf. test_app_deploy.py),
DB sqlite in-memory (conftest). Le contrat .workshop.env (7 clés hubctl v0.6.1)
est figé ici : toute divergence doit faire crier ces tests.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import WorkshopOperation, utcnow


@pytest.mark.asyncio
async def test_workshop_operation_model_persists(async_session):
    op = WorkshopOperation(
        app_slug="wstest", operation="update", status="pending",
        triggered_by="admin",
    )
    async_session.add(op)
    await async_session.commit()
    await async_session.refresh(op)
    assert op.id is not None
    assert op.status == "pending"
    assert op.current_step is None
    assert op.started_at is not None


@pytest.mark.asyncio
async def test_workshop_retention_setting_default():
    from app.config import get_settings
    assert get_settings().workshop_retention_days == 30
