# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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

    async def test_status_expose_conf_absente(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, monkeypatch,
    ):
        """État résiduel de l'incident 02/10 : enabled + initialisé mais
        wg0.conf absente → conf_exists doit le révéler à l'UI."""
        import app.routes.vpn as vpn_mod
        monkeypatch.setattr(vpn_mod, "_docker_exec", lambda c, cmd: (1, ""))

        await _seed_server_settings(async_session, initialized=True, enabled=True)
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/status", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["conf_exists"] is False

    async def test_status_expose_conf_presente(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, monkeypatch,
    ):
        import pathlib

        import app.routes.vpn as vpn_mod
        monkeypatch.setattr(vpn_mod, "_docker_exec", lambda c, cmd: (1, ""))

        await _seed_server_settings(async_session, initialized=True, enabled=True)
        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"
        conf_path.write_text("[Interface]\n")
        auth = get_auth_header(admin_token)

        resp = await client.get("/api/vpn/status", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["conf_exists"] is True


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

    async def test_toggle_enable_regenere_conf_absente(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, monkeypatch,
    ):
        """Bug 02/10/2026 : toggle OFF supprime wg0.conf, toggle ON ne le
        régénérait pas → conteneur démarré sans config (« conf=ABSENTE »).
        Le toggle ON doit auto-régénérer la conf si le serveur est initialisé."""
        import pathlib

        import app.routes.vpn as vpn_mod

        def fake_exec(container, cmd):
            # Aucun docker réel en tests : listen-port inconnu (conteneur absent)
            if cmd == ["wg", "show", "wg0", "listen-port"]:
                return 1, ""
            return 0, ""

        monkeypatch.setattr(vpn_mod, "_docker_exec", fake_exec)

        await _seed_server_settings(async_session, initialized=True, enabled=False)
        auth = get_auth_header(admin_token)
        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"

        # Reproduction du scénario incident : OFF (supprime la conf) puis ON
        resp = await client.put("/api/vpn/toggle", json={"enabled": False}, headers=auth)
        assert resp.status_code == 200
        assert not conf_path.exists()  # comportement OFF existant : conf supprimée

        resp = await client.put("/api/vpn/toggle", json={"enabled": True}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is True

        # Fix : la conf doit être régénérée (clés + peers toujours en DB)
        assert conf_path.exists()
        content = conf_path.read_text()
        assert "[Interface]" in content
        assert "ListenPort = 51820" in content

    async def test_toggle_enable_conf_deja_presente_ne_double_pas(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, monkeypatch,
    ):
        """Toggle ON quand la conf existe déjà : pas de réécriture inutile
        (comportement minimal, l'état fichier reste la source appliquée)."""
        import pathlib

        import app.routes.vpn as vpn_mod

        def fake_exec(container, cmd):
            if cmd == ["wg", "show", "wg0", "listen-port"]:
                return 1, ""
            return 0, ""

        monkeypatch.setattr(vpn_mod, "_docker_exec", fake_exec)

        await _seed_server_settings(async_session, initialized=True, enabled=False)
        auth = get_auth_header(admin_token)
        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text("# conf existante\n")
        before = conf_path.stat().st_mtime_ns

        resp = await client.put("/api/vpn/toggle", json={"enabled": True}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is True

        assert conf_path.read_text() == "# conf existante\n"
        assert conf_path.stat().st_mtime_ns == before

    async def test_toggle_enable_sans_serveur_initialise(
        self, client: AsyncClient, async_session: AsyncSession,
        admin_token: str, vpn_temp_dir: str, monkeypatch,
    ):
        """Revue froide 05/10 : toggle ON sans privkey en DB → pas de
        régénération, pas de crash, conf toujours absente (garde l.463)."""
        import pathlib

        import app.routes.vpn as vpn_mod
        monkeypatch.setattr(vpn_mod, "_docker_exec", lambda c, cmd: (1, ""))

        await _seed_server_settings(async_session, initialized=False, enabled=False)
        auth = get_auth_header(admin_token)
        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"

        resp = await client.put("/api/vpn/toggle", json={"enabled": True}, headers=auth)
        assert resp.status_code == 200
        assert resp.json()["vpn_enabled"] is True
        assert not conf_path.exists()

    async def test_regenerate_conf_cree_le_fichier_en_0600(
        self, async_session: AsyncSession, vpn_temp_dir: str, monkeypatch,
    ):
        """Revue froide 05/10 : la création du fichier (clé privée serveur)
        ne doit jamais exposer de fenêtre umask (~0644) — création en mode
        0600, y compris si le chmod final n'a pas pu s'exécuter."""
        import os
        import pathlib
        import stat as stat_mod

        import app.routes.vpn as vpn_mod
        monkeypatch.setattr(vpn_mod, "_docker_exec", lambda c, cmd: (1, ""))

        def _boom(self, mode):
            raise OSError("chmod simulé en échec")

        monkeypatch.setattr(vpn_mod.Path, "chmod", _boom)

        old_umask = os.umask(0o022)
        try:
            with pytest.raises(OSError):
                await vpn_mod._regenerate_and_sync(
                    async_session, _VALID_PRIVKEY, 51820, "10.8.0.0/24"
                )
        finally:
            os.umask(old_umask)

        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"
        mode = stat_mod.S_IMODE(conf_path.stat().st_mode)
        assert mode == 0o600, f"perms={oct(mode)} — fenêtre umask exposée"


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

        exec_calls: list[tuple[str, list[str]]] = []
        restart_calls: list[str] = []

        def fake_exec(container, cmd):
            exec_calls.append((container, list(cmd)))
            # Port actif simulé = 51820 (différent du 51821 demandé)
            if cmd == ["wg", "show", "wg0", "listen-port"]:
                return 0, "51820\n"
            return 0, ""

        def fake_restart(container):
            restart_calls.append(container)
            return True

        monkeypatch.setattr(vpn_mod, "_docker_exec", fake_exec)
        monkeypatch.setattr(vpn_mod, "_docker_restart", fake_restart)

        # listen_port=51821 alors que le mock renvoie 51820 → port a changé
        await vpn_mod._regenerate_and_sync(
            async_session, "PRIVKEY", 51821, "10.8.0.0/24"
        )

        assert len(restart_calls) == 1
        assert restart_calls[0] == "hub-wireguard"
        # syncconf ne doit PAS être appelé si le port a changé (restart uniquement)
        syncconf_calls = [cmd for _, cmd in exec_calls if "syncconf" in " ".join(cmd)]
        assert syncconf_calls == [], "syncconf ne doit pas être appelé si restart"

    async def test_regenere_fichier_preexistant_en_0444(
        self, async_session: AsyncSession, vpn_temp_dir: str, monkeypatch,
    ):
        """Minors revue finale : un wg0.conf préexistant aux perms
        fantaisistes (0444) ne doit pas faire échouer la régénération
        (O_WRONLY → EACCES) — remise à niveau puis réécriture."""
        import os
        import pathlib
        import stat as stat_mod

        import app.routes.vpn as vpn_mod
        monkeypatch.setattr(vpn_mod, "_docker_exec", lambda c, cmd: (1, ""))

        conf_path = pathlib.Path(vpn_temp_dir) / "wg_confs" / "wg0.conf"
        conf_path.write_text("# ancienne conf illisible\n")
        conf_path.chmod(0o444)

        await vpn_mod._regenerate_and_sync(
            async_session, _VALID_PRIVKEY, 51820, "10.8.0.0/24"
        )

        content = conf_path.read_text()
        assert "[Interface]" in content
        assert "# ancienne conf illisible" not in content
        mode = stat_mod.S_IMODE(conf_path.stat().st_mode)
        assert mode == 0o600, f"perms={oct(mode)}"

    async def test_port_unchanged_uses_syncconf(
        self, async_session: AsyncSession, vpn_temp_dir: str, monkeypatch,
    ):
        import app.routes.vpn as vpn_mod

        exec_calls: list[tuple[str, list[str]]] = []
        restart_calls: list[str] = []

        def fake_exec(container, cmd):
            exec_calls.append((container, list(cmd)))
            # Port actif simulé = 51820 = port demandé → inchangé
            if cmd == ["wg", "show", "wg0", "listen-port"]:
                return 0, "51820\n"
            return 0, ""

        def fake_restart(container):
            restart_calls.append(container)
            return True

        monkeypatch.setattr(vpn_mod, "_docker_exec", fake_exec)
        monkeypatch.setattr(vpn_mod, "_docker_restart", fake_restart)

        await vpn_mod._regenerate_and_sync(
            async_session, "PRIVKEY", 51820, "10.8.0.0/24"
        )

        assert restart_calls == [], "Aucun restart attendu si le port ne change pas"
        syncconf_calls = [cmd for _, cmd in exec_calls if "syncconf" in " ".join(cmd)]
        assert len(syncconf_calls) == 1, "syncconf doit être appelé si port inchangé"


# =============================================================================
# _parse_handshake_age — parsing de la durée 'latest handshake' de wg show
# =============================================================================

class TestParseHandshakeAge:
    """Le parsing de la durée brute de `wg show` doit convertir en secondes,
    indépendamment de la combinaison d'unités (s/m/h/d)."""

    def test_seconds_only(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("20 seconds ago") == 20

    def test_minutes_only(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("5 minutes ago") == 300

    def test_mixed_minutes_seconds(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("2 minutes, 15 seconds ago") == 135

    def test_hours_minutes(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("1 hour, 5 minutes ago") == 3900

    def test_days_hours_minutes(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("1 day, 3 hours, 2 minutes ago") == 97320

    def test_singular_units(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("1 minute, 1 second ago") == 61

    def test_empty_or_none(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age(None) is None
        assert _parse_handshake_age("") is None

    def test_unparseable(self):
        from app.routes.vpn import _parse_handshake_age
        assert _parse_handshake_age("jamais") is None


class TestPeerConnected:
    """Seuil de 'connecté' : un peer est connecté si son dernier handshake
    date de moins de _PEER_CONNECTED_THRESHOLD_SECONDS (180s = 3 min)."""

    def test_recent_handshake_connected(self):
        from app.routes.vpn import _peer_connected
        assert _peer_connected("45 seconds ago") is True
        assert _peer_connected("2 minutes, 30 seconds ago") is True  # 150s < 180

    def test_old_handshake_disconnected(self):
        from app.routes.vpn import _peer_connected
        # Bug de régression : avant, "5 minutes ago" était compté comme connecté
        # car contenait "minute". Maintenant 300s > 180s → déconnecté.
        assert _peer_connected("5 minutes ago") is False
        # "1 hour, 5 minutes" contenait "minute" → bug. Désormais déconnecté.
        assert _peer_connected("1 hour, 5 minutes ago") is False
        assert _peer_connected("1 hour ago") is False

    def test_no_handshake_disconnected(self):
        from app.routes.vpn import _peer_connected
        assert _peer_connected(None) is False
        assert _peer_connected("") is False

    def test_boundary_exactly_threshold(self):
        from app.routes.vpn import _peer_connected
        # 180s exactement = seuil = encore connecté (<=)
        assert _peer_connected("3 minutes ago") is True
        # 181s = déconnecté
        assert _peer_connected("3 minutes, 1 second ago") is False
