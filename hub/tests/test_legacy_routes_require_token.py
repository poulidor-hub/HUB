# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Le partage et la spécification OpenAPI d'une application exigent un jeton M2M.

`PUT /api/apps/me/shared` et `POST /api/apps/me/openapi` acceptaient encore un
appel SANS jeton : adresse du réseau autorisée + en-tête `X-Slug`. L'entrée
publique est fermée, mais tout conteneur du réseau pouvait ainsi basculer le
partage M2M d'une application ou réécrire sa spécification (revue finale de la
branche, point 3). Ces deux routes rejoignent `/me/regenerate-secret`, qui
exige déjà un jeton.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

GARDE_IP = patch("app.routes.apps.is_docker_network_ip", return_value=True)
REFUS = 401


@pytest.mark.asyncio
async def test_shared_sans_jeton_est_refuse(client: AsyncClient, async_session: AsyncSession, test_app):
    """Adresse du réseau + X-Slug, sans jeton : refusé, le partage ne bouge pas."""
    assert test_app.is_shared_service is False

    with GARDE_IP:
        r = await client.put("/api/apps/me/shared?is_shared=true", headers={"X-Slug": test_app.slug})

    assert r.status_code == REFUS, r.text
    await async_session.refresh(test_app)
    assert test_app.is_shared_service is False


@pytest.mark.asyncio
async def test_openapi_sans_jeton_est_refuse(client: AsyncClient, async_session: AsyncSession, test_app):
    """Adresse du réseau + X-Slug, sans jeton : refusé, la spécification ne bouge pas."""
    assert not test_app.api_endpoints

    with GARDE_IP:
        r = await client.post("/api/apps/me/openapi",
                              json={"openapi_spec": {"openapi": "3.0.0", "paths": {"/x": {}}}},
                              headers={"X-Slug": test_app.slug})

    assert r.status_code == REFUS, r.text
    await async_session.refresh(test_app)
    assert not test_app.api_endpoints
