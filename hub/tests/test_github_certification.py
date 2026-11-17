# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub.

Task 5 : filtrage du catalogue GET /repos — visible ⟺ manifest_valid IS TRUE
OU already_integrated. Les masquées sont comptées : hidden_uncertified_count
(invalidées + inconnues), hidden_breakdown (détail par raison des invalidées),
hidden_unchecked_count (jamais vérifiées). GET /repos reste 100 % cache.

Task 7 : POST /prepare bloque sur un certificat invalide (revalidation LIVE,
422 reason_key) et pré-remplit l'identité du manifeste (slug + nom).
"""
from datetime import timedelta

import pytest

from app.models import Application, GithubRepoCache, utcnow
from tests.conftest import get_auth_header


def _seed_cache(async_session, repo_id: int, name: str, owner: str = "MYCOMPANY",
                manifest_valid=None, manifest_reason=None):
    """Seed une ligne GithubRepoCache (inspiré des seeds de test_github_catalog.py)."""
    async_session.add(GithubRepoCache(
        repo_id=repo_id, name=name, full_name=f"{owner}/{name}",
        owner=owner,
        html_url=f"https://github.com/{owner}/{name}",
        clone_url=f"https://github.com/{owner}/{name}.git",
        manifest_valid=manifest_valid,
        manifest_reason=manifest_reason,
    ))


@pytest.mark.asyncio
async def test_list_filters_uncertified_and_counts(client, admin_token, async_session):
    # A certifiée → visible ; B invalidée (manifest manquant) → masquée ;
    # C jamais vérifiée (NULL) → masquée (inconnue) ; D invalidée MAIS déjà
    # intégrée → visible quand même (règle : certificat OU intégration).
    _seed_cache(async_session, 1, "certified-a", manifest_valid=True)
    _seed_cache(async_session, 2, "uncertified-b",
                manifest_valid=False, manifest_reason="manifest_missing")
    _seed_cache(async_session, 3, "unchecked-c")
    _seed_cache(async_session, 4, "integrated-d",
                manifest_valid=False, manifest_reason="manifest_schema_invalid")
    async_session.add(Application(
        name="Integrated D", slug="integrated-d", api_url="http://h:9100",
        git_repo_url="https://github.com/MYCOMPANY/integrated-d.git",
    ))
    await async_session.commit()

    r = await client.get("/api/apps/github/repos", headers=get_auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    names = {x["name"] for x in body["repos"]}
    assert names == {"certified-a", "integrated-d"}          # B et C masquées
    assert body["total"] == 2
    assert body["hidden_uncertified_count"] == 2             # B (invalidée) + C (inconnue)
    assert body["hidden_breakdown"] == {"manifest_missing": 1}
    assert body["hidden_unchecked_count"] == 1
    by_name = {x["name"]: x for x in body["repos"]}
    assert by_name["certified-a"]["manifest_valid"] is True
    assert by_name["integrated-d"]["already_integrated"] is True


# ===== Task 6 : scan de fond des manifestes + statut live =====

import app.manifest_scan as manifest_scan
from app.hub_app_manifest import ManifestCheckResult
from unittest.mock import MagicMock, AsyncMock, patch


def _seed_scan_cache(async_session, repo_id: int, name: str,
                     default_branch="main", updated_at_github=None,
                     manifest_checked_at=None):
    """Seed une ligne GithubRepoCache pour le scan (branche + dates contrôlées)."""
    async_session.add(GithubRepoCache(
        repo_id=repo_id, name=name, full_name=f"MYCOMPANY/{name}",
        owner="MYCOMPANY",
        html_url=f"https://github.com/MYCOMPANY/{name}",
        clone_url=f"https://github.com/MYCOMPANY/{name}.git",
        default_branch=default_branch,
        updated_at_github=updated_at_github,
        manifest_checked_at=manifest_checked_at,
    ))


@pytest.mark.asyncio
async def test_run_manifest_scan_updates_rows(async_session, monkeypatch):
    """Scan de 2 repos : valid=True (slug mémorisé) et valid=False (raison),
    checked_at avance, état final running=False + finished_at présent."""
    from sqlalchemy import select
    _seed_scan_cache(async_session, 201, "scan-ok")
    _seed_scan_cache(async_session, 202, "scan-ko")
    await async_session.commit()

    mock_check = AsyncMock(side_effect=[
        ManifestCheckResult(valid=True, name="N", slug="s"),
        ManifestCheckResult(valid=False, reason_key="manifest_missing"),
    ])
    monkeypatch.setattr(manifest_scan, "check_manifest", mock_check)

    await manifest_scan.run_manifest_scan([201, 202])

    async_session.expire_all()  # sync : écritures faites par d'autres sessions
    rows = {r.repo_id: r for r in (
        await async_session.execute(select(GithubRepoCache))).scalars()}
    assert rows[201].manifest_valid is True
    assert rows[201].manifest_slug == "s"
    assert rows[201].manifest_checked_at is not None
    assert rows[202].manifest_valid is False
    assert rows[202].manifest_reason == "manifest_missing"
    assert rows[202].manifest_checked_at is not None

    status = manifest_scan.get_manifest_scan_status()
    assert status["running"] is False
    assert status["total"] == 2
    assert status["done"] == 2
    assert status["errors"] == 0
    assert status["started_at"] is not None
    assert status["finished_at"] is not None


@pytest.mark.asyncio
async def test_scan_network_error_keeps_checked_at(async_session, monkeypatch):
    """Résultat INCONNU (valid=None, panne réseau) : checked_at reste NULL,
    valid reste NULL, le compteur errors est incrémenté (inconnu ≠ non certifié)."""
    from sqlalchemy import select
    _seed_scan_cache(async_session, 203, "scan-flaky")
    await async_session.commit()

    mock_check = AsyncMock(return_value=ManifestCheckResult(valid=None, reason_key=None))
    monkeypatch.setattr(manifest_scan, "check_manifest", mock_check)

    await manifest_scan.run_manifest_scan([203])

    async_session.expire_all()
    row = (await async_session.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == 203))).scalar_one()
    assert row.manifest_checked_at is None
    assert row.manifest_valid is None

    status = manifest_scan.get_manifest_scan_status()
    assert status["errors"] >= 1
    assert status["done"] == 1
    assert status["running"] is False


@pytest.mark.asyncio
async def test_start_manifest_scan_guard(monkeypatch):
    """Un scan déjà actif → start_manifest_scan() renvoie False (jamais d'empilement)."""
    monkeypatch.setattr(
        manifest_scan, "_scan_state",
        {"running": True, "total": 0, "done": 0, "errors": 0,
         "started_at": None, "finished_at": None},
    )
    assert manifest_scan.start_manifest_scan() is False


@pytest.mark.asyncio
async def test_scan_selection_rule(async_session):
    """Sélection des ids à revalider : jamais vérifié OU stale (> 7 j) OU
    updated_at_github plus récent que le certificat ; les frais sont exclus ;
    les default_branch NULL sont inclus (non filtrant)."""
    now = utcnow()
    _seed_scan_cache(async_session, 401, "never-checked")                       # inclus
    _seed_scan_cache(async_session, 402, "stale",
                     manifest_checked_at=now - timedelta(days=30))              # inclus
    _seed_scan_cache(async_session, 403, "repo-updated",
                     updated_at_github=now - timedelta(hours=12),
                     manifest_checked_at=now - timedelta(days=1))               # inclus
    _seed_scan_cache(async_session, 404, "fresh-no-update",
                     updated_at_github=now - timedelta(days=2),
                     manifest_checked_at=now - timedelta(days=1))               # exclu
    _seed_scan_cache(async_session, 405, "recent-check",
                     manifest_checked_at=now - timedelta(hours=1))              # exclu
    _seed_scan_cache(async_session, 406, "branch-null", default_branch=None)    # inclus
    await async_session.commit()

    ids = await manifest_scan._select_repo_ids_to_revalidate()
    assert set(ids) == {401, 402, 403, 406}


@pytest.mark.asyncio
async def test_refresh_triggers_scan(client, admin_token, async_session, settings, monkeypatch):
    """Le refresh (upsert réussi) déclenche start_manifest_scan exactement 1 fois."""
    settings.github_token = "ghp_test"
    mock_start = MagicMock()
    monkeypatch.setattr("app.routes.github_catalog.start_manifest_scan", mock_start)

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = [{
        "id": 301, "name": "trigger-scan", "full_name": "MYCOMPANY/trigger-scan",
        "owner": {"login": "MYCOMPANY"},
        "html_url": "https://github.com/MYCOMPANY/trigger-scan",
        "clone_url": "https://github.com/MYCOMPANY/trigger-scan.git",
        "description": None, "default_branch": "main",
        "fork": False, "archived": False, "private": True,
        "updated_at": "2026-07-20T00:00:00Z",
    }]
    mock_resp.headers = {}
    with patch("app.routes.github_catalog.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client
        r = await client.post("/api/apps/github/repos/refresh",
                              headers=get_auth_header(admin_token))

    assert r.status_code == 200
    assert mock_start.call_count == 1


@pytest.mark.asyncio
async def test_scan_status_route_admin_only(client, admin_token, user_token, monkeypatch):
    """GET /repos/manifest-scan : 200 + état pour l'admin, 403 pour un user.

    État réinitialisé : l'état module traverse les tests, la route doit
    refléter exactement l'état courant (pas d'hypothèse d'ordre)."""
    monkeypatch.setattr(
        manifest_scan, "_scan_state",
        {"running": False, "total": 0, "done": 0, "errors": 0,
         "started_at": None, "finished_at": None},
    )
    r_admin = await client.get("/api/apps/github/repos/manifest-scan",
                               headers=get_auth_header(admin_token))
    assert r_admin.status_code == 200
    body = r_admin.json()
    assert body["running"] is False
    assert body["total"] == 0
    assert body["done"] == 0
    assert body["errors"] == 0

    r_user = await client.get("/api/apps/github/repos/manifest-scan",
                              headers=get_auth_header(user_token))
    assert r_user.status_code == 403


# ===== Task 7 : POST /prepare — porte de certification bloquante =====

import app.routes.github_catalog as gc


def _seed_prepare_cache(async_session, repo_id: int, name: str, owner: str = "org",
                        default_branch="main", manifest_valid=True):
    """Seed une ligne GithubRepoCache pour prepare (branche + certificat cache)."""
    async_session.add(GithubRepoCache(
        repo_id=repo_id, name=name, full_name=f"{owner}/{name}",
        owner=owner,
        html_url=f"https://github.com/{owner}/{name}",
        clone_url=f"https://github.com/{owner}/{name}.git",
        default_branch=default_branch,
        manifest_valid=manifest_valid,
    ))


def _mock_repo_access_ok(monkeypatch):
    """Préflight d'accès Git mocké OK (l'accès n'est PAS bloquant en prepare)."""
    from app.repo_access import RepoAccessResult

    async def _access(clone_url, token):
        return RepoAccessResult(True, "ok", "Accès au dépôt vérifié.")
    monkeypatch.setattr(gc, "check_repo_access", _access)


@pytest.mark.asyncio
async def test_prepare_blocks_on_missing_manifest(client, admin_token, async_session, monkeypatch):
    """Manifeste absent (valid=False, manifest_missing) → 422 + reason_key."""
    _seed_prepare_cache(async_session, 501, "repo-sans-certif")
    await async_session.commit()

    mock_check = AsyncMock(return_value=ManifestCheckResult(
        valid=False, reason_key="manifest_missing"))
    monkeypatch.setattr(gc, "check_manifest", mock_check)
    _mock_repo_access_ok(monkeypatch)  # ne doit pas être atteint

    r = await client.post("/api/apps/github/prepare",
                          headers=get_auth_header(admin_token),
                          json={"repo_full_name": "org/repo-sans-certif"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason_key"] == "manifest_missing"
    assert "hub-app.yaml" in detail["message"]
    assert mock_check.await_count == 1  # revalidation LIVE effectuée


@pytest.mark.asyncio
async def test_prepare_blocks_on_unknown_default_branch(client, admin_token, async_session, monkeypatch):
    """default_branch NULL en cache → 422 manifest_unverifiable, AVANT tout appel réseau."""
    _seed_prepare_cache(async_session, 502, "sans-branche", default_branch=None)
    await async_session.commit()

    mock_check = AsyncMock(return_value=ManifestCheckResult(valid=True))
    monkeypatch.setattr(gc, "check_manifest", mock_check)  # ne doit JAMAIS être appelé

    r = await client.post("/api/apps/github/prepare",
                          headers=get_auth_header(admin_token),
                          json={"repo_full_name": "org/sans-branche"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["reason_key"] == "manifest_unverifiable"
    assert mock_check.await_count == 0  # refus avant tout appel réseau


@pytest.mark.asyncio
async def test_prepare_success_uses_manifest_identity(client, admin_token, async_session, monkeypatch):
    """Certificat OK : le slug et le nom pré-remplis viennent du manifeste."""
    _seed_prepare_cache(async_session, 503, "repo-autre-nom")
    await async_session.commit()

    mock_check = AsyncMock(return_value=ManifestCheckResult(
        valid=True, name="Nom Manifeste", slug="slug-du-manifeste"))
    monkeypatch.setattr(gc, "check_manifest", mock_check)
    _mock_repo_access_ok(monkeypatch)

    r = await client.post("/api/apps/github/prepare",
                          headers=get_auth_header(admin_token),
                          json={"repo_full_name": "org/repo-autre-nom"})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "slug-du-manifeste"
    assert body["name"] == "Nom Manifeste"
    # Revalidation LIVE sur (owner, repo, branche du cache)
    assert mock_check.await_args.args[:3] == ("org", "repo-autre-nom", "main")


@pytest.mark.asyncio
async def test_prepare_slug_conflict_409(client, admin_token, async_session, monkeypatch):
    """Slug du manifeste déjà pris par une app → 409 (inchangé, mais sur le slug du manifeste)."""
    _seed_prepare_cache(async_session, 504, "repo-conflit")
    async_session.add(Application(
        name="Existant", slug="slug-du-manifeste", api_url="http://h:8000"))
    await async_session.commit()

    mock_check = AsyncMock(return_value=ManifestCheckResult(
        valid=True, name="Nom Manifeste", slug="slug-du-manifeste"))
    monkeypatch.setattr(gc, "check_manifest", mock_check)
    _mock_repo_access_ok(monkeypatch)

    r = await client.post("/api/apps/github/prepare",
                          headers=get_auth_header(admin_token),
                          json={"repo_full_name": "org/repo-conflit"})
    assert r.status_code == 409
    assert "slug-du-manifeste" in r.json()["detail"]
