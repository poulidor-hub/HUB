# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Chantier B — API /api/routing/* (admin) : état, zone, dns_name, services tiers.

Publication automatique : toute mutation réussie appelle regenerate() (après son
commit métier) — aucun bouton « propager ».
"""

import socket
import struct

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.config import get_settings
from app.database import get_db
from app.models import AppSetting, Application, PortAllocation, RoutedService
from app.routing_service import (
    collect_entries,
    regenerate,
    validate_dns_name,
    validate_zone,
)

router = APIRouter(prefix="/api/routing", tags=["routing"])

_DNS_FQDN_RE = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"


# ---------------------------------------------------------------- helpers


def _fqdn(name: str, zone: str) -> str:
    """Nom court de la zone → FQDN ; FQDN déjà complet → inchangé."""
    name = (name or "").strip().lower().rstrip(".")
    if zone and (name == zone or name.endswith(f".{zone}")):
        return name
    return f"{name}.{zone}" if zone else name


async def _get_zone(db: AsyncSession) -> str:
    row = await db.get(AppSetting, "hub_dns_zone")
    return (row.value if row else "") or ""


async def _set_zone(db: AsyncSession, zone: str) -> None:
    row = await db.get(AppSetting, "hub_dns_zone")
    if row is None:
        row = AppSetting(key="hub_dns_zone", value=zone)
        db.add(row)
    else:
        row.value = zone
    await db.commit()


def _resolver_status() -> bool:
    """Le conteneur hub-dns tourne-t-il ? (best-effort, False si Docker muet)."""
    try:
        from app.docker_scanner import docker_scanner

        for container in docker_scanner.client.containers.list(all=True):
            if getattr(container, "name", "") == "hub-dns":
                return container.status == "running"
    except Exception:
        pass
    return False


def _wildcard_info(zone: str) -> dict | None:
    """Infos du cert wildcard (lecture seule — jamais d'émission en GET)."""
    if not zone:
        return None
    try:
        import pki
        from cryptography import x509

        crt_path, _ = pki._server_paths("hub-dns-wildcard")
        if not crt_path.exists():
            return None
        cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
        nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before
        na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        return {
            "cn": cert.subject.get_attributes_for_oid(
                x509.NameOID.COMMON_NAME)[0].value,
            "issued_at": nb.isoformat(),
            "expires_at": na.isoformat(),
        }
    except Exception:
        return None


def _resolve_local(fqdn: str, server: str, timeout: float = 2.0) -> str | None:
    """Requête DNS A brute (UDP) vers le résolveur du hub — diagnostic vue admin."""
    import random

    tid = random.randrange(65536)
    query = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for part in fqdn.split("."):
        query += bytes([len(part)]) + part.encode()
    query += b"\x00" + struct.pack(">HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query, (server, 53))
        data, _ = sock.recvfrom(512)
        ancount = struct.unpack(">H", data[6:8])[0]
        idx = 12
        while data[idx] != 0:
            idx += data[idx] + 1
        idx += 5  # 0x00 terminal + QTYPE + QCLASS
        for _ in range(ancount):
            if data[idx] & 0xC0:  # compression
                idx += 2
            else:
                while data[idx] != 0:
                    idx += data[idx] + 1
                idx += 1
            rtype, _class, _ttl, rdlen = struct.unpack(">HHIH", data[idx:idx + 10])
            idx += 10
            if rtype == 1 and rdlen == 4:
                return ".".join(str(b) for b in data[idx:idx + 4])
            idx += rdlen
        return None
    except (OSError, struct.error, IndexError):
        return None
    finally:
        sock.close()


async def _assert_dns_name_free(db: AsyncSession, zone: str, fqdn: str,
                                exclude_app_slug: str | None = None,
                                exclude_service_id: int | None = None) -> None:
    """Wrapper HTTP : ValueError du helper central → 422."""
    try:
        from app.routing_service import assert_dns_name_available
        await assert_dns_name_available(db, dns_name=fqdn, zone=zone,
                                  exclude_app_slug=exclude_app_slug,
                                  exclude_service_id=exclude_service_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ---------------------------------------------------------------- schemas


class ZoneIn(BaseModel):
    zone: str


class DnsNameIn(BaseModel):
    dns_name: str


class ServiceIn(BaseModel):
    name: str
    dns_name: str
    target_url: str
    is_enabled: bool = True


# ---------------------------------------------------------------- endpoints


@router.get("/state")
async def state(db: AsyncSession = Depends(get_db),
                _admin=Depends(get_current_admin)):
    zone = await _get_zone(db)
    hub_host = get_settings().get_hub_host()
    entries = await collect_entries(db, hub_host, zone)
    by_ref = {e["ref"]: e for e in entries if e["kind"] == "app"}
    allocations = {
        a.app_slug: a
        for a in (await db.execute(select(PortAllocation))).scalars().all()
    }
    apps_payload = []
    apps = (await db.execute(select(Application))).scalars().all()
    for app in apps:
        entry = by_ref.get(app.slug, {})
        alloc = allocations.get(app.slug)
        apps_payload.append({
            "slug": app.slug,
            "name": app.name,
            "dns_name": app.dns_name,
            "fqdn": entry.get("fqdn"),
            "published": bool(zone) and bool(app.dns_name),
            "is_workshop": app.is_workshop,
            "is_remote": app.is_remote,
            "is_active": app.is_active,
            # fallback ports : lecture seule (transition) — PortAllocation si connue
            "fallback": {
                "api": alloc.api_port if alloc else None,
                "front": alloc.frontend_port if alloc else None,
                "https": alloc.frontend_https_port if alloc else None,
            },
        })
    services_payload = [{
        "id": s.id, "name": s.name, "dns_name": s.dns_name,
        "target_url": s.target_url, "is_enabled": s.is_enabled,
    } for s in (await db.execute(select(RoutedService))).scalars().all()]
    err = await db.get(AppSetting, "routing_last_error")
    return {
        "configured": bool(zone),
        "zone": zone,
        "hub_host": hub_host,
        "resolver": {"active": _resolver_status(),
                     "count": sum(1 for e in entries if e.get("in_zone"))},
        "wildcard": _wildcard_info(zone),
        "apps": apps_payload,
        "services": services_payload,
        "last_error": (err.value if err else "") or "",
    }


@router.put("/zone")
async def put_zone(body: ZoneIn, db: AsyncSession = Depends(get_db),
                   _admin=Depends(get_current_admin)):
    try:
        zone = validate_zone(body.zone)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await _set_zone(db, zone)
    await regenerate()
    return {"zone": zone}


@router.put("/apps/{slug}")
async def put_app_dns_name(slug: str, body: DnsNameIn,
                           db: AsyncSession = Depends(get_db),
                           _admin=Depends(get_current_admin)):
    app = (await db.execute(
        select(Application).where(Application.slug == slug)
    )).scalar_one_or_none()
    if app is None:
        raise HTTPException(404, f"App inconnue : {slug}")
    try:
        dns_name = validate_dns_name(body.dns_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    zone = await _get_zone(db)
    fqdn = _fqdn(dns_name, zone)
    await _assert_dns_name_free(db, zone, fqdn, exclude_app_slug=slug)
    app.dns_name = dns_name
    await db.commit()
    await regenerate()
    return {"slug": slug, "dns_name": dns_name, "fqdn": fqdn}


async def _service_from_body(db: AsyncSession, zone: str, body: ServiceIn,
                             exclude_id: int | None = None) -> RoutedService:
    import re

    fqdn = body.dns_name.strip().lower().rstrip(".")
    if not re.match(_DNS_FQDN_RE, fqdn):
        raise HTTPException(422, f"FQDN invalide : {body.dns_name}")
    if not body.target_url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "target_url doit commencer par http:// ou https://")
    await _assert_dns_name_free(db, zone, fqdn, exclude_service_id=exclude_id)
    return RoutedService(name=body.name, dns_name=fqdn,
                         target_url=body.target_url, is_enabled=body.is_enabled)


@router.post("/services")
async def post_service(body: ServiceIn, db: AsyncSession = Depends(get_db),
                       _admin=Depends(get_current_admin)):
    zone = await _get_zone(db)
    svc = await _service_from_body(db, zone, body)
    db.add(svc)
    await db.commit()
    await db.refresh(svc)
    await regenerate()
    return {"id": svc.id, "name": svc.name, "dns_name": svc.dns_name,
            "target_url": svc.target_url, "is_enabled": svc.is_enabled}


@router.put("/services/{service_id}")
async def put_service(service_id: int, body: ServiceIn,
                      db: AsyncSession = Depends(get_db),
                      _admin=Depends(get_current_admin)):
    svc = await db.get(RoutedService, service_id)
    if svc is None:
        raise HTTPException(404, f"Service inconnu : {service_id}")
    zone = await _get_zone(db)
    updated = await _service_from_body(db, zone, body, exclude_id=service_id)
    svc.name, svc.dns_name = updated.name, updated.dns_name
    svc.target_url, svc.is_enabled = updated.target_url, updated.is_enabled
    await db.commit()
    await regenerate()
    return {"id": svc.id, "name": svc.name, "dns_name": svc.dns_name,
            "target_url": svc.target_url, "is_enabled": svc.is_enabled}


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, db: AsyncSession = Depends(get_db),
                         _admin=Depends(get_current_admin)):
    svc = await db.get(RoutedService, service_id)
    if svc is None:
        raise HTTPException(404, f"Service inconnu : {service_id}")
    await db.delete(svc)
    await db.commit()
    await regenerate()
    return {"deleted": service_id}


@router.get("/test-resolve/{dns_name}")
async def test_resolve(dns_name: str, db: AsyncSession = Depends(get_db),
                       _admin=Depends(get_current_admin)):
    zone = await _get_zone(db)
    fqdn = _fqdn(dns_name, zone)
    hub_host = get_settings().get_hub_host()
    ip = _resolve_local(fqdn, hub_host) if (zone and hub_host) else None
    return {"fqdn": fqdn, "resolved": bool(ip), "ip": ip}
