"""Schéma AppWithUsersAndDocker — champs consommés par le tableau admin (fiche app).

Le tableau de la page Administration ▸ Applications affiche la version et les
badges Atelier / Non gérée : /apps/all doit donc transmettre `version`,
`is_workshop` et `is_unmanaged` (bug historique : présents sur le modèle ORM,
mais abandonnés par from_orm_model_with_docker → toujours false/None côté front).
"""
from datetime import datetime, timezone

from app.models import Application
from app.schemas import AppWithUsersAndDocker


def _make_app(**overrides) -> Application:
    app = Application(
        id=1,
        name="Demo",
        slug="demo",
        description=None,
        icon="bi-box",
        color="#0d6efd",
        api_url="http://demo-api:9001",
        frontend_url="http://demo:4201",
        callback_url=None,
        is_active=True,
        requires_auth=True,
        is_shared_service=False,
        created_at=datetime.now(timezone.utc),
    )
    app.user_roles = []
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def test_exposes_version_and_workshop_flags():
    app = _make_app(version="1.2.3", is_workshop=True, is_unmanaged=True)
    dto = AppWithUsersAndDocker.from_orm_model_with_docker(app)
    assert dto.version == "1.2.3"
    assert dto.is_workshop is True
    assert dto.is_unmanaged is True


def test_defaults_are_none_false_when_absent():
    app = _make_app()
    dto = AppWithUsersAndDocker.from_orm_model_with_docker(app)
    assert dto.version is None
    assert dto.is_workshop is False
    assert dto.is_unmanaged is False
