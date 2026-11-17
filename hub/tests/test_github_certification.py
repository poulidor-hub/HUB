# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub : filtrage du catalogue GET /repos (Task 5).

Règle d'affichage : visible ⟺ manifest_valid IS TRUE OU already_integrated.
Les masquées sont comptées : hidden_uncertified_count (invalidées + inconnues),
hidden_breakdown (détail par raison des invalidées), hidden_unchecked_count
(jamais vérifiées). GET /repos reste 100 % cache : zéro appel GitHub.
"""
import pytest

from app.models import Application, GithubRepoCache
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
