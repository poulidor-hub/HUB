"""
Tests des endpoints VPN WireGuard (mode serveur).
Couvre : init, server-config, peers CRUD, config download, qrcode, toggle, auth.
"""
import os
import tempfile
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import AppSetting, User, VpnPeer
from tests.conftest import get_auth_header


_VALID_PRIVKEY = "OUrAxOwyrwschr4I3Nj+FvNVzSOv2JyHZQRYwEb5Fzo="
_VALID_PUBKEY = "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI=="


async def _seed_server_settings(
    db: AsyncSession,
    *,
    initialized: bool = True,
    enabled: bool = False,
    endpoint: str = "203.0.113.10",
):
    settings = {
        "vpn_enabled": "true" if enabled else "false",
        "vpn_endpoint": endpoint,
        "vpn_listen_port": "51820",
        "vpn_subnet": "10.8.0.0/24",
    }
    if initialized:
        settings["vpn_server_private_key"] = _VALID_PRIVKEY
        settings["vpn_server_public_key"] = _VALID_PUBKEY

    for key, value in settings.items():
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    await db.commit()


@pytest.fixture
def vpn_temp_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "wg_confs"), exist_ok=True)
        monkeypatch.setenv("VPN_CONFIG_DIR", tmpdir)
        from app.config import get_settings
        get_settings.cache_clear()
        yield tmpdir
        get_settings.cache_clear()


@pytest.fixture
def mock_keypair(monkeypatch):
    """Mock wg genkey/pubkey pour éviter la dépendance binaire en tests."""
    import app.routes.vpn as vpn_mod
    original_run = vpn_mod.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 1 and cmd[0] == "wg":
            if "genkey" in cmd:
                class FakeResult:
                    returncode = 0
                    stdout = _VALID_PRIVKEY + "\n"
                    stderr = ""
                return FakeResult()
            if "pubkey" in cmd:
                class FakeResult:
                    returncode = 0
                    stdout = _VALID_PUBKEY + "\n"
                    stderr = ""
                return FakeResult()
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(vpn_mod.subprocess, "run", fake_run)
    return fake_run


# =============================================================================
# POST /api/vpn/init
# =============================================================================

class TestVpnInit:

    async def test_init_creates_keypair_and_settings(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=False)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/init", json={
            "endpoint": "10.0.0.1",
            "listen_port": 51820,
        }, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["status"] == "initialized"

    async def test_init_already_initialized_returns_409(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/init", json={
            "endpoint": "10.0.0.1",
        }, headers=auth)
        assert resp.status_code == 409

    async def test_init_requires_admin(
        self, client: AsyncClient, async_session: AsyncSession,
        vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=False)
        resp = await client.post("/api/vpn/init", json={
            "endpoint": "10.0.0.1",
        })
        assert resp.status_code in (401, 403)


# =============================================================================
# GET /api/vpn/server-config
# =============================================================================

class TestVpnServerConfig:

    async def test_get_config_when_initialized(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/server-config", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["initialized"] is True
        assert data["endpoint"] == "203.0.113.10"
        assert data["listen_port"] == 51820

    async def test_get_config_when_not_initialized(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=False)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/server-config", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["initialized"] is False


# =============================================================================
# GET /api/vpn/peers
# =============================================================================

class TestVpnPeersList:

    async def test_list_peers_empty(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/peers", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["peers"] == []

    async def test_list_peers_requires_admin(
        self, client: AsyncClient, async_session: AsyncSession,
        vpn_temp_dir: str,
    ):
        resp = await client.get("/api/vpn/peers")
        assert resp.status_code in (401, 403)


# =============================================================================
# POST /api/vpn/peers
# =============================================================================

class TestVpnCreatePeer:

    async def test_create_peer_success(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, admin_user: User, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/peers", json={
            "user_id": admin_user.id,
            "name": "Laptop",
        }, headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Laptop"
        assert data["vpn_ip"].startswith("10.8.0.")

    async def test_create_peer_user_not_found(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/peers", json={
            "user_id": 99999,
            "name": "Laptop",
        }, headers=auth)
        assert resp.status_code == 404

    async def test_create_peer_server_not_initialized(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, admin_user: User, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=False)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/peers", json={
            "user_id": admin_user.id,
            "name": "Laptop",
        }, headers=auth)
        assert resp.status_code == 400


# =============================================================================
# DELETE /api/vpn/peers/{id}
# =============================================================================

class TestVpnDeletePeer:

    async def test_delete_peer_success(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, admin_user: User, vpn_temp_dir: str, mock_keypair,
    ):
        await _seed_server_settings(async_session, initialized=True)

        peer = VpnPeer(
            user_id=admin_user.id, name="Laptop",
            public_key=_VALID_PUBKEY, private_key=_VALID_PRIVKEY,
            vpn_ip="10.8.0.2/32", enabled=True,
        )
        async_session.add(peer)
        await async_session.commit()
        await async_session.refresh(peer)

        auth = get_auth_header(admin_token)
        resp = await client.delete(f"/api/vpn/peers/{peer.id}", headers=auth)
        assert resp.status_code == 200

    async def test_delete_peer_not_found(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True)
        auth = get_auth_header(admin_token)

        resp = await client.delete("/api/vpn/peers/99999", headers=auth)
        assert resp.status_code == 404


# =============================================================================
# PUT /api/vpn/peers/{id}/toggle
# =============================================================================

class TestVpnTogglePeer:

    async def test_toggle_peer_disables(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, admin_user: User, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True)

        peer = VpnPeer(
            user_id=admin_user.id, name="Laptop",
            public_key=_VALID_PUBKEY, private_key=_VALID_PRIVKEY,
            vpn_ip="10.8.0.2/32", enabled=True,
        )
        async_session.add(peer)
        await async_session.commit()
        await async_session.refresh(peer)

        auth = get_auth_header(admin_token)
        resp = await client.put(f"/api/vpn/peers/{peer.id}/toggle", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


# =============================================================================
# GET /api/vpn/peers/{id}/config
# =============================================================================

class TestVpnPeerConfig:

    async def test_download_config(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, admin_user: User, vpn_temp_dir: str, monkeypatch,
    ):
        # HUB_HOST fixe → AllowedIPs déterministes (hub_host via get_hub_host()).
        monkeypatch.setenv("HUB_HOST", "203.0.113.42")
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            await _seed_server_settings(async_session, initialized=True)

            peer = VpnPeer(
                user_id=admin_user.id, name="Laptop Pro",
                public_key=_VALID_PUBKEY, private_key=_VALID_PRIVKEY,
                vpn_ip="10.8.0.2/32", enabled=True,
            )
            async_session.add(peer)
            await async_session.commit()
            await async_session.refresh(peer)

            auth = get_auth_header(admin_token)
            resp = await client.get(f"/api/vpn/peers/{peer.id}/config", headers=auth)
            assert resp.status_code == 200
            assert "attachment" in resp.headers.get("content-disposition", "")
            body = resp.content.decode()
            assert "[Interface]" in body
            assert "[Peer]" in body
            assert "DNS =" not in body            # aucun résolveur imposé au client
            assert "10.8.0.1/32" in body          # serveur VPN (gateway) via tunnel
            assert "203.0.113.42/32" in body      # hub_host : toutes les apps du host
        finally:
            get_settings.cache_clear()


# =============================================================================
# GET /api/vpn/status
# =============================================================================

class TestVpnStatus:

    async def test_status_when_disabled(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True, enabled=False)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/status", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


# =============================================================================
# PUT /api/vpn/toggle
# =============================================================================

class TestVpnToggle:

    async def test_toggle_enable(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_server_settings(async_session, initialized=True, enabled=False)
        auth = get_auth_header(admin_token)

        resp = await client.put("/api/vpn/toggle", json={"enabled": True}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is True


# =============================================================================
# _regenerate_and_sync — application des changements (fix wg syncconf)
# =============================================================================

class TestRegenerateAndSync:
    """wg syncconf ne modifie pas le ListenPort à chaud : un changement de port
    doit déclencher un redémarrage du conteneur hub-wireguard."""

    async def test_port_change_triggers_restart(
        self, async_session: AsyncSession, vpn_temp_dir: str, monkeypatch,
    ):
        import app.routes.vpn as vpn_mod

        calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd) if isinstance(cmd, list) else cmd)
            class R:
                returncode = 0
                # Port actif simulé = 51820 (différent du 51821 demandé)
                stdout = "51820\n" if "listen-port" in " ".join(cmd) else ""
                stderr = ""
            return R()

        monkeypatch.setattr(vpn_mod.subprocess, "run", fake_run)

        # listen_port=51821 alors que le mock renvoie 51820 → port a changé
        await vpn_mod._regenerate_and_sync(
            async_session, "PRIVKEY", 51821, "10.8.0.0/24"
        )

        restart_calls = [c for c in calls if c[:2] == ["docker", "restart"]]
        assert len(restart_calls) == 1
        assert restart_calls[0] == ["docker", "restart", "hub-wireguard"]

    async def test_port_unchanged_uses_syncconf(
        self, async_session: AsyncSession, vpn_temp_dir: str, monkeypatch,
    ):
        import app.routes.vpn as vpn_mod

        calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd) if isinstance(cmd, list) else cmd)
            class R:
                returncode = 0
                # Port actif simulé = 51820 = port demandé → inchangé
                stdout = "51820\n" if "listen-port" in " ".join(cmd) else ""
                stderr = ""
            return R()

        monkeypatch.setattr(vpn_mod.subprocess, "run", fake_run)

        await vpn_mod._regenerate_and_sync(
            async_session, "PRIVKEY", 51820, "10.8.0.0/24"
        )

        restart_calls = [c for c in calls if c[:2] == ["docker", "restart"]]
        assert restart_calls == [], "Aucun restart attendu si le port ne change pas"
        syncconf_calls = [c for c in calls if "syncconf" in " ".join(c)]
        assert len(syncconf_calls) == 1, "syncconf doit être appelé si port inchangé"
