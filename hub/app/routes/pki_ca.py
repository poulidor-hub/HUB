"""Téléchargement de la CA racine du HUB — GET /api/pki/ca.crt et GET /api/pki/ca/info.

Fonctionnalité 2026-10-06 : bouton « Certificat » sur la page Hub. Un utilisateur
télécharge la CA racine et l'installe dans le magasin de confiance de son poste
pour supprimer les avertissements « certificat auto-signé » à l'ouverture du Hub
et des apps HTTPS (toutes signées par cette même CA, cf. cert_sync).

Endpoints SANS authentification : une CA est publique par nature (aucun secret —
la sécurité repose exclusivement sur `ca.key`, jamais servi), et une app distante
doit pouvoir récupérer la chaîne de confiance AVANT de détenir des credentials
(test_remote_app_m2m.py::test_1_download_ca_certificate appelle sans header).
"""
from datetime import timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

import pki  # hub/pki.py — à la racine du contexte /code

router = APIRouter(prefix="/api/pki", tags=["pki"])

CA_DOWNLOAD_FILENAME = "POULIDOR-Hub-Root-CA.crt"


def _ca_path() -> Path:
    """Chemin en dur de la CA racine — délègue à la PKI (patchable en tests)."""
    return pki._certs_dir() / "ca" / "ca.crt"


def _load_ca_cert() -> x509.Certificate:
    """Charge la CA racine ; 404 explicite si la PKI n'est pas initialisée."""
    path = _ca_path()
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="CA non initialisée sur le HUB (certs/ca/ca.crt absent)",
        )
    return x509.load_pem_x509_certificate(path.read_bytes())


class CaInfoResponse(BaseModel):
    subject: str
    not_before: str
    not_after: str
    fingerprint_sha256: str


@router.get("/ca.crt")
async def download_ca_crt():
    """Sert la CA racine (PEM) en téléchargement — public, sans auth."""
    _load_ca_cert()  # 404 explicite avant la construction du FileResponse
    return FileResponse(
        path=_ca_path(),
        media_type="application/x-x509-ca-cert",
        filename=CA_DOWNLOAD_FILENAME,
    )


@router.get("/ca/info", response_model=CaInfoResponse)
async def ca_info():
    """Métadonnées de la CA (sujet, validité, empreinte SHA-256) — pour la modale."""
    cert = _load_ca_cert()
    not_before = getattr(cert, "not_valid_before_utc", None)
    not_after = getattr(cert, "not_valid_after_utc", None)
    if not_before is None:  # cryptography < 42 : naïf UTC
        not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
    if not_after is None:
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return CaInfoResponse(
        subject=cert.subject.rfc4514_string(),
        not_before=not_before.isoformat(),
        not_after=not_after.isoformat(),
        fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
    )
