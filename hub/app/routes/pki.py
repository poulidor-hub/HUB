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
    (Endpoint protégé - nécessite authentification admin)
    
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


@router.get("/ca.crt")
async def get_ca_certificate_public():
    """
    Endpoint PUBLIC pour télécharger le certificat CA du Hub.
    
    Cet endpoint ne nécessite PAS d'authentification car le certificat CA
    est une information publique (comme les CA publiques sur Internet).
    
    Il permet aux applications distantes de récupérer automatiquement
    le certificat CA pour établir des connexions HTTPS sécurisées avec le Hub.
    
    Usage:
        curl http://hub:8000/api/pki/ca.crt > hub-ca.crt
    
    Returns:
        Le certificat CA au format PEM
    """
    ca_cert_path = CA_DIR / "ca.crt"
    
    if not ca_cert_path.exists():
        raise HTTPException(
            status_code=404, 
            detail="CA non initialisée. Exécutez 'python pki.py init' sur le Hub."
        )
    
    return FileResponse(
        ca_cert_path,
        media_type="application/x-pem-file",
        filename="hub-ca.crt"
    )


@router.get("/app-cert/{app_slug}")
async def get_or_create_app_certificate(
    app_slug: str,
    file: str = "cert",
    ip: Optional[str] = None
):
    """
    Endpoint PUBLIC pour obtenir le certificat d'une application.
    
    Si le certificat n'existe pas ou n'est plus valide avec la CA actuelle,
    il sera automatiquement généré/régénéré.
    
    Cet endpoint permet aux applications de télécharger automatiquement
    leur certificat SSL au démarrage, garantissant qu'elles utilisent
    toujours un certificat valide signé par la CA actuelle.
    
    Args:
        app_slug: Identifiant de l'application
        file: Type de fichier (cert, key, fullchain, bundle)
        ip: IP de l'application (utilisée pour les SANs si le cert doit être généré)
    
    Returns:
        - cert: Le certificat de l'application
        - key: La clé privée
        - fullchain: Certificat + CA
        - bundle: Archive ZIP avec cert + key + ca
    
    Note:
        La clé privée est retournée car c'est un environnement interne.
        Pour plus de sécurité, utilisez un stockage centralisé des secrets.
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    import zipfile
    import io
    
    # Vérifier que la CA existe
    ca_cert_path = CA_DIR / "ca.crt"
    if not ca_cert_path.exists():
        raise HTTPException(
            status_code=503,
            detail="CA non initialisée. Le Hub doit d'abord initialiser sa PKI."
        )
    
    cert_dir = SERVER_DIR / app_slug
    cert_path = cert_dir / f"{app_slug}.crt"
    key_path = cert_dir / f"{app_slug}.key"
    fullchain_path = cert_dir / f"{app_slug}-fullchain.crt"
    
    # Vérifier si le certificat existe et est valide avec la CA actuelle
    need_regeneration = False
    reason = ""
    
    if not cert_path.exists() or not key_path.exists():
        need_regeneration = True
        reason = "certificat inexistant"
    else:
        # Vérifier que le certificat est signé par la CA actuelle
        try:
            with open(cert_path, 'rb') as f:
                app_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            with open(ca_cert_path, 'rb') as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            
            # Comparer l'issuer du certificat avec le subject de la CA
            if app_cert.issuer != ca_cert.subject:
                need_regeneration = True
                reason = "certificat signé par une ancienne CA"
            # Vérifier si le certificat n'est pas expiré
            from datetime import datetime, timezone
            if app_cert.not_valid_after_utc < datetime.now(timezone.utc):
                need_regeneration = True
                reason = "certificat expiré"
            
            # Vérifier si l'IP demandée est dans les SANs du certificat
            if ip and not need_regeneration:
                try:
                    san_ext = app_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                    san_ips = [
                        str(name.value) for name in san_ext.value 
                        if isinstance(name, x509.IPAddress)
                    ]
                    if ip not in san_ips:
                        need_regeneration = True
                        reason = f"IP {ip} absente des SANs (présents: {san_ips})"
                except x509.ExtensionNotFound:
                    need_regeneration = True
                    reason = "pas d'extension SAN dans le certificat"
        except Exception as e:
            need_regeneration = True
            reason = f"erreur lecture certificat: {e}"
    
    # Régénérer le certificat si nécessaire
    if need_regeneration:
        config = load_env_config()
        
        # Déterminer les IPs à inclure
        ips_list = ["127.0.0.1"]
        if ip:
            ips_list.insert(0, ip)
        
        # Domaines par défaut
        domains_list = ["localhost", app_slug]
        
        try:
            success = create_server_certificate(
                name=app_slug,
                domains=domains_list,
                ips=ips_list,
                config=config,
                force=True  # Toujours écraser
            )
            if not success:
                raise HTTPException(
                    status_code=500,
                    detail=f"Impossible de générer le certificat pour '{app_slug}'"
                )
            # Log pour debug
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"🔐 Certificat auto-généré pour '{app_slug}' (raison: {reason})")
        except FileNotFoundError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur génération certificat: {e}")
    
    # Retourner le fichier demandé
    if file == "key":
        if not key_path.exists():
            raise HTTPException(status_code=404, detail="Clé privée non trouvée")
        return FileResponse(
            key_path,
            media_type="application/x-pem-file",
            filename=f"{app_slug}.key"
        )
    elif file == "fullchain":
        if not fullchain_path.exists():
            raise HTTPException(status_code=404, detail="Fullchain non trouvé")
        return FileResponse(
            fullchain_path,
            media_type="application/x-x509-ca-cert",
            filename=f"{app_slug}-fullchain.crt"
        )
    elif file == "bundle":
        # Créer un ZIP avec tous les fichiers
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            if cert_path.exists():
                zf.write(cert_path, f"{app_slug}.crt")
            if key_path.exists():
                zf.write(key_path, f"{app_slug}.key")
            if ca_cert_path.exists():
                zf.write(ca_cert_path, "hub-ca.crt")
        zip_buffer.seek(0)
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={app_slug}-certs.zip"}
        )
    else:
        # Par défaut: certificat
        if not cert_path.exists():
            raise HTTPException(status_code=404, detail="Certificat non trouvé")
        return FileResponse(
            cert_path,
            media_type="application/x-x509-ca-cert",
            filename=f"{app_slug}.crt"
        )


@router.get("/certificates/{name}/download")
async def download_certificate(name: str, file: str = "cert", admin=Depends(get_current_admin)):
    """
    Télécharge les fichiers d'un certificat (authentification admin requise).
    
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
