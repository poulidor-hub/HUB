# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub : manifeste hub-app.yaml (modèle, validation, fetch)."""
import pytest
from sqlalchemy import select
from app.models import GithubRepoCache
from app.hub_app_manifest import (validate_manifest_text, REASON_MISSING,
                                  REASON_INVALID_YAML, REASON_SCHEMA_INVALID,
                                  REASON_REPO_MISMATCH)

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
  name: exemple-app
  slug: exemple-app
  description: Gestion de l'inventaire
  repository: https://github.com/org/exemple-app.git
spec:
  type: business
  healthCheck: /api/health
"""

def _reason(text, url=None): return validate_manifest_text(text, url)

def test_valid_manifest():
    r = _reason(VALID_YAML, "https://github.com/org/exemple-app.git")
    assert r.valid is True and r.slug == "exemple-app" and r.name == "exemple-app" and r.reason_key is None

def test_invalid_yaml(): assert _reason("a: [unclosed").valid is False and _reason("a: [unclosed").reason_key == REASON_INVALID_YAML

def test_non_mapping_yaml():
    for t in ("- a\n- b", "just a string", "123"):
        r = _reason(t); assert r.valid is False and r.reason_key == REASON_INVALID_YAML

def test_wrong_api_version(): assert _reason(VALID_YAML.replace("hub.poulidor/v1", "hub.poulidor/v2")).reason_key == REASON_SCHEMA_INVALID

def test_wrong_kind(): assert _reason(VALID_YAML.replace("HubApp", "Something")).reason_key == REASON_SCHEMA_INVALID

def test_bad_slug(): assert _reason(VALID_YAML.replace("exemple-app", "exemple-app!")).reason_key == REASON_SCHEMA_INVALID

def test_bad_type(): assert _reason(VALID_YAML.replace("business", "widget")).reason_key == REASON_SCHEMA_INVALID

def test_missing_required_field(): assert _reason("apiVersion: hub.poulidor/v1\nkind: HubApp\nmetadata:\n  name: X\nspec:\n  type: business").reason_key == REASON_SCHEMA_INVALID

def test_unknown_fields_tolerated(): assert _reason(VALID_YAML + "  futureField: whatever\n", "https://github.com/org/exemple-app.git").valid is True

def test_repository_matches_with_variants():
    for u in ("git@github.com:org/exemple-app.git", "https://github.com/org/exemple-app", "https://github.com/org/exemple-app/"):
        assert _reason(VALID_YAML, u).valid is True, u

def test_repository_mismatch():
    r = _reason(VALID_YAML, "https://github.com/other/repo.git")
    assert r.valid is False and r.reason_key == REASON_REPO_MISMATCH

def test_no_repository_field_ok(): assert _reason(VALID_YAML.replace("  repository: https://github.com/org/exemple-app.git\n", "")).valid is True
