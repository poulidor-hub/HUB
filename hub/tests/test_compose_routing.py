# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chantier B Task 6 — garde-fou compose (hub-dns + vhosts gateway) + hooks.

Réutilise le découpeur de blocs de test_compose_socket_proxy (sans PyYAML).
"""

import pathlib
import re

import pytest
from unittest.mock import patch, AsyncMock

from tests.test_compose_socket_proxy import _service_blocks

HUB_BASE = pathlib.Path(__file__).resolve().parents[2]

# HOST_ONLY : le compose n'est pas copié dans l'image — run hôte uniquement
import os as _os
if not _os.path.exists(str(HUB_BASE / "docker-compose.yml")):
    pytest.skip("fichiers checkout hors image — run hôte", allow_module_level=True)
COMPOSE = HUB_BASE / "docker-compose.yml"


def test_compose_has_hub_dns_service():
    blocs = _service_blocks(COMPOSE.read_text())
    assert "hub-dns" in blocs, "service hub-dns absent du compose"
    bloc = blocs["hub-dns"]
    assert "network_mode: host" in bloc, "hub-dns doit être en host network (tunnel WG)"
    assert "ports:" not in bloc, "hub-dns ne publie aucun port (53 via host network)"
    assert "dnsmasq.d-generated" in bloc, "hub-dns doit monter la zone générée (volume dédié)"
    assert "mem_limit: 64m" in bloc


def test_compose_publish_dirs_mounted():
    blocs = _service_blocks(COMPOSE.read_text())
    assert "/code/dns-generated:rw" in blocs["hub-api"], "hub-api doit écrire la zone (/code/dns-generated:rw)"
    assert "/code/gateway:rw" in blocs["hub-api"], "hub-api doit écrire les vhosts (/code/gateway:rw)"
    assert "vhosts.d" in blocs["hub-frontend"], (
        "hub-frontend doit lire les vhosts générés (/etc/nginx/vhosts.d)"
    )


def test_frontend_nginx_includes_vhosts_dir():
    entrypoint = (HUB_BASE / "hub" / ".." / "frontend" / "docker-entrypoint.sh")
    entrypoint = entrypoint.resolve()
    content = entrypoint.read_text()
    assert "vhosts.d" in content, "l'entrypoint doit inclure /etc/nginx/vhosts.d/*.conf"


# ---------------------------------------------------------------- hooks


@pytest.mark.asyncio
async def test_toggle_active_triggers_regenerate(client, admin_token, async_session,
                                                 no_publish):
    """Le toggle actif/inactif d'une app déclenche la publication (regenerate)."""
    from app.models import Application

    app = Application(name="Inv", slug="exemple-app", api_url="http://192.0.2.10:9001",
                      frontend_url="http://192.0.2.10:4201", dns_name="exemple-app")
    async_session.add(app)
    await async_session.commit()

    resp = await client.patch(f"/api/apps/{app.id}/toggle-active",
                              headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert no_publish["n"] >= 1, "toggle-active doit déclencher regenerate()"


def test_admin_tabs_panes_are_closed():
    """Régression : un tab-pane admin non fermé aspire le HTML suivant et
    rend plusieurs menus admin vides (incident 03/11/2026)."""
    import pathlib
    index = pathlib.Path(HUB_BASE, "frontend", "public", "index.html").read_text(encoding="utf-8")
    # hors <script> (innerHTML JS) et commentaires (exemples HTML) — puis
    # équilibre global : un tab-pane non fermé (régression onglets vides
    # du 03/11/2026) déséquilibre TOUT le fichier.
    index = re.sub(r"<script.*?</script>", "", index, flags=re.S)
    index = re.sub(r"<!--.*?-->", "", index, flags=re.S)
    assert index.count("<div") == index.count("</div>"), (
        "index.html déséquilibré : un tab-pane admin n'est pas fermé — "
        "le HTML suivant serait aspiré et les menus deviendraient vides"
    )
