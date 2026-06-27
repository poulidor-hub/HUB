"""
Routes VPN WireGuard — configuration et gestion du client VPN.

Endpoints admin :
- POST /api/vpn/deploy : Déployer la configuration WireGuard
- GET /api/vpn/status : État actuel de la connexion VPN
- PUT /api/vpn/toggle : Activer/désactiver le VPN
- GET /api/vpn/config : Récupérer la configuration actuelle
- POST /api/vpn/generate-key : Générer une paire de clés WireGuard
"""

import hashlib
import ipaddress
import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import AppSetting, User, utcnow
from app.auth import get_current_admin
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vpn", tags=["VPN"])

VPN_ENABLED_KEY = "vpn_enabled"
VPN_CONFIG_HASH_KEY = "vpn_config_hash"

_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def _is_valid_wg_key(s: str) -> bool:
    if not s or len(s) != 44:
        return False
    return bool(_WG_KEY_RE.match(s))


def _is_valid_cidr(s: str) -> bool:
    try:
        ipaddress.IPv4Network(s, strict=False)
        return "/" in s
    except (ValueError, TypeError):
        return False


def _wg_conf_path() -> Path:
    config_dir = get_settings().vpn_config_dir
    return Path(config_dir) / "wg_confs" / "wg0.conf"


def _wg_conf_dir() -> Path:
    p = _wg_conf_path().parent
    p.mkdir(parents=True, exist_ok=True)
    return p


def _generate_wg_conf(
    private_key: str,
    address: str,
    dns: str | None,
    public_key: str,
    endpoint: str,
    allowed_ips: str,
    preshared_key: str | None = None,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {address}",
    ]
    if dns:
        lines.append(f"DNS = {dns}")

    lines.append("")
    lines.append("[Peer]")
    lines.append(f"PublicKey = {public_key}")
    lines.append(f"Endpoint = {endpoint}")
    lines.append(f"AllowedIPs = {allowed_ips}")
    if preshared_key:
        lines.append(f"PresharedKey = {preshared_key}")

    return "\n".join(lines) + "\n"


def _parse_wg_conf(content: str) -> dict:
    result: dict = {}
    current_section = None
    for line in content.strip().split("\n"):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1]
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if current_section == "Interface" and key != "PrivateKey":
                result[key.lower()] = value
            elif current_section == "Peer":
                result[key.lower()] = value
    return result


def _wg_status() -> dict | None:
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
    data: dict = {"peers": []}
    current_peer = None
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("interface:"):
            continue
        if line.startswith("peer:"):
            current_peer = {}
            data["peers"].append(current_peer)
            continue
        if current_peer is not None:
            if ":" in line:
                key, _, value = line.partition(":")
                current_peer[key.strip()] = value.strip()
        else:
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip()
    return data


# =============================================================================
# Schemas Pydantic
# =============================================================================

class VpnDeployRequest(BaseModel):
    enabled: bool
    private_key: str
    address: str
    dns: str | None = None
    public_key: str
    endpoint: str
    allowed_ips: str = "0.0.0.0/0"
    preshared_key: str | None = None

    @field_validator("private_key", "public_key")
    @classmethod
    def validate_wg_key(cls, v: str) -> str:
        if not _is_valid_wg_key(v):
            raise ValueError("Clé WireGuard invalide (format base64 44 caractères attendu)")
        return v

    @field_validator("preshared_key")
    @classmethod
    def validate_preshared_key(cls, v: str | None) -> str | None:
        if v is not None and not _is_valid_wg_key(v):
            raise ValueError("PresharedKey invalide (format base64 44 caractères attendu)")
        return v

    @field_validator("address")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not _is_valid_cidr(v):
            raise ValueError("Adresse CIDR invalide (ex: 10.8.0.5/24)")
        return v

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v or ":" not in v:
            raise ValueError("Endpoint invalide (format host:port attendu)")
        host, _, port_str = v.partition(":")
        port_str = port_str.strip()
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            raise ValueError(f"Port invalide: {port_str}")
        return v


class VpnToggleRequest(BaseModel):
    enabled: bool


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/deploy")
async def deploy_vpn_config(
    payload: VpnDeployRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    _wg_conf_dir()

    conf_content = _generate_wg_conf(
        private_key=payload.private_key,
        address=payload.address,
        dns=payload.dns,
        public_key=payload.public_key,
        endpoint=payload.endpoint,
        allowed_ips=payload.allowed_ips,
        preshared_key=payload.preshared_key,
    )
    conf_path = _wg_conf_path()
    conf_path.write_text(conf_content)

    config_hash = hashlib.sha256(conf_content.encode()).hexdigest()

    for key, value in [
        (VPN_ENABLED_KEY, "true" if payload.enabled else "false"),
        (VPN_CONFIG_HASH_KEY, config_hash),
    ]:
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if not setting:
            setting = AppSetting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
            setting.updated_at = utcnow()
            setting.updated_by_id = current_admin.id

    await db.commit()

    logger.info(
        f"Configuration VPN déployée par {current_admin.username} "
        f"(endpoint={payload.endpoint}, address={payload.address})"
    )

    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, "VPN_CONFIG_UPDATED",
            target="vpn",
            details=f"Config VPN déployée: endpoint={payload.endpoint}, address={payload.address}",
            notify_sse=False,
        )
        await db.commit()
    except Exception:
        pass

    return {"status": "deployed"}


@router.get("/status")
async def get_vpn_status(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == VPN_ENABLED_KEY)
    )
    setting = result.scalar_one_or_none()
    enabled = setting is not None and setting.value == "true"

    response = {"enabled": enabled, "connected": False}

    if enabled:
        wg_data = _wg_status()
        if wg_data:
            peers = wg_data.get("peers", [])
            connected = False
            vpn_ip = None
            endpoint = None
            transfer_rx = 0
            transfer_tx = 0

            for peer in peers:
                if "latest handshake" in peer:
                    connected = True
                if "endpoint" in peer and not endpoint:
                    endpoint = peer["endpoint"]
                if "transfer" in peer:
                    parts = peer["transfer"].split()
                    if len(parts) >= 2:
                        transfer_rx = parts[0]
                        transfer_tx = parts[2]

            response["connected"] = connected
            if connected:
                conf_path = _wg_conf_path()
                if conf_path.exists():
                    parsed = _parse_wg_conf(conf_path.read_text())
                    vpn_ip = parsed.get("address", "").split("/")[0] if "address" in parsed else None

            response["vpn_ip"] = vpn_ip
            response["endpoint"] = endpoint
            response["transfer_rx"] = transfer_rx
            response["transfer_tx"] = transfer_tx

    return response


@router.put("/toggle")
async def toggle_vpn(
    payload: VpnToggleRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == VPN_ENABLED_KEY)
    )
    setting = result.scalar_one_or_none()

    if not setting:
        setting = AppSetting(key=VPN_ENABLED_KEY, value="false")
        db.add(setting)

    new_value = "true" if payload.enabled else "false"

    if not payload.enabled:
        conf_path = _wg_conf_path()
        if conf_path.exists():
            conf_path.unlink()

    setting.value = new_value
    setting.updated_at = utcnow()
    setting.updated_by_id = current_admin.id
    await db.commit()

    action = "VPN_ENABLED" if payload.enabled else "VPN_DISABLED"
    logger.info(f"{action} par {current_admin.username}")

    try:
        from app.routes.audit import log_action
        await log_action(
            db, current_admin.id, action,
            target="vpn",
            details=f"VPN {'activé' if payload.enabled else 'désactivé'}",
            notify_sse=False,
        )
        await db.commit()
    except Exception:
        pass

    return {"vpn_enabled": payload.enabled}


@router.get("/config")
async def get_vpn_config(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == VPN_ENABLED_KEY)
    )
    enabled_setting = result.scalar_one_or_none()
    enabled = enabled_setting is not None and enabled_setting.value == "true"

    conf_path = _wg_conf_path()
    if not conf_path.exists():
        return {"enabled": enabled, "configured": False}

    content = conf_path.read_text()
    parsed = _parse_wg_conf(content)

    private_key_line = None
    for line in content.split("\n"):
        if line.strip().startswith("PrivateKey"):
            private_key_line = line.strip()
            break

    private_key_hash = ""
    if private_key_line:
        _, _, pk = private_key_line.partition("=")
        private_key_hash = hashlib.sha256(pk.strip().encode()).hexdigest()

    return {
        "enabled": enabled,
        "configured": True,
        "address": parsed.get("address"),
        "dns": parsed.get("dns"),
        "public_key": parsed.get("publickey"),
        "endpoint": parsed.get("endpoint"),
        "allowed_ips": parsed.get("allowedips"),
        "private_key_hash": private_key_hash,
    }


@router.post("/generate-key")
async def generate_wireguard_key(
    current_admin: User = Depends(get_current_admin),
):
    try:
        result = subprocess.run(
            ["wg", "genkey"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erreur lors de la génération de la clé privée"
            )
        private_key = result.stdout.strip()

        result2 = subprocess.run(
            ["wg", "pubkey"],
            input=private_key,
            capture_output=True, text=True, timeout=5
        )
        if result2.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erreur lors de la génération de la clé publique"
            )
        public_key = result2.stdout.strip()

        return {"private_key": private_key, "public_key": public_key}
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WireGuard (wg) n'est pas installé sur le serveur"
        )
