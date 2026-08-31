"""Tests du plan de propagation d'IP aux apps (validation admin).

- GET  /api/config/network/propagation-plan : apps v2 enregistrées, statuts
  (up_to_date / pending / no_env) + section "excluded" (ateliers, apps système)
- POST /api/config/network/propagation-plan/apply : écriture HUB_HOST dans les
  .env des apps Explicitement validées par l'admin (liste de slugs).
"""
import pytest
from sqlalchemy import select

from app.models import Application
from tests.conftest import get_auth_header


EFFECTIVE_IP = "192.0.2.50"     # IP effective du hub (mutée par fixture)
OLD_IP = "192.0.2.10"


@pytest.fixture(autouse=True)
def _restore_hub_host_singleton():
    from app.config import get_settings
    settings = get_settings()
    saved = settings.hub_host
    yield
    settings.hub_host = saved


@pytest.fixture()
def effective_ip():
    """Fixe l'IP effective du hub pour le plan (mutation à chaud, restaurée)."""
    from app.config import get_settings
    get_settings().hub_host = EFFECTIVE_IP
    return EFFECTIVE_IP


@pytest.fixture()
def apps_dir(tmp_path, monkeypatch):
    """APPS/ temporaire (get_apps_dir monkeypatché)."""
    import app.deploy_service as ds
    apps_dir = tmp_path / "APPS"
    apps_dir.mkdir()
    monkeypatch.setattr(ds, "get_apps_dir", lambda: apps_dir)
    # network.py importe get_apps_dir depuis deploy_service dans son module :
    monkeypatch.setattr("app.routes.network.get_apps_dir", lambda: apps_dir)
    return apps_dir


def _mk_app(async_session, slug, *, is_workshop=False, is_system=False):
    app = Application(
        name=slug.title(), slug=slug,
        api_url=f"http://127.0.0.1:19001",   # NOT NULL en DB, non utilisé ici
        is_active=True,
        is_workshop=is_workshop, is_system_app=is_system,
    )
    async_session.add(app)
    async_session.commit()
    return app


def _mk_env(apps_dir, slug, hub_host=OLD_IP, variant=None):
    env = apps_dir / slug
    env.mkdir(parents=True, exist_ok=True)
    key = variant or "HUB_HOST"
    (env / ".env").write_text(f"APP_SLUG={slug}\n{key}={hub_host}\nAPI_PORT=19001\n")
    return env / ".env"


# --- Auth ---

async def test_plan_requires_admin(client):
    assert (await client.get("/api/config/network/propagation-plan")).status_code == 401


async def test_apply_requires_admin(client):
    r = await client.post("/api/config/network/propagation-plan/apply", json={"slugs": ["x"]})
    assert r.status_code == 401


# --- GET plan ---

async def test_plan_empty(client, admin_token, apps_dir, effective_ip):
    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["hub_host"] == EFFECTIVE_IP
    assert data["apps"] == []
    assert data["excluded"] == []


async def test_plan_statuses(client, admin_token, async_session, apps_dir,
                             effective_ip):
    _mk_app(async_session, "app-pending")      # .env sur l'ancienne IP
    _mk_env(apps_dir, "app-pending", hub_host=OLD_IP)
    _mk_app(async_session, "app-uptodate")     # .env déjà à jour
    _mk_env(apps_dir, "app-uptodate", hub_host=EFFECTIVE_IP)
    _mk_app(async_session, "app-noenv")        # enregistrée, sans .env

    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    data = r.json()
    by_slug = {a["slug"]: a for a in data["apps"]}
    assert by_slug["app-pending"]["status"] == "pending"
    assert by_slug["app-pending"]["current_hub_host"] == OLD_IP
    assert by_slug["app-uptodate"]["status"] == "up_to_date"
    assert by_slug["app-noenv"]["status"] == "no_env"
    assert by_slug["app-noenv"]["current_hub_host"] is None
    assert data["excluded"] == []


async def test_plan_excludes_workshop_and_system(client, admin_token,
                                                 async_session, apps_dir,
                                                 effective_ip):
    _mk_app(async_session, "app-ws", is_workshop=True)
    _mk_app(async_session, "hub", is_system=True)

    r = await client.get("/api/config/network/propagation-plan",
                         headers=get_auth_header(admin_token))
    data = r.json()
    assert data["apps"] == []
    reasons = {e["slug"]: e["reason"] for e in data["excluded"]}
    assert "atelier" in reasons["app-ws"]
    assert "système" in reasons["hub"]


# --- POST apply ---

async def test_apply_writes_hub_host_and_variants(client, admin_token,
                                                  async_session, apps_dir,
                                                  effective_ip):
    _mk_app(async_session, "app-a")
    env_path = _mk_env(apps_dir, "app-a", hub_host=OLD_IP, variant="HUB_URL")

    r = await client.post("/api/config/network/propagation-plan/apply",
                          json={"slugs": ["app-a"]},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["hub_host"] == EFFECTIVE_IP
    assert data["applied"] == [{"slug": "app-a", "status": "written"}]

    # La variante HUB_URL existante est réécrite (pas d'ajout de canonique —
    # chirurgical), le reste du fichier intact
    text = env_path.read_text()
    assert f"HUB_URL={EFFECTIVE_IP}" in text
    assert "HUB_HOST=" not in text
    assert "APP_SLUG=app-a" in text
    assert "API_PORT=19001" in text


async def test_apply_refuses_unknown_and_noenv(client, admin_token,
                                               async_session, apps_dir,
                                               effective_ip):
    _mk_app(async_session, "app-noenv")
    r = await client.post("/api/config/network/propagation-plan/apply",
                          json={"slugs": ["inconnu", "app-noenv"]},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 200
    applied = {a["slug"]: a["status"] for a in r.json()["applied"]}
    assert applied["inconnu"] == "not_found"
    assert applied["app-noenv"] == "no_env"


async def test_apply_refuses_workshop(client, admin_token, async_session,
                                      apps_dir, effective_ip):
    _mk_app(async_session, "app-ws", is_workshop=True)
    r = await client.post("/api/config/network/propagation-plan/apply",
                          json={"slugs": ["app-ws"]},
                          headers=get_auth_header(admin_token))
    applied = {a["slug"]: a["status"] for a in r.json()["applied"]}
    assert applied["app-ws"] == "excluded"


async def test_apply_empty_slugs_rejected(client, admin_token):
    r = await client.post("/api/config/network/propagation-plan/apply",
                          json={"slugs": []},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 400


async def test_apply_idempotent(client, admin_token, async_session, apps_dir,
                                effective_ip):
    _mk_app(async_session, "app-a")
    _mk_env(apps_dir, "app-a", hub_host=EFFECTIVE_IP)
    for _ in range(2):
        r = await client.post("/api/config/network/propagation-plan/apply",
                              json={"slugs": ["app-a"]},
                              headers=get_auth_header(admin_token))
        assert r.status_code == 200
        assert r.json()["applied"] == [{"slug": "app-a", "status": "written"}]
    assert f"HUB_HOST={EFFECTIVE_IP}" in (apps_dir / "app-a" / ".env").read_text()
