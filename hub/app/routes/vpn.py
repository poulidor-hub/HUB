# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

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

import ipaddress
import io
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import qrcode
import qrcode.image.svg

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

# Conteneur Docker hébergeant l'interface wg0 (linuxserver/wireguard).
_WG_CONTAINER = "hub-wireguard"

# Un peer WireGuard est considéré comme "connecté" si son dernier handshake
# date de moins de ce seuil. WireGuard lui-même déclare un peer inactif au-delà
# de ~120-180s (PersistentKeepalive=25 côté client garantit un handshake
# régulier environ toutes les 25 s tant que le tunnel est monté).
# ATTENTION : la sortie `wg show` est en anglais (langue du conteneur).
_PEER_CONNECTED_THRESHOLD_SECONDS = 180


def _docker_exec(container: str, cmd: list[str]) -> tuple[int, str]:
    """Exécute une commande dans un conteneur via l'API Docker (socket unix).

    Utilise la lib Python `docker` (déjà utilisée par docker_scanner.py) plutôt
    que le CLI `docker` qui n'est PAS installé dans l'image hub-api. Le socket
    Docker est monté en RO via /var/run/docker.sock:/var/run/docker.sock:ro.

    Retourne (exit_code, stdout+stderr combiné).
    Lève une exception si l'API Docker ou le conteneur est indisponible.
    """
    import docker  # lazy : dépendance optionnelle si VPN non utilisé
    client = docker.from_env()
    result = client.containers.get(container).exec_run(cmd, demux=False)
    output = result.output
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return result.exit_code if result.exit_code is not None else -1, output


def _docker_restart(container: str) -> bool:
    """Redémarre un conteneur via l'API Docker. Retourne True si OK."""
    import docker
    client = docker.from_env()
    client.containers.get(container).restart(timeout=30)
    return True


def _parse_handshake_age(raw: str | None) -> int | None:
    """Convertit la chaîne 'latest handshake' de `wg show` en secondes.

    `wg show` formate les durées en anglais, ex :
      - '20 seconds ago'                       → 20
      - '5 minutes ago'                        → 300
      - '2 minutes, 15 seconds ago'            → 135
      - '1 hour, 5 minutes ago'                → 3900
      - '1 day, 3 hours, 2 minutes ago'        → 97320

    Retourne None si la chaîne est vide ou non parsable.
    """
    if not raw:
        return None
    text = raw.lower()
    total = 0
    found = False
    # On cherche toutes les paires '<nombre> <unité>' dans la chaîne.
    for match in re.finditer(r"(\d+)\s*(second|minute|hour|day)s?", text):
        found = True
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "second":
            total += value
        elif unit == "minute":
            total += value * 60
        elif unit == "hour":
            total += value * 3600
        elif unit == "day":
            total += value * 86400
    return total if found else None


def _peer_connected(handshake_raw: str | None) -> bool:
    """Détermine si un peer est connecté (handshake récent < seuil)."""
    age = _parse_handshake_age(handshake_raw)
    return age is not None and age <= _PEER_CONNECTED_THRESHOLD_SECONDS


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
    subnet: str = "10.8.0.0/24",
    dns_line: str = "",
) -> str:
    """Génère le .conf client pour un peer (téléchargement par l'utilisateur).

    AllowedIPs dynamiques (aucune IP en dur dans le code) :
      - vpn_server_ip/32 : le HUB via le tunnel (dérivé du sous-réseau VPN)
      - hub_host/32       : TOUTES les apps du host, tous ports (présentes et
                            futures) — depuis HUB_HOST/.env via get_hub_host()
    Chantier B : si la zone de routage est configurée, dns_line (« DNS = ip »)
    est poussé dans [Interface] — les noms <dns_name>.<zone> résolvent via le
    résolveur du hub. Sinon aucune ligne DNS : le client garde son résolveur.
    """
    peer_ip = peer.vpn_ip.split("/")[0]
    vpn_server_ip = str(ipaddress.IPv4Network(subnet, strict=False).network_address + 1)
    hub_host = get_settings().get_hub_host()
    dns_block = f"{dns_line}\n" if dns_line else ""
    return (
        "[Interface]\n"
        f"PrivateKey = {peer.private_key}\n"
        f"Address = {peer_ip}/32\n"
        f"{dns_block}"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"Endpoint = {endpoint}:{listen_port}\n"
        f"AllowedIPs = {vpn_server_ip}/32, {hub_host}/32\n"
        "PersistentKeepalive = 25\n"
    )


async def get_dns_line_for_peers(db: AsyncSession) -> str:
    """Chantier B — ligne DNS des configs client, si la zone de routage est configurée."""
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == "hub_dns_zone")
    )
    zone = (result.scalar_one_or_none() or "").strip()
    if not zone:
        return ""
    hub_host = get_settings().get_hub_host()
    return f"DNS = {hub_host}" if hub_host else ""


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


def _get_current_listen_port() -> int | None:
    """Retourne le ListenPort actuellement actif sur wg0, ou None si indisponible."""
    try:
        exit_code, output = _docker_exec(
            _WG_CONTAINER, ["wg", "show", "wg0", "listen-port"]
        )
        if exit_code == 0:
            port_str = output.strip()
            if port_str.isdigit():
                return int(port_str)
    except Exception:
        pass
    return None


def _restart_wireguard_interface() -> bool:
    """Redémarre le conteneur hub-wireguard pour appliquer un changement de
    ListenPort/Address/PrivateKey (wg syncconf ne les modifie pas à chaud)."""
    try:
        return _docker_restart(_WG_CONTAINER)
    except Exception:
        return False


async def _regenerate_and_sync(
    db: AsyncSession,
    server_privkey: str,
    listen_port: int,
    subnet: str,
) -> None:
    """Régénère le wg0.conf depuis les peers en DB et applique la config.

    - Si le ListenPort change : restart du conteneur (wg syncconf ne modifie pas
      le ListenPort à chaud — sinon le changement de port via l'UI restait sans effet).
    - Sinon : wg syncconf (léger, synchronise uniquement les peers).
    """
    result = await db.execute(
        select(VpnPeer).order_by(VpnPeer.id)
    )
    peers = list(result.scalars().all())

    conf_content = _generate_server_conf(server_privkey, listen_port, subnet, peers)

    conf_path = _wg_conf_path()
    _wg_conf_dir()
    # Création en mode 0600 : write_text+chmod laissait une fenêtre umask
    # (~0644) sur un fichier contenant la clé privée serveur (revue 05/10).
    # Fichier préexistant aux perms fantaisistes (ex. 0444) : remise à
    # niveau AVANT ouverture — le propriétaire peut toujours chmod, et
    # O_WRONLY sur un fichier sans bit d'écriture lèverait EACCES.
    if conf_path.exists():
        conf_path.chmod(0o600)
    fd = os.open(conf_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(conf_content)
    conf_path.chmod(0o600)  # fichier préexistant : O_TRUNC conserve ses perms

    # Détecter un changement de ListenPort (wg syncconf ne l'applique pas à chaud)
    current_port = _get_current_listen_port()
    if current_port is not None and current_port != listen_port:
        if _restart_wireguard_interface():
            logger.info(
                f"WireGuard redémarré (ListenPort {current_port} → {listen_port})"
            )
        else:
            logger.error(
                f"Échec restart WireGuard (port {current_port} → {listen_port}) "
                f"— la conf sera appliquée au prochain démarrage du conteneur"
            )
        return

    # Port inchangé (ou conteneur arrêté) : syncconf léger pour les peers
    try:
        _docker_exec(
            _WG_CONTAINER,
            ["bash", "-c", "wg syncconf wg0 <(wg-quick strip wg0)"],
        )
    except Exception:
        logger.warning("wg syncconf failed (container may be stopped)")


def _wg_status() -> dict | None:
    """Lit l'état du tunnel via docker exec wg show."""
    try:
        exit_code, output = _docker_exec(_WG_CONTAINER, ["wg", "show", "wg0"])
        if exit_code != 0:
            return None
        output = output.strip()
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
            for p in wg.get("peers", []):
                if _peer_connected(p.get("latest handshake")):
                    connected_count += 1

    return {
        "enabled": enabled,
        "initialized": initialized,
        "peer_count": peer_count,
        "connected_count": connected_count,
        "listen_port": int(listen_port_str),
        "conf_exists": _wg_conf_path().exists(),
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
    else:
        # Auto-réparation (bug 02/10/2026) : le toggle OFF supprime wg0.conf ;
        # si le serveur est initialisé mais le fichier absent (OFF puis ON,
        # restauration…), on le régénère depuis la DB — sinon le conteneur
        # démarre sans config (« conf=ABSENTE » côté hubctl vpn sync).
        server_privkey = await _get_setting(db, VPN_SERVER_PRIVKEY)
        if server_privkey and not _wg_conf_path().exists():
            listen_port_str = await _get_setting(db, VPN_LISTEN_PORT) or "51820"
            subnet = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"
            await _regenerate_and_sync(
                db, server_privkey, int(listen_port_str), subnet
            )
            logger.info("wg0.conf régénérée au toggle ON (fichier absent)")

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

    peer_list = []
    for peer in peers:
        stats = wg_stats.get(peer.public_key, {})
        handshake_raw = stats.get("latest handshake")
        connected = _peer_connected(handshake_raw)

        transfer_parts = stats.get("transfer", "").split() if stats.get("transfer") else []
        transfer_rx = None
        transfer_tx = None
        if len(transfer_parts) >= 6:
            # Format pretty (ex: "1.50 GiB received, 789.00 MiB sent")
            transfer_rx = f"{transfer_parts[0]} {transfer_parts[1]}"
            transfer_tx = f"{transfer_parts[3]} {transfer_parts[4]}"
        elif len(transfer_parts) == 4:
            # Format raw bytes (ex: "123456 received, 789012 sent")
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
    subnet_str = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"

    if not server_pubkey or not endpoint:
        raise HTTPException(status_code=400, detail="Serveur non initialisé")

    dns_line = await get_dns_line_for_peers(db)
    conf = _generate_client_conf(peer, server_pubkey, endpoint, int(listen_port_str), subnet_str, dns_line=dns_line)

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
    subnet_str = await _get_setting(db, VPN_SUBNET) or "10.8.0.0/24"

    if not server_pubkey or not endpoint:
        raise HTTPException(status_code=400, detail="Serveur non initialisé")

    dns_line = await get_dns_line_for_peers(db)
    conf = _generate_client_conf(peer, server_pubkey, endpoint, int(listen_port_str), subnet_str, dns_line=dns_line)

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(conf.encode(), image_factory=factory)

    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/svg+xml")
