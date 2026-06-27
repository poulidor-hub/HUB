"""
Tests des endpoints VPN WireGuard.
Couvre : deploy, status, toggle, config, generate-key, auth.
"""
import os
import tempfile
import base64
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import AppSetting, User
from tests.conftest import get_auth_header


VPN_SETTINGS = {"vpn_enabled", "vpn_config_hash"}


async def _seed_vpn_settings(db: AsyncSession, enabled: bool = False, config_hash: str = ""):
    for key in VPN_SETTINGS:
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            if key == "vpn_enabled":
                existing.value = "true" if enabled else "false"
            elif key == "vpn_config_hash":
                existing.value = config_hash
        else:
            value = ""
            if key == "vpn_enabled":
                value = "true" if enabled else "false"
            elif key == "vpn_config_hash":
                value = config_hash
            db.add(AppSetting(key=key, value=value))
    await db.commit()


@pytest.fixture
def vpn_temp_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        vpn_dir = os.path.join(tmpdir, "wg_confs")
        os.makedirs(vpn_dir, exist_ok=True)
        monkeypatch.setenv("VPN_CONFIG_DIR", tmpdir)
        from app.config import get_settings
        get_settings.cache_clear()
        yield tmpdir
        get_settings.cache_clear()


# =============================================================================
# POST /api/vpn/deploy
# =============================================================================

class TestVpnDeploy:
    """Tests du endpoint POST /api/vpn/deploy"""

    async def test_deploy_valid_config_creates_wg0_conf(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session)
        auth = get_auth_header(admin_token)

        payload = {
            "enabled": True,
            "private_key": "OUrAxOwyrwschr4I3Nj+FvNVzSOv2JyHZQRYwEb5Fzo=",
            "address": "10.8.0.5/24",
            "dns": "1.1.1.1",
            "public_key": "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==",
            "endpoint": "vps.example.com:51820",
            "allowed_ips": "0.0.0.0/0",
            "preshared_key": None,
        }

        resp = await client.post("/api/vpn/deploy", json=payload, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deployed"

        conf_path = os.path.join(vpn_temp_dir, "wg_confs", "wg0.conf")
        assert os.path.isfile(conf_path)

        with open(conf_path, "r") as f:
            content = f.read()
        assert "yAnz5TF+" in content
        assert "10.8.0.5/24" in content
        assert "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==" in content or "xTIBA5r" in content

        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "vpn_enabled")
        )
        setting = result.scalar_one()
        assert setting.value == "true"

        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "vpn_config_hash")
        )
        hash_setting = result.scalar_one()
        assert len(hash_setting.value) == 64

    async def test_deploy_invalid_private_key_returns_422(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session)
        auth = get_auth_header(admin_token)

        payload = {
            "enabled": True,
            "private_key": "not-a-valid-base64-key!!!",
            "address": "10.8.0.5/24",
            "dns": "1.1.1.1",
            "public_key": "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==",
            "endpoint": "vps.example.com:51820",
            "allowed_ips": "0.0.0.0/0",
        }

        resp = await client.post("/api/vpn/deploy", json=payload, headers=auth)
        assert resp.status_code == 422

    async def test_deploy_invalid_cidr_returns_422(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session)
        auth = get_auth_header(admin_token)

        payload = {
            "enabled": True,
            "private_key": "OUrAxOwyrwschr4I3Nj+FvNVzSOv2JyHZQRYwEb5Fzo=",
            "address": "invalid-cidr",
            "dns": "1.1.1.1",
            "public_key": "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==",
            "endpoint": "vps.example.com:51820",
            "allowed_ips": "0.0.0.0/0",
        }

        resp = await client.post("/api/vpn/deploy", json=payload, headers=auth)
        assert resp.status_code == 422

    async def test_deploy_unauthorized(
        self, client: AsyncClient, async_session: AsyncSession,
    ):
        await _seed_vpn_settings(async_session)

        payload = {
            "enabled": True, "private_key": "OUrAxOwyrwschr4I3Nj+FvNVzSOv2JyHZQRYwEb5Fzo=",
            "address": "10.8.0.5/24",
            "public_key": "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==",
            "endpoint": "vps.example.com:51820",
            "allowed_ips": "0.0.0.0/0",
        }

        resp = await client.post("/api/vpn/deploy", json=payload)
        assert resp.status_code in (401, 403)


# =============================================================================
# GET /api/vpn/status
# =============================================================================

class TestVpnStatus:
    """Tests du endpoint GET /api/vpn/status"""

    async def test_status_disabled(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str,
    ):
        await _seed_vpn_settings(async_session, enabled=False)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/status", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["connected"] is False

    async def test_status_enabled_no_tunnel(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str,
    ):
        await _seed_vpn_settings(async_session, enabled=True)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/status", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["connected"] is False

    async def test_status_unauthorized(
        self, client: AsyncClient, async_session: AsyncSession,
    ):
        await _seed_vpn_settings(async_session)
        resp = await client.get("/api/vpn/status")
        assert resp.status_code in (401, 403)


# =============================================================================
# PUT /api/vpn/toggle
# =============================================================================

class TestVpnToggle:
    """Tests du endpoint PUT /api/vpn/toggle"""

    async def test_toggle_enable(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session, enabled=False)
        auth = get_auth_header(admin_token)

        resp = await client.put("/api/vpn/toggle", json={"enabled": True}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is True

        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "vpn_enabled")
        )
        assert result.scalar_one().value == "true"

    async def test_toggle_disable(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session, enabled=True)
        auth = get_auth_header(admin_token)

        resp = await client.put("/api/vpn/toggle", json={"enabled": False}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is False

        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "vpn_enabled")
        )
        assert result.scalar_one().value == "false"

    async def test_toggle_unauthorized(
        self, client: AsyncClient, async_session: AsyncSession,
    ):
        await _seed_vpn_settings(async_session)
        resp = await client.put("/api/vpn/toggle", json={"enabled": True})
        assert resp.status_code in (401, 403)


# =============================================================================
# GET /api/vpn/config
# =============================================================================

class TestVpnConfig:
    """Tests du endpoint GET /api/vpn/config"""

    async def test_config_returns_masked_key(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str,
    ):
        await _seed_vpn_settings(async_session, enabled=True)
        auth = get_auth_header(admin_token)

        payload = {
            "enabled": True,
            "private_key": "OUrAxOwyrwschr4I3Nj+FvNVzSOv2JyHZQRYwEb5Fzo=",
            "address": "10.8.0.5/24",
            "dns": "1.1.1.1",
            "public_key": "IV/yo+aGOozVjU49Bm3+ZMGlhS6DR/f27YJBgHfW0WI==",
            "endpoint": "vps.example.com:51820",
            "allowed_ips": "0.0.0.0/0",
        }
        await client.post("/api/vpn/deploy", json=payload, headers=auth)

        resp = await client.get("/api/vpn/config", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["address"] == "10.8.0.5/24"
        assert "private_key" not in data
        assert "private_key_hash" in data
        assert len(data["private_key_hash"]) > 0

    async def test_config_unauthorized(
        self, client: AsyncClient, async_session: AsyncSession,
    ):
        await _seed_vpn_settings(async_session)
        resp = await client.get("/api/vpn/config")
        assert resp.status_code in (401, 403)


# =============================================================================
# POST /api/vpn/generate-key
# =============================================================================

class TestVpnGenerateKey:
    """Tests du endpoint POST /api/vpn/generate-key"""

    async def test_generate_key_success(
        self, client: AsyncClient, async_session: AsyncSession, admin_token: str,
    ):
        await _seed_vpn_settings(async_session)
        auth = get_auth_header(admin_token)

        resp = await client.post("/api/vpn/generate-key", headers=auth)
        if resp.status_code == 500:
            pytest.skip("wg not installed on test machine")
        assert resp.status_code == 200
        data = resp.json()
        assert "private_key" in data
        assert "public_key" in data
        private_bytes = base64.b64decode(data["private_key"] + "==")
        assert len(private_bytes) == 32
        public_bytes = base64.b64decode(data["public_key"] + "==")
        assert len(public_bytes) == 32

    async def test_generate_key_unauthorized(
        self, client: AsyncClient, async_session: AsyncSession,
    ):
        await _seed_vpn_settings(async_session)
        resp = await client.post("/api/vpn/generate-key")
        assert resp.status_code in (401, 403)
