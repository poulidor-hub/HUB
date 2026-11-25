# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub : manifeste hub-app.yaml (modèle, validation, fetch)."""
import pytest
from sqlalchemy import select
from app.models import GithubRepoCache
from app.hub_app_manifest import (validate_manifest_text, check_manifest,
                                  update_cache_manifest, ManifestCheckResult,
                                  REASON_MISSING,
                                  REASON_INVALID_YAML, REASON_SCHEMA_INVALID,
                                  REASON_REPO_MISMATCH)
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_repo_cache_has_manifest_columns(async_session):
    """La migration 069 (modèle) expose les 5 colonnes du certificat."""
    row = GithubRepoCache(repo_id=1, name="x", full_name="o/x", owner="o",
                          html_url="https://github.com/o/x", clone_url="https://github.com/o/x.git")
    async_session.add(row)
    await async_session.commit()
    loaded = (await async_session.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == 1))).scalar_one()
    assert loaded.manifest_valid is None
    assert loaded.manifest_checked_at is None
    assert loaded.manifest_reason is None
    assert loaded.manifest_name is None
    assert loaded.manifest_slug is None


# ---------------------------------------------------------------------------
# Task 2 : validate_manifest_text (validation pure, sans I/O)
# ---------------------------------------------------------------------------

VALID_YAML = """apiVersion: hub.poulidor/v1
kind: HubApp
metadata:
  name: Mon App
  slug: mon-app
  description: Description de mon app
  repository: https://github.com/org/mon-app.git
spec:
  type: business
  healthCheck: /api/health
"""

def _reason(text, url=None): return validate_manifest_text(text, url)

def test_valid_manifest():
    r = _reason(VALID_YAML, "https://github.com/org/mon-app.git")
    assert r.valid is True and r.slug == "mon-app" and r.name == "Mon App" and r.reason_key is None

def test_invalid_yaml(): assert _reason("a: [unclosed").valid is False and _reason("a: [unclosed").reason_key == REASON_INVALID_YAML

def test_non_mapping_yaml():
    for t in ("- a\n- b", "just a string", "123"):
        r = _reason(t); assert r.valid is False and r.reason_key == REASON_INVALID_YAML

def test_wrong_api_version(): assert _reason(VALID_YAML.replace("hub.poulidor/v1", "hub.poulidor/v2")).reason_key == REASON_SCHEMA_INVALID

def test_wrong_kind(): assert _reason(VALID_YAML.replace("HubApp", "Something")).reason_key == REASON_SCHEMA_INVALID

def test_bad_slug(): assert _reason(VALID_YAML.replace("mon-app", "Mon_App!")).reason_key == REASON_SCHEMA_INVALID

def test_bad_type(): assert _reason(VALID_YAML.replace("business", "widget")).reason_key == REASON_SCHEMA_INVALID

def test_missing_required_field(): assert _reason("apiVersion: hub.poulidor/v1\nkind: HubApp\nmetadata:\n  name: X\nspec:\n  type: business").reason_key == REASON_SCHEMA_INVALID

def test_unknown_fields_tolerated(): assert _reason(VALID_YAML + "  futureField: whatever\n", "https://github.com/org/mon-app.git").valid is True

def test_repository_matches_with_variants():
    for u in ("git@github.com:org/mon-app.git", "https://github.com/org/mon-app", "https://github.com/org/mon-app/"):
        assert _reason(VALID_YAML, u).valid is True, u

def test_repository_mismatch():
    r = _reason(VALID_YAML, "https://github.com/other/repo.git")
    assert r.valid is False and r.reason_key == REASON_REPO_MISMATCH

def test_no_repository_field_ok(): assert _reason(VALID_YAML.replace("  repository: https://github.com/org/mon-app.git\n", "")).valid is True


# ---------------------------------------------------------------------------
# Task 3 : check_manifest (fetch raw GitHub) + update_cache_manifest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_manifest_404_is_missing(async_session, monkeypatch):
    monkeypatch.setattr("app.hub_app_manifest.fetch_github_raw_with_status",
                        AsyncMock(return_value=(None, 404)))
    r = await check_manifest("org", "repo", "main", async_session)
    assert r.valid is False and r.reason_key == REASON_MISSING

@pytest.mark.asyncio
async def test_check_manifest_network_error_is_unknown(async_session, monkeypatch):
    monkeypatch.setattr("app.hub_app_manifest.fetch_github_raw_with_status",
                        AsyncMock(return_value=(None, 0)))
    r = await check_manifest("org", "repo", "main", async_session)
    assert r.valid is None and r.reason_key is None

@pytest.mark.asyncio
async def test_check_manifest_success_validates(async_session, monkeypatch):
    monkeypatch.setattr("app.hub_app_manifest.fetch_github_raw_with_status",
                        AsyncMock(return_value=(VALID_YAML, 200)))
    r = await check_manifest("org", "mon-app", "main", async_session)
    assert r.valid is True and r.slug == "mon-app"

@pytest.mark.asyncio
async def test_check_manifest_other_http_error_is_unknown(async_session, monkeypatch):
    """Code ≥ 400 autre que 404 (5xx, 403 rate-limit…) : résultat inconnu,
    jamais une décertification."""
    monkeypatch.setattr("app.hub_app_manifest.fetch_github_raw_with_status",
                        AsyncMock(return_value=(None, 503)))
    r = await check_manifest("org", "repo", "main", async_session)
    assert r.valid is None and r.reason_key is None

@pytest.mark.asyncio
async def test_update_cache_unknown_does_not_advance_checked_at(async_session):
    """Seed puis update avec valid=None : checked_at reste NULL ;
    avec valid=False : checked_at posé + reason enregistrée."""
    row = GithubRepoCache(repo_id=7, name="x", full_name="o/x", owner="o",
                          html_url="https://github.com/o/x",
                          clone_url="https://github.com/o/x.git")
    async_session.add(row)
    await async_session.commit()

    await update_cache_manifest(async_session, 7,
                                ManifestCheckResult(valid=None, reason_key=None))
    loaded = (await async_session.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == 7))).scalar_one()
    assert loaded.manifest_checked_at is None
    assert loaded.manifest_valid is None
    assert loaded.manifest_reason is None

    await update_cache_manifest(async_session, 7,
                                ManifestCheckResult(valid=False, reason_key=REASON_MISSING))
    loaded = (await async_session.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == 7))).scalar_one()
    assert loaded.manifest_checked_at is not None
    assert loaded.manifest_valid is False
    assert loaded.manifest_reason == REASON_MISSING

@pytest.mark.asyncio
async def test_update_cache_valid_persists_certificate(async_session):
    """Chemin valid=True : le certificat complet (valid, name, slug, checked_at)
    persiste en base — rechargé depuis la session après commit."""
    row = GithubRepoCache(repo_id=9, name="x", full_name="o/x", owner="o",
                          html_url="https://github.com/o/x",
                          clone_url="https://github.com/o/x.git")
    async_session.add(row)
    await async_session.commit()

    await update_cache_manifest(async_session, 9,
                                ManifestCheckResult(valid=True, reason_key=None,
                                                    name="Mon App", slug="mon-app"))
    loaded = (await async_session.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == 9))).scalar_one()
    assert loaded.manifest_valid is True
    assert loaded.manifest_name == "Mon App"
    assert loaded.manifest_slug == "mon-app"
    assert loaded.manifest_checked_at is not None


# ---------------------------------------------------------------------------
# Task 4 : schémas enrichis (certificat exposé + compteurs de masqués)
# ---------------------------------------------------------------------------

def test_schemas_manifest_fields():
    from app.schemas import GithubRepo, GithubRepoListResponse, GithubManifestScanStatus, AppCreateSimplified
    repo = GithubRepo(name="x", full_name="o/x", owner="o", html_url="", git_url="",
                      fork=False, archived=False, private=False, manifest_valid=True, manifest_slug="x")
    assert repo.manifest_valid is True
    resp = GithubRepoListResponse(repos=[], total=0, token_configured=True,
                                  hidden_uncertified_count=3, hidden_breakdown={"manifest_missing": 2},
                                  hidden_unchecked_count=1)
    assert resp.hidden_uncertified_count == 3
    st = GithubManifestScanStatus(running=False)
    assert st.total == 0 and st.finished_at is None
    app = AppCreateSimplified(name="Mon app", slug="mon-app", bypass_manifest_check=True)
    assert app.bypass_manifest_check is True
