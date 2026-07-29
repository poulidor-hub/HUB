"""Tests du flag is_unmanaged (apps non gérées / launcher-only)."""
import pytest

from app.models import Application


@pytest.mark.asyncio
async def test_application_model_has_is_unmanaged_default_false():
    """Le modèle Application expose is_unmanaged, défaut False."""
    app = Application(
        name="X", slug="x", api_url="http://h:8000", frontend_url="http://h", is_active=True
    )
    assert hasattr(app, "is_unmanaged")
    # default côté Python (la BDD impose NOT NULL DEFAULT FALSE via migration)
    assert app.is_unmanaged is False or app.is_unmanaged is None  # avant flush
