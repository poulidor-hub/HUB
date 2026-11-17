# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub : manifeste hub-app.yaml (modèle, validation, fetch)."""
import pytest
from sqlalchemy import select
from app.models import GithubRepoCache

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
