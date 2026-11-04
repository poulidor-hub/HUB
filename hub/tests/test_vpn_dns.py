# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chantier B Task 7 — WireGuard pousse le DNS du hub aux peers (si zone configurée)."""

import pytest

from app.config import get_settings
from app.models import AppSetting, VpnPeer
from app.routes.vpn import _generate_client_conf, get_dns_line_for_peers

HUB_HOST = "192.0.2.10"


def _peer() -> VpnPeer:
    return VpnPeer(vpn_ip="10.8.0.2/32", private_key="PEER-PRIVATE-KEY",
                   public_key="PEER-PUB", name="Laptop", enabled=True)


def _conf(dns_line: str) -> str:
    return _generate_client_conf(
        _peer(), server_pubkey="SRV-PUB", endpoint="vpn.example.org",
        listen_port=51820, subnet="10.8.0.0/24", dns_line=dns_line,
    )


def test_client_conf_contains_dns_line_when_configured():
    conf = _conf(f"DNS = {HUB_HOST}")
    assert f"DNS = {HUB_HOST}" in conf
    # la ligne DNS vit dans [Interface] (avant [Peer])
    assert conf.index("DNS =") < conf.index("[Peer]")


def test_client_conf_without_zone_has_no_dns_line():
    conf = _conf("")
    assert "DNS =" not in conf


async def test_get_dns_line_with_zone(async_session):
    async_session.add(AppSetting(key="hub_dns_zone", value="hub.example.lan"))
    await async_session.commit()
    assert await get_dns_line_for_peers(async_session) == f"DNS = {HUB_HOST}"
    assert get_settings().get_hub_host() == HUB_HOST


async def test_get_dns_line_without_zone(async_session):
    assert await get_dns_line_for_peers(async_session) == ""
