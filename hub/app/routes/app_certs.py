"""Émission de certificats serveur pour les apps, signés par la PKI du HUB.

POST /api/apps/cert/issue — auth M2M service account uniquement.
L'app appelante reçoit son couple cert/key + la CA du HUB (chaîne de confiance).
Idempotent : même SAN → même cert ; SAN changé (ex. IP du hub modifiée) → ré-émission.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import pki  # hub/pki.py — à la racine du contexte /code
from app.auth import verify_token_not_revoked
from app.config import get_settings
from app.database import get_db
from app.models import Application, ServiceAccount

router = APIRouter(prefix="/api/apps/cert", tags=["app-certs"])

CERT_REISSUE_MARGIN_DAYS = 30


def _certs_dir() -> Path:
    """Résolution du dossier certs — délègue à la PKI (patchable en tests)."""
    return pki._certs_dir()


class CertIssueResponse(BaseModel):
    cert: str
    key: str
    ca: str
    san: list[str]
    fingerprint: str


async def _san_for_slug(db: AsyncSession, slug: str) -> list[str]:
    """SAN = domains du HUB (PKI_CERT_DOMAINS, ex. localhost pour l'accès opérateur)
    + host de l'app : propre à l'app si elle est distante, sinon host du HUB.

    Pas de colonne app_host/custom_host sur Application : pour une app distante,
    `registered_from_ip` (migration 005) est le host du serveur de l'app.
    """
    result = await db.execute(select(Application).where(Application.slug == slug))
    app = result.scalar_one_or_none()
    if app is not None and app.is_remote and app.registered_from_ip:
        host = app.registered_from_ip
    else:
        host = get_settings().get_hub_host()
    domains = [d.strip() for d in get_settings().pki_cert_domains.split(",") if d.strip()]
    seen: set[str] = set()
    san: list[str] = []
    for value in domains + [host]:
        if value and value not in seen:
            seen.add(value)
            san.append(value)
    return san


def _fingerprint(cert_pem: str) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return cert.fingerprint(hashes.SHA256()).hex()


async def _get_sa_from_m2m(request: Request, db: AsyncSession) -> ServiceAccount:
    """Résout un Bearer M2M en ServiceAccount (même pattern que apps.py::_service_account_from_m2m_token)."""
    auth = request.headers.get("Authorization", "")
    scheme, token = get_authorization_scheme_param(auth)
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token M2M requis")
    try:
        payload = await verify_token_not_revoked(token, db)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token M2M invalide ou expiré")
    if payload.get("type") != "service_account":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token M2M requis")
    client_id = payload.get("sub")
    if not client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token M2M invalide")
    result = await db.execute(
        select(ServiceAccount)
        .options(selectinload(ServiceAccount.applications))
        .where(ServiceAccount.client_id == client_id)
    )
    sa = result.scalar_one_or_none()
    if sa is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Service account inconnu")
    return sa


@router.post("/issue", response_model=CertIssueResponse)
async def issue_app_cert(request: Request, db: AsyncSession = Depends(get_db)):
    sa = await _get_sa_from_m2m(request, db)
    slug = request.headers.get("X-Slug") or (
        sa.applications[0].slug if sa.applications else None
    )
    if not slug:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service account not linked to any application")
    # Garde stricte : ce endpoint retourne la clé privée et peut écraser (force=True)
    # le cert du slug ciblé → slugs possédés uniquement, pas de mode compat.
    if slug not in {app.slug for app in sa.applications}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service account not authorized for this application")

    san = await _san_for_slug(db, slug)
    base = _certs_dir() / "server" / slug
    crt_path, key_path = base / "server.crt", base / "server.key"
    need_issue = not crt_path.exists()
    if not need_issue:
        # Idempotence : SAN du cert existant != SAN demandé → ré-émettre.
        # Cert proche de l'expiration (marge 30j) → ré-émettre aussi
        # ("si un cert valide existe déjà" : un cert expiré/dépassé n'est pas valide).
        existing = x509.load_pem_x509_certificate(crt_path.read_bytes())
        try:
            current_san = {str(gn.value) for gn in existing.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value}
        except x509.ExtensionNotFound:
            current_san = set()
        not_after = getattr(existing, "not_valid_after_utc", None)
        if not_after is None:  # cryptography < 42 : naïf UTC
            not_after = existing.not_valid_after.replace(tzinfo=timezone.utc)
        expires_soon = not_after - timedelta(days=CERT_REISSUE_MARGIN_DAYS) <= datetime.now(timezone.utc)
        need_issue = current_san != set(san) or expires_soon

    if need_issue:
        try:
            # RSA-4096 + I/O fichier : hors event loop (bloque plusieurs secondes)
            await asyncio.to_thread(pki.create_server_cert, name=slug, force=True, cn=slug, san=san)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    try:
        cert_pem = crt_path.read_text()
        key_pem = key_path.read_text()
        ca_pem = (_certs_dir() / "ca" / "ca.crt").read_text()
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Certificat ou CA introuvable sur le HUB — émission incomplète, réessayez",
        )
    return CertIssueResponse(cert=cert_pem, key=key_pem, ca=ca_pem, san=san,
                             fingerprint=_fingerprint(cert_pem))
