"""
Routes PKI - Gestion des Certificats SSL/TLS
============================================

API REST pour la gestion de la PKI locale du Hub.
"""

import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Optional

# Ajouter le dossier /code pour importer pki.py (Docker)
# En local, le dossier parent du projet contient pki.py
if Path("/code/pki.py").exists():
    sys.path.insert(0, "/code")
else:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from pki import (
    load_env_config, 
    get_pki_status_json, 
    init_ca, 
    create_server_certificate,
    revoke_certificate,
    renew_certificate,
    load_registry,
    CA_DIR,
    SERVER_DIR,
    CERTS_DIR,
)

from app.auth import get_current_admin

router = APIRouter(prefix="/api/pki", tags=["PKI"])


# ===== SCHEMAS =====
class PKIStatusResponse(BaseModel):
    """Statut complet de la PKI"""
    initialized: bool
    ca: Optional[dict] = None
    certificates: list[dict] = []
    config: dict


class InitCARequest(BaseModel):
    """Requête d'initialisation de la CA"""
    force: bool = Field(default=False, description="Réinitialiser même si existe")


class CreateCertRequest(BaseModel):
    """Requête de création de certificat"""
    name: str = Field(..., description="Nom du certificat", min_length=1, max_length=50)
    domains: list[str] = Field(default=[], description="Domaines DNS")
    ips: list[str] = Field(default=[], description="Adresses IP")
    force: bool = Field(default=False, description="Écraser si existe")


class CertActionRequest(BaseModel):
    """Requête pour action sur certificat existant"""
    name: str = Field(..., description="Nom du certificat")


class MessageResponse(BaseModel):
    """Réponse simple avec message"""
    success: bool
    message: str


# ===== ENDPOINTS =====

@router.get("/status", response_model=PKIStatusResponse)
async def get_pki_status(admin=Depends(get_current_admin)):
    """
    Récupère l'état complet de la PKI.
    
    Retourne:
    - Si la CA est initialisée
    - Informations sur la CA (nom, expiration, etc.)
    - Liste des certificats avec leur statut
    - Configuration actuelle
    """
    return get_pki_status_json()


@router.post("/init", response_model=MessageResponse)
async def initialize_ca(request: InitCARequest, admin=Depends(get_current_admin)):
    """
    Initialise l'Autorité de Certification (CA).
    
    Cette opération crée :
    - Une clé privée RSA 4096 bits pour la CA
    - Un certificat racine auto-signé
    
    ⚠️ Ne réinitialisez pas si des certificats sont déjà utilisés !
    """
    config = load_env_config()
    
    try:
        success = init_ca(config, force=request.force)
        if success:
            return {"success": True, "message": "CA initialisée avec succès"}
        else:
            return {"success": False, "message": "CA déjà existante. Utilisez force=true pour réinitialiser."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/certificates", response_model=MessageResponse)
async def create_certificate(request: CreateCertRequest, admin=Depends(get_current_admin)):
    """
    Crée un nouveau certificat serveur signé par la CA.
    
    Le certificat inclura automatiquement :
    - Les domaines spécifiés
    - Les IPs spécifiées
    - L'IP du serveur (auto-détectée)
    - localhost et 127.0.0.1
    """
    config = load_env_config()
    
    # Utiliser les domaines par défaut si non spécifiés
    domains = request.domains or config['cert_domains']
    ips = request.ips or config['cert_ips']
    
    try:
        success = create_server_certificate(
            name=request.name,
            domains=domains,
            ips=ips,
            config=config,
            force=request.force
        )
        if success:
            return {"success": True, "message": f"Certificat '{request.name}' créé avec succès"}
        else:
            return {"success": False, "message": f"Certificat '{request.name}' existe déjà. Utilisez force=true pour le remplacer."}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/certificates")
async def list_certificates(admin=Depends(get_current_admin)):
    """Liste tous les certificats"""
    registry = load_registry()
    return {
        "certificates": registry.get('certificates', {}),
        "revoked": registry.get('revoked', [])
    }


@router.post("/certificates/{name}/revoke", response_model=MessageResponse)
async def revoke_cert(name: str, admin=Depends(get_current_admin)):
    """Révoque un certificat"""
    try:
        success = revoke_certificate(name)
        if success:
            return {"success": True, "message": f"Certificat '{name}' révoqué"}
        else:
            return {"success": False, "message": f"Certificat '{name}' non trouvé"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/certificates/{name}/renew", response_model=MessageResponse)
async def renew_cert(name: str, admin=Depends(get_current_admin)):
    """Renouvelle un certificat existant"""
    config = load_env_config()
    
    try:
        success = renew_certificate(name, config)
        if success:
            return {"success": True, "message": f"Certificat '{name}' renouvelé"}
        else:
            return {"success": False, "message": f"Certificat '{name}' non trouvé"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ca/download")
async def download_ca(format: str = "pem", admin=Depends(get_current_admin)):
    """
    Télécharge le certificat CA pour installation sur les clients.
    
    Formats disponibles :
    - pem : Linux/Mac/Firefox
    - der : Windows
    """
    ca_cert_path = CA_DIR / "ca.crt"
    
    if not ca_cert_path.exists():
        raise HTTPException(status_code=404, detail="CA non initialisée")
    
    registry = load_registry()
    ca_name = registry.get('ca', {}).get('common_name', 'Hub-CA').replace(' ', '-')
    
    if format == "der":
        # Convertir en DER
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        
        with open(ca_cert_path, 'rb') as f:
            ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        
        der_data = ca_cert.public_bytes(serialization.Encoding.DER)
        
        return Response(
            content=der_data,
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": f"attachment; filename={ca_name}.crt"}
        )
    else:
        return FileResponse(
            ca_cert_path,
            media_type="application/x-pem-file",
            filename=f"{ca_name}.pem"
        )


@router.get("/certificates/{name}/download")
async def download_certificate(name: str, file: str = "cert", admin=Depends(get_current_admin)):
    """
    Télécharge les fichiers d'un certificat.
    
    Fichiers disponibles :
    - cert : Certificat (.crt)
    - key : Clé privée (.key)
    - fullchain : Certificat + CA (.crt)
    """
    cert_dir = SERVER_DIR / name
    
    if not cert_dir.exists():
        raise HTTPException(status_code=404, detail=f"Certificat '{name}' non trouvé")
    
    if file == "key":
        file_path = cert_dir / f"{name}.key"
        media_type = "application/x-pem-file"
        filename = f"{name}.key"
    elif file == "fullchain":
        file_path = cert_dir / f"{name}-fullchain.crt"
        media_type = "application/x-x509-ca-cert"
        filename = f"{name}-fullchain.crt"
    else:
        file_path = cert_dir / f"{name}.crt"
        media_type = "application/x-x509-ca-cert"
        filename = f"{name}.crt"
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier non trouvé")
    
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.get("/config")
async def get_pki_config(admin=Depends(get_current_admin)):
    """Récupère la configuration PKI actuelle"""
    return load_env_config()


@router.get("/install-instructions")
async def get_install_instructions(os: str = "linux"):
    """
    Récupère les instructions d'installation de la CA.
    
    Systèmes supportés : linux, windows, mac, firefox
    """
    registry = load_registry()
    ca_name = registry.get('ca', {}).get('common_name', 'Hub-CA').replace(' ', '-')
    
    instructions = {
        "linux": {
            "title": "Installation Linux (Ubuntu/Debian)",
            "steps": [
                f"1. Téléchargez le certificat CA depuis /api/pki/ca/download",
                f"2. sudo cp {ca_name}.pem /usr/local/share/ca-certificates/{ca_name}.crt",
                f"3. sudo update-ca-certificates",
                f"4. Redémarrez votre navigateur"
            ]
        },
        "rhel": {
            "title": "Installation Linux (RHEL/CentOS/Fedora)",
            "steps": [
                f"1. Téléchargez le certificat CA depuis /api/pki/ca/download",
                f"2. sudo cp {ca_name}.pem /etc/pki/ca-trust/source/anchors/{ca_name}.pem",
                f"3. sudo update-ca-trust",
                f"4. Redémarrez votre navigateur"
            ]
        },
        "windows": {
            "title": "Installation Windows",
            "steps": [
                f"1. Téléchargez le certificat CA depuis /api/pki/ca/download?format=der",
                f"2. Double-cliquez sur {ca_name}.crt",
                f"3. Cliquez sur 'Installer le certificat...'",
                f"4. Sélectionnez 'Ordinateur local' → Suivant",
                f"5. Sélectionnez 'Placer tous les certificats dans le magasin suivant'",
                f"6. Parcourir → 'Autorités de certification racines de confiance'",
                f"7. Terminer et redémarrer le navigateur"
            ]
        },
        "mac": {
            "title": "Installation Mac",
            "steps": [
                f"1. Téléchargez le certificat CA depuis /api/pki/ca/download",
                f"2. Double-cliquez sur {ca_name}.pem",
                f"3. Ajoutez-le au trousseau 'Système'",
                f"4. Ouvrez 'Trousseau d'accès'",
                f"5. Trouvez le certificat, double-cliquez",
                f"6. Développez 'Confiance' → 'Toujours faire confiance'",
                f"7. Fermez et entrez votre mot de passe"
            ]
        },
        "firefox": {
            "title": "Installation Firefox (tous OS)",
            "steps": [
                f"1. Téléchargez le certificat CA depuis /api/pki/ca/download",
                f"2. Ouvrez Firefox → Préférences → Vie privée et sécurité",
                f"3. Section 'Certificats' → 'Afficher les certificats'",
                f"4. Onglet 'Autorités' → 'Importer'",
                f"5. Sélectionnez {ca_name}.pem",
                f"6. Cochez 'Faire confiance pour identifier les sites web'",
                f"7. OK et redémarrez Firefox"
            ]
        }
    }
    
    if os in instructions:
        return instructions[os]
    else:
        return instructions
