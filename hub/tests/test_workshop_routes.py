"""Tests des routes /api/apps/{slug}/workshop* (vue santé)."""
import pytest

import app.workshop_service as ws
import app.routes.workshop as route_mod
from app.models import Application
from tests.conftest import get_auth_header


async def _mk_app(async_session, slug="wstest", repo="https://git.example/x.git"):
    # Adaptation du brief : api_url est nullable=False sur le modèle Application.
    app_row = Application(slug=slug, name="Test", api_url="http://localhost:9100",
                          git_repo_url=repo, is_active=True, requires_auth=True)
    async_session.add(app_row)
    await async_session.commit()
    return app_row


def _async_ret(value):
    async def _f(*a, **k):
        return value
    return _f


async def test_get_workshop_404(client, admin_token):
    r = await client.get("/api/apps/nimporte/workshop",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_get_workshop_state(client, async_session, admin_token, tmp_path, monkeypatch):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.get("/api/apps/wstest/workshop",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["exists"] is False and data["git_ok"] in (True, False)
    assert data["active_operation"] is False


async def test_endpoints_require_admin(client):
    # sans token → 401 (le path 403 non-admin existe via get_current_admin)
    r = await client.post("/api/apps/wstest/workshop")
    assert r.status_code == 401


async def test_create_conflict_when_exists(client, async_session, admin_token,
                                           tmp_path, monkeypatch):
    await _mk_app(async_session)
    app = tmp_path / "wstest"; app.mkdir(parents=True)
    ws._write_ws_env_full(app / ".workshop.env", {
        "APP_SLUG": "wstest-ws", "API_PORT": "10000", "FRONTEND_PORT": "5200",
        "FRONTEND_HTTPS_PORT": "5201", "WS_REF": "main", "WS_COMMIT": "x",
        "APP_SUBNET": "10.210.0.0/24"})
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.post("/api/apps/wstest/workshop",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


async def test_update_conflict_when_absent(client, async_session, admin_token,
                                           tmp_path, monkeypatch):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.post("/api/apps/wstest/workshop/update",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


async def test_operation_conflict_409(client, async_session, admin_token,
                                      tmp_path, monkeypatch):
    from app.models import WorkshopOperation
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    async_session.add(WorkshopOperation(app_slug="autre", operation="update",
                                        status="running"))
    await async_session.commit()
    r = await client.post("/api/apps/wstest/workshop/update",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409
    assert "en cours" in r.json()["detail"]


async def test_create_ok(client, async_session, admin_token, tmp_path, monkeypatch):
    await _mk_app(async_session)
    app = tmp_path / "wstest"; app.mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    monkeypatch.setattr(route_mod, "_git_state",
                        _async_ret({"ok": True, "branch": "main", "head": "a"}))
    monkeypatch.setattr(route_mod, "has_active_workshop_operation", _async_ret(False))
    started = {"called": False}
    async def _fake_start(slug, operation, triggered_by, ref=None):
        started["called"] = True
        started["ref"] = ref
        assert operation == "create"
        return 42
    monkeypatch.setattr(route_mod, "start_workshop_operation", _fake_start)
    r = await client.post("/api/apps/wstest/workshop",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 201
    assert r.json()["operation_id"] == 42 and started["called"]
    assert started["ref"] is None          # pas de body → ref None (comportement historique)


async def test_status_shape(client, async_session, admin_token):
    await _mk_app(async_session)
    r = await client.get("/api/apps/wstest/workshop/status",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "current" in data and "history" in data
    assert data["current"] is None


# ── git-config ──
# Adaptations du brief : `rm` remplacé par le `route_mod` déjà importé en tête ;
# `get_auth_header` retiré des signatures (helper conftest importé, pas une fixture).

async def test_git_config_404(client, admin_token):
    r = await client.post("/api/apps/nimporte/git-config",
                          json={"git_repo_url": "https://x.git"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_git_config_401(client):
    r = await client.post("/api/apps/x/git-config", json={"git_repo_url": "https://x.git"})
    assert r.status_code == 401


async def test_git_config_rejects_ssh(client, async_session, admin_token):
    await _mk_app(async_session)
    for url in ("git@github.com:org/repo.git", "ssh://git@github.com/org/repo.git",
                "git://github.com/org/repo.git", ""):
        r = await client.post("/api/apps/wstest/git-config",
                              json={"git_repo_url": url},
                              headers=get_auth_header(admin_token))
        assert r.status_code == 400, url
    assert "SSH non supporté" in r.text or "URL git requise" in r.text


async def test_git_config_409_not_a_repo(client, async_session, admin_token,
                                         tmp_path, monkeypatch):
    await _mk_app(async_session)
    (tmp_path / "wstest").mkdir()
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)

    async def _real_remote(app_dir, url):  # pas un repo → RuntimeError
        raise RuntimeError("/pas/un/repo n'est pas un dépôt git — déployez l'app depuis son dépôt d'abord")
    monkeypatch.setattr(route_mod, "_configure_remote", _real_remote)
    r = await client.post("/api/apps/wstest/git-config",
                          json={"git_repo_url": "https://x.git"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 409


async def test_git_config_success(client, async_session, admin_token,
                                  monkeypatch):
    app_row = await _mk_app(async_session)

    async def _fake_remote(app_dir, url): return "added"

    async def _fake_test(url): return True, "Connexion OK — 3 branche(s)"
    monkeypatch.setattr(route_mod, "_configure_remote", _fake_remote)
    monkeypatch.setattr(route_mod, "test_git_connection", _fake_test)
    r = await client.post("/api/apps/wstest/git-config",
                          json={"git_repo_url": "https://github.com/org/repo.git"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 200 and r.json() == {
        "ok": True, "message": "Connexion OK — 3 branche(s)", "sync": "added"}
    await async_session.refresh(app_row)
    assert app_row.git_repo_url == "https://github.com/org/repo.git"


async def test_git_config_verdict_echec_auth(client, async_session, admin_token,
                                             monkeypatch):
    await _mk_app(async_session)

    async def _fake_remote(app_dir, url): return "added"

    async def _fake_test(url): return False, "remote: Access denied"
    monkeypatch.setattr(route_mod, "_configure_remote", _fake_remote)
    monkeypatch.setattr(route_mod, "test_git_connection", _fake_test)
    r = await client.post("/api/apps/wstest/git-config",
                          json={"git_repo_url": "https://x.git"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 200 and r.json()["ok"] is False


async def test_workshop_state_exposes_git_repo_url(client, async_session,
                                                   admin_token,
                                                   tmp_path, monkeypatch):
    app_row = await _mk_app(async_session)
    app_row.git_repo_url = "https://db.example/repo.git"
    await async_session.commit()
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)

    # Adaptation du brief : get_workshop_state est SYNC côté route (réalité
    # workshop_service) — le mock async du brief levait TypeError.
    def _state(slug): return ws.get_workshop_state(slug)

    async def _git_state(d): return {"ok": False, "branch": None, "head": None}
    monkeypatch.setattr(route_mod, "get_workshop_state", _state)
    monkeypatch.setattr(route_mod, "_git_state", _git_state)
    r = await client.get("/api/apps/wstest/workshop", headers=get_auth_header(admin_token))
    assert r.status_code == 200 and r.json()["git_repo_url"] == "https://db.example/repo.git"


async def test_patch_git_repo_url_syncs_remote_best_effort(client, async_session,
                                                           admin_token,
                                                           monkeypatch):
    app_row = await _mk_app(async_session)

    async def _fake_remote(app_dir, url): return "added"
    monkeypatch.setattr("app.workshop_service._configure_remote", _fake_remote)
    r = await client.patch(f"/api/apps/{app_row.id}",
                           json={"git_repo_url": "https://x.example/r.git"},
                           headers=get_auth_header(admin_token))
    assert r.status_code == 200
    assert r.json().get("git_sync") == "ok"

    async def _boom(app_dir, url): raise RuntimeError("pas un dépôt git")
    monkeypatch.setattr("app.workshop_service._configure_remote", _boom)
    r2 = await client.patch(f"/api/apps/{app_row.id}",
                            json={"git_repo_url": "https://y.example/r.git"},
                            headers=get_auth_header(admin_token))
    assert r2.status_code == 200          # best-effort : jamais bloquant
    assert str(r2.json().get("git_sync", "")).startswith("warning:")


# ─── Ref param (branches → atelier) ────────────────────────────────────

async def test_create_with_valid_ref_passes_ref(client, async_session, admin_token,
                                                tmp_path, monkeypatch):
    """POST /workshop {ref} → start_workshop_operation appelé avec ce ref."""
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    async def _git_ok(d): return {"ok": True, "branch": "main", "head": "abc"}
    monkeypatch.setattr(route_mod, "_git_state", _git_ok)
    calls = {}

    async def _fake_start(slug, operation, triggered_by, ref=None):
        calls.update(slug=slug, operation=operation, ref=ref)
        return 42

    monkeypatch.setattr(route_mod, "start_workshop_operation", _fake_start)
    r = await client.post("/api/apps/wstest/workshop", json={"ref": "feat/xyz"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 201
    assert calls["operation"] == "create" and calls["ref"] == "feat/xyz"


async def test_update_with_valid_ref_passes_ref(client, async_session, admin_token,
                                                tmp_path, monkeypatch):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    ws._write_ws_env_full(tmp_path / "wstest" / ".workshop.env", {
        "APP_SLUG": "wstest-ws", "API_PORT": "10000", "FRONTEND_PORT": "5200",
        "FRONTEND_HTTPS_PORT": "5201", "WS_REF": "main", "WS_COMMIT": "x",
        "APP_SUBNET": "10.210.0.0/24"})
    async def _git_ok(d): return {"ok": True, "branch": "main", "head": "abc"}
    monkeypatch.setattr(route_mod, "_git_state", _git_ok)
    calls = {}

    async def _fake_start(slug, operation, triggered_by, ref=None):
        calls.update(operation=operation, ref=ref)
        return 43

    monkeypatch.setattr(route_mod, "start_workshop_operation", _fake_start)
    r = await client.post("/api/apps/wstest/workshop/update",
                          json={"ref": "fix/health"},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 201
    assert calls["operation"] == "update" and calls["ref"] == "fix/health"


@pytest.mark.parametrize("bad", ["-flag", "a..b", "es pace", "a~b", "a^b", "x" * 200])
async def test_invalid_ref_rejected_400(client, async_session, admin_token,
                                        tmp_path, monkeypatch, bad):
    await _mk_app(async_session)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.post("/api/apps/wstest/workshop/update", json={"ref": bad},
                          headers=get_auth_header(admin_token))
    assert r.status_code == 400


async def test_branches_parses_github(client, async_session, admin_token, monkeypatch):
    """GET /{slug}/branches parse la réponse GitHub (défaut en tête)."""
    from app.routes import github_catalog  # noqa: F401  (s'assure que le module est chargé)
    import app.routes.workshop as wmod
    await _mk_app(async_session, repo="https://github.com/test-org/test-repo.git")

    async def _fake_fetch(url, db):
        if url.endswith("/repos/test-org/test-repo"):
            return ({"default_branch": "main"}, 200)
        return ([{"name": "feat/xyz", "commit": {"sha": "aaa111"}},
                 {"name": "main", "commit": {"sha": "bbb222"}}], 200)

    monkeypatch.setattr(wmod, "fetch_github_json", _fake_fetch)
    r = await client.get("/api/apps/wstest/branches",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["default_branch"] == "main"
    assert data["branches"][0]["name"] == "main"          # défaut en tête
    assert data["branches"][1]["name"] == "feat/xyz"


async def test_branches_without_repo_configured(client, async_session, admin_token):
    await _mk_app(async_session, repo=None)
    r = await client.get("/api/apps/wstest/branches",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False and data["repo_configured"] is False


async def test_branches_cached_30s(client, async_session, admin_token, monkeypatch):
    """Deux appels rapprochés → un seul passage réseau (cache 30 s)."""
    import app.routes.workshop as wmod
    await _mk_app(async_session, repo="https://github.com/test-org/test-repo.git")
    wmod._branches_cache.clear()
    n = {"count": 0}

    async def _fake_fetch(url, db):
        n["count"] += 1
        if url.endswith("/repos/test-org/test-repo"):
            return ({"default_branch": "main"}, 200)
        return ([{"name": "main", "commit": {"sha": "bbb222"}}], 200)

    monkeypatch.setattr(wmod, "fetch_github_json", _fake_fetch)
    r1 = await client.get("/api/apps/wstest/branches", headers=get_auth_header(admin_token))
    r2 = await client.get("/api/apps/wstest/branches", headers=get_auth_header(admin_token))
    assert r1.status_code == 200 and r2.status_code == 200
    assert n["count"] == 2                 # 1 repos + 1 branches, pas 2×2
    assert r2.json()["cached"] is True


async def test_workshop_state_dir_name_differs_from_slug(client, async_session,
                                                         admin_token, tmp_path,
                                                         monkeypatch):
    # Apps dont le dossier ≠ slug (ex. APPS/exemple-app pour « philia-proxy ») :
    # l'état atelier doit être lu via APP_SLUG du .env, pas le nom du dossier.
    await _mk_app(async_session, slug="myapp-proxy")
    app = tmp_path / "MyApp_IA"; app.mkdir(parents=True)
    (app / ".env").write_text("APP_SLUG=myapp-proxy\n")
    ws._write_ws_env_full(app / ".workshop.env", {
        "APP_SLUG": "myapp-proxy-ws", "API_PORT": "10007", "FRONTEND_PORT": "5207",
        "FRONTEND_HTTPS_PORT": "5208", "WS_REF": "main", "WS_COMMIT": "abc",
        "APP_SUBNET": "10.210.7.0/24"})
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    r = await client.get("/api/apps/myapp-proxy/workshop",
                         headers=get_auth_header(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["exists"] is True
    assert data["ws_slug"] == "myapp-proxy-ws"
    assert data["frontend_port"] == "5207"


def test_app_dir_resolver(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    # cas canonique : dossier == slug, .env concordant
    plain = tmp_path / "plain"; plain.mkdir()
    (plain / ".env").write_text("APP_SLUG=plain\n")
    assert ws._app_dir("plain") == plain
    # dossier ≠ slug : résolu par scan des .env
    odd = tmp_path / "Odd_Dir"; odd.mkdir()
    (odd / ".env").write_text("APP_SLUG=odd-slug\n")
    assert ws._app_dir("odd-slug") == odd
    # dossier == slug mais .env d'un AUTRE slug : le scan doit gagner
    hijack = tmp_path / "hijack"; hijack.mkdir()
    (hijack / ".env").write_text("APP_SLUG=autre\n")
    assert ws._app_dir("autre") == hijack
    # repli historique : rien ne matche → APPS/<slug>
    assert ws._app_dir("unknown") == tmp_path / "unknown"


async def test_create_ok_dir_differs_from_slug(client, async_session, admin_token,
                                               tmp_path, monkeypatch):
    # Le « passage en atelier » depuis la vue santé doit fonctionner pour les
    # apps dont le dossier ≠ slug : la route résout le dossier via APP_SLUG
    # (ex. APPS/exemple-app pour « philia-proxy ») avant le contrôle git.
    import subprocess

    await _mk_app(async_session, slug="myapp-proxy")
    app = tmp_path / "MyApp_IA"; app.mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services: {}\n")
    (app / ".env").write_text("APP_SLUG=myapp-proxy\n")
    subprocess.run(["git", "init", "-q"], cwd=app, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://git.example/x.git"],
                   cwd=app, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=app, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=app, check=True)
    (app / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=app, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=app, check=True)
    monkeypatch.setattr(ws, "get_apps_dir", lambda: tmp_path)
    monkeypatch.setattr(route_mod, "has_active_workshop_operation", _async_ret(False))
    started = {"called": False}
    async def _fake_start(slug, operation, triggered_by, ref=None):
        started["called"] = True
        return 7
    monkeypatch.setattr(route_mod, "start_workshop_operation", _fake_start)
    r = await client.post("/api/apps/myapp-proxy/workshop",
                          headers=get_auth_header(admin_token))
    assert r.status_code == 201, r.json()
    assert started["called"] is True
