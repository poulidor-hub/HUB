"""
Routes VPN WireGuard — Serveur VPN permettant aux utilisateurs HUB de se
connecter au HUB à distance.

Endpoints admin :
- GET  /api/vpn/status        : État du serveur VPN
- PUT  /api/vpn/toggle        : Activer/désactiver le serveur
- GET  /api/vpn/server-config : Configuration du serveur
- PUT  /api/vpn/server-config : Mettre à jour la configuration
- POST /api/vpn/init          : Initialiser le serveur (génère la keypair)
- POST /api/vpn/generate-key  : Générer une paire de clés WireGuard
- GET  /api/vpn/peers         : Lister les peers (+ stats live)
- POST /api/vpn/peers         : Créer un peer
- DELETE /api/vpn/peers/{id}  : Supprimer un peer
- PUT  /api/vpn/peers/{id}/toggle : Activer/désactiver un peer
- GET  /api/vpn/peers/{id}/config  : Télécharger le .conf client
- GET  /api/vpn/peers/{id}/qrcode  : QR code SVG
"""

import hashlib
import ipaddress
import io
import logging
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import AppSetting, User, VpnPeer, utcnow
from app.auth import get_current_admin
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vpn", tags=["VPN"])

# Clés app_settings
VPN_ENABLED_KEY = "vpn_enabled"
VPN_SERVER_PRIVKEY = "vpn_server_private_key"
VPN_SERVER_PUBKEY = "vpn_server_public_key"
VPN_ENDPOINT = "vpn_endpoint"
VPN_LISTEN_PORT = "vpn_listen_port"
VPN_SUBNET = "vpn_subnet"

_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _is_valid_wg_key(s: str) -> bool:
    if not s or len(s) != 44:
        return False
    return bool(_WG_KEY_RE.match(s))


def _wg_conf_path() -> Path:
    config_dir = get_settings().vpn_config_dir
    return Path(config_dir) / "wg_confs" / "wg0.conf"


def _wg_conf_dir() -> Path:
    p = _wg_conf_path().parent
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _get_setting(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_setting(db: AsyncSession, key: str, value: str, user_id: int) -> None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
        setting.updated_at = utcnow()
        setting.updated_by_id = user_id


def _generate_keypair() -> tuple[str, str]:
    """Génère une paire de clés WireGuard. Retourne (private_key, public_key)."""
    try:
        r1 = subprocess.run(["wg", "genkey"], capture_output=True, text=True, timeout=5)
        if r1.returncode != 0:
            raise RuntimeError("wg genkey failed")
        private_key = r1.stdout.strip()

        r2 = subprocess.run(["wg", "pubkey"], input=private_key,
                            capture_output=True, text=True, timeout=5)
        if r2.returncode != 0:
            raise RuntimeError("wg pubkey failed")
        public_key = r2.stdout.strip()

        return private_key, public_key
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WireGuard (wg) n'est pas installé sur le serveur"
        )


def _generate_server_conf(
    server_privkey: str,
    listen_port: int,
    subnet: str,
    peers: list[VpnPeer],
) -> str:
    """Génère le contenu du wg0.conf serveur avec tous les peers."""
    server_ip = str(ipaddress.IPv4Network(subnet, strict=False).network_address + 1)

    lines = [
        "[Interface]",
        f"PrivateKey = {server_privkey}",
        f"Address = {server_ip}/{_cidr_prefix_len(subnet)}",
        f"ListenPort = {listen_port}",
    ]

    for peer in peers:
        if not peer.enabled:
            continue
        lines.append("")
        lines.append(f"# {peer.user.username} - {peer.name}")
        lines.append("[Peer]")
        lines.append(f"PublicKey = {peer.public_key}")
        lines.append(f"AllowedIPs = {peer.vpn_ip.split('/')[0]}/32")

    return "\n".join(lines) + "\n"


def _cidr_prefix_len(cidr: str) -> int:
    """Extrait le prefix length d'un CIDR (ex: '10.8.0.0/24' → 24)."""
    return int(cidr.split("/")[1]) if "/" in cidr else 24


def _generate_client_conf(
    peer: VpnPeer,
    server_pubkey: str,
    endpoint: str,
    listen_port: int,
) -> str:
    """Génère le .conf client pour un peer (téléchargement par l'utilisateur)."""
    peer_ip = peer.vpn_ip.split("/")[0]
    return (
        "[Interface]\n"
        f"PrivateKey = {peer.private_key}\n"
        f"Address = {peer_ip}/32\n"
        f"DNS = 10.8.0.1\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"Endpoint = {endpoint}:{listen_port}\n"
        f"AllowedIPs = 10.8.0.1/32\n"
        "PersistentKeepalive = 25\n"
    )


async def _allocate_peer_ip(db: AsyncSession, subnet: str) -> str:
    """Alloue la prochaine IP disponible dans le sous-réseau (à partir de .2)."""
    network = ipaddress.IPv4Network(subnet, strict=False)
    used_ips: set[str] = set()

    result = await db.execute(select(VpnPeer.vpn_ip))
    for (vpn_ip,) in result.all():
        used_ips.add(vpn_ip.split("/")[0])

    # .1 = serveur, on commence à .2
    for i in range(2, network.num_addresses - 1):
        candidate = str(network.network_address + i)
        if candidate not in used_ips:
            return f"{candidate}/32"

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Plus d'IP disponible dans le sous-réseau VPN"
    )


async def _regenerate_and_sync(
    db: AsyncSession,
    server_privkey: str,
    listen_port: int,
    subnet: str,
) -> None:
    """Régénère le wg0.conf depuis les peers en DB et applique via wg syncconf."""
    result = await db.execute(
        select(VpnPeer).order_by(VpnPeer.id)
    )
    peers = list(result.scalars().all())

    conf_content = _generate_server_conf(server_privkey, listen_port, subnet, peers)

    conf_path = _wg_conf_path()
    _wg_conf_dir()
    conf_path.write_text(conf_content)
    conf_path.chmod(0o600)

    # Appliquer à chaud si le conteneur tourne
    try:
        subprocess.run(
            ["docker", "exec", "hub-wireguard",
             "bash", "-c", "wg syncconf wg0 <(wg-quick strip wg0)"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass  # Conteneur arrêté — la conf sera appliquée au prochain démarrage


def _wg_status() -> dict | None:
    """Lit l'état du tunnel via docker exec wg show."""
    try:
        result = subprocess.run(
            ["docker", "exec", "hub-wireguard", "wg", "show", "wg0"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        if not output:
            return None
        return _parse_wg_show_output(output)
    except Exception:
        return None


def _parse_wg_show_output(output: str) -> dict:
    """Parse la sortie de 'wg show wg0'. Retourne {peers: [{public_key, endpoint, ...}]}."""
    data: dict = {"peers": []}
    current_peer: dict | None = None
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("interface:"):
            continue
        if line.startswith("peer:"):
            current_peer = {"public_key": line.split(":", 1)[1].strip()}
            data["peers"].append(current_peer)
            continue
        if current_peer is not None:
            if ":" in line:
                key, _, value = line.partition(":")
                current_peer[key.strip()] = value.strip()
    return data


def _is_server_initialized(db_settings: dict) -> bool:
    """Vrai si la keypair serveur a été générée."""
    return bool(db_settings.get(VPN_SERVER_PRIVKEY))


# =============================================================================
# Schémas Pydantic
# =============================================================================

class VpnToggleRequest(BaseModel):
    enabled: bool


class VpnServerConfigUpdate(BaseModel):
    endpoint: str
    listen_port: int = 51820

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Endpoint requis (IP ou domaine du HUB)")
        return v.strip()


class VpnPeerCreate(BaseModel):
    user_id: int
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Le nom du device est requis")
        if len(v) > 100:
            raise ValueError("Le nom ne peut pas dépasser 100 caractères")
        return v


# =============================================================================
# Endpoints — Serveur
# =============================================================================

@router.get("/status")
async def get_vpn_status(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    enabled = await _get_setting(db, VPN_ENABLED_KEY) == "true"
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    initialized = bool(server_privkey)

    peer_count = 0
    connected_count = 0
    listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"

    result = await db.execute(select(VpnPeer))
    all_peers = result.scalars().all()
    peer_count = len(all_peers)

    if enabled and initialized:
        wg = _wg_status()
        if wg:
            now = datetime.now(timezone.utc)
            for p in wg.get("peers", []):
                handshake = p.get("latest handshake")
                if handshake and "minute" in handshake:
                    connected_count += 1
                elif handshake and "second" in handshake:
                    connected_count += 1

    return {
        "enabled": enabled,
        "initialized": initialized,
        "peer_count": peer_count,
        "connected_count": connected_count,
        "listen_port": int(listen_port_str),
    }


@router.put("/toggle")
async def toggle_vpn(
    payload: VpnToggleRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await _set_setting(db, VPN_ENABLED_KEY, "true" if payload.enabled else "false",
                       current_admin.id)

    if not payload.enabled:
        conf_path = _wg_conf_path()
        if conf_path.exists():
            conf_path.unlink()

    await db.commit()

    action = "VPN_ENABLED" if payload.enabled else "VPN_DISABLED"
    logger.info(f"{action} par {current_admin.username}")

    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, action, target="vpn",
            details=f"VPN {'activé' if payload.enabled else 'désactivé'}",
            notify_sse=False,
        )
        await db.commit()
    except Exception:
        pass

    return {"vpn_enabled": payload.enabled}


@router.get("/server-config")
async def get_server_config(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    server_pubkey = await _get_setting(db, VPN_SERVER_PUBKEY)
    endpoint = await _get_setting(db, VPN_ENDPOINT)
    listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"
    subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"

    return {
        "initialized": bool(server_privkey),
        "endpoint": endpoint or "",
        "listen_port": int(listen_port_str),
        "subnet": subnet,
        "server_public_key": server_pubkey or "",
    }


@router.put("/server-config")
async def update_server_config(
    payload: VpnServerConfigUpdate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    if not server_privkey:
        raise HTTPException(status_code=400, detail="Serveur non initialisé")

    await _set_setting(db, VPN_ENDPOINT, payload.endpoint, current_admin.id)
    await _set_setting(db, VPN_LISTEN_PORT, str(payload.listen_port), current_admin.id)
    await db.commit()

    subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"
    await _regenerate_and_sync(db, server_privkey, payload.listen_port, subnet)

    logger.info(f"Config serveur VPN mise à jour par {current_admin.username}")
    return {"status": "updated"}


@router.post("/init")
async def init_server(
    payload: VpnServerConfigUpdate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await _get_setting(db, VPN_SERVER_PRIVKEY)
    if existing:
        raise HTTPException(status_code=409, detail="Serveur déjà initialisé")

    private_key, public_key = _generate_keypair()

    await _set_setting(db, VPN_SERVER_PRIVKEY, private_key, current_admin.id)
    await _set_setting(db, VPN_SERVER_PUBKEY, public_key, current_admin.id)
    await _set_setting(db, VPN_ENDPOINT, payload.endpoint, current_admin.id)
    await _set_setting(db, VPN_LISTEN_PORT, str(payload.listen_port), current_admin.id)
    await _set_setting(db, VPN_SUBNET, "10.8.0.0/24", current_admin.id)
    await _set_setting(db, VPN_ENABLED_KEY, "true", current_admin.id)
    await db.commit()

    await _regenerate_and_sync(db, private_key, payload.listen_port, "10.8.0.0/24")

    logger.info(f"Serveur VPN initialisé par {current_admin.username}")

    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "VPN_SERVER_INITIALIZED", target="vpn",
            details=f"Serveur VPN initialisé: endpoint={payload.endpoint}",
            notify_sse=False,
        )
        await db.commit()
    except Exception:
        pass

    return {
        "status": "initialized",
        "server_public_key": public_key,
    }


@router.post("/generate-key")
async def generate_wireguard_key(
    current_admin: User = Depends(get_current_admin),
):
    private_key, public_key = _generate_keypair()
    return {"private_key": private_key, "public_key": public_key}


# =============================================================================
# Endpoints — Peers
# =============================================================================

@router.get("/peers")
async def list_peers(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VpnPeer).order_by(VpnPeer.id))
    peers = result.scalars().all()

    # Stats live depuis wg show
    wg_stats: dict[str, dict] = {}
    wg = _wg_status()
    if wg:
        for p in wg.get("peers", []):
            pk = p.get("public_key", "")
            wg_stats[pk] = p

    now = datetime.now(timezone.utc)
    peer_list = []
    for peer in peers:
        stats = wg_stats.get(peer.public_key, {})
        handshake_raw = stats.get("latest handshake")

        last_handshake = None
        connected = False
        if handshake_raw:
            connected = "minute" in handshake_raw or "second" in handshake_raw

        transfer_parts = stats.get("transfer", "").split() if stats.get("transfer") else []
        transfer_rx = None
        transfer_tx = None
        if len(transfer_parts) >= 3:
            transfer_rx = transfer_parts[0]
            transfer_tx = transfer_parts[2]

        peer_list.append({
            "id": peer.id,
            "user_id": peer.user_id,
            "username": peer.user.username if peer.user else "—",
            "name": peer.name,
            "vpn_ip": peer.vpn_ip.split("/")[0],
            "public_key": peer.public_key,
            "enabled": peer.enabled,
            "created_at": peer.created_at.isoformat() if peer.created_at else None,
            "last_handshake": handshake_raw,
            "transfer_rx": transfer_rx,
            "transfer_tx": transfer_tx,
            "connected": connected,
        })

    return {"peers": peer_list}


@router.post("/peers")
async def create_peer(
    payload: VpnPeerCreate,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    # Vérifier que l'utilisateur existe
    user_result = await db.execute(select(User).where(User.id == payload.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    # Vérifier que le serveur est initialisé
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    if not server_privkey:
        raise HTTPException(status_code=400, detail="Serveur VPN non initialisé")

    # Générer la keypair du peer
    private_key, public_key = _generate_keypair()

    # Allouer une IP
    subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"
    vpn_ip = await _allocate_peer_ip(db, subnet)

    # Créer le peer en DB
    peer = VpnPeer(
        user_id=payload.user_id,
        name=payload.name,
        public_key=public_key,
        private_key=private_key,
        vpn_ip=vpn_ip,
        enabled=True,
    )
    db.add(peer)
    await db.commit()
    await db.refresh(peer)

    # Régénérer le wg0.conf
    listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"
    await _regenerate_and_sync(db, server_privkey, int(listen_port_str), subnet)

    logger.info(
        f"Peer VPN créé par {current_admin.username}: "
        f"user={user.username}, name={payload.name}, ip={vpn_ip}"
    )

    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "VPN_PEER_CREATED", target="vpn",
            details=f"Peer créé: {user.username}/{payload.name} ({vpn_ip})",
            notify_sse=False,
        )
        await db.commit()
    except Exception:
        pass

    return {
        "id": peer.id,
        "name": peer.name,
        "vpn_ip": peer.vpn_ip,
        "public_key": peer.public_key,
    }


@router.delete("/peers/{peer_id}")
async def delete_peer(
    peer_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VpnPeer).where(VpnPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer introuvable")

    username = peer.user.username if peer.user else "—"
    peer_name = peer.name

    await db.delete(peer)
    await db.commit()

    # Régénérer le wg0.conf
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    if server_privkey:
        listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"
        subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"
        await _regenerate_and_sync(db, server_privkey, int(listen_port_str), subnet)

    logger.info(f"Peer VPN supprimé par {current_admin.username}: {username}/{peer_name}")

    return {"status": "deleted"}


@router.put("/peers/{peer_id}/toggle")
async def toggle_peer(
    peer_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VpnPeer).where(VpnPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer introuvable")

    peer.enabled = not peer.enabled
    peer.updated_at = utcnow()
    await db.commit()

    # Régénérer le wg0.conf
    server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
    if server_privkey:
        listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"
        subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"
        await _regenerate_and_sync(db, server_privkey, int(listen_port_str), subnet)

    return {"id": peer.id, "enabled": peer.enabled}


@router.get("/peers/{peer_id}/config")
async def download_peer_config(
    peer_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VpnPeer).where(VpnPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer introuvable")

    server_pubkey = await _get_setting(db, VPN_SERVER_PUBKEY)
    endpoint = await _get_setting(db, VPN_ENDPOINT)
    listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"

    if not server_pubkey or not endpoint:
        raise HTTPException(status_code=400, detail="Serveur non initialisé")

    conf = _generate_client_conf(peer, server_pubkey, endpoint, int(listen_port_str))

    safe_name = peer.name.replace(" ", "_").lower()
    filename = f"hub-{peer.user.username}-{safe_name}.conf"

    return StreamingResponse(
        io.BytesIO(conf.encode()),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/peers/{peer_id}/qrcode")
async def get_peer_qrcode(
    peer_id: int,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(VpnPeer).where(VpnPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer introuvable")

    server_pubkey = await _get_setting(db, VPN_SERVER_PUBKEY)
    endpoint = await _get_setting(db, VPN_ENDPOINT)
    listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"

    if not server_pubkey or not endpoint:
        raise HTTPException(status_code=400, detail="Serveur non initialisé")

    conf = _generate_client_conf(peer, server_pubkey, endpoint, int(listen_port_str))

    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(conf.encode(), image_factory=factory)

    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/svg+xml")
