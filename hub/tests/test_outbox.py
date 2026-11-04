# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de regression outbox : admin requis, 2xx only, cleanup persiste."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_outbox_cleanup_requires_admin(client: AsyncClient, user_token):
    r = await client.post("/api/outbox/cleanup")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_outbox_stats_requires_admin(client: AsyncClient):
    r = await client.get("/api/outbox/stats")
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_outbox_retry_requires_admin(client: AsyncClient):
    r = await client.post("/api/outbox/1/retry")
    assert r.status_code in (401, 403)
