#!/usr/bin/env python3
"""
Script CLI pour exporter l'environnement HUB complet.

Usage:
    python export_env.py                          # Export complet
    python export_env.py --output backup.json     # Fichier personnalisé
    python export_env.py --no-jwt-keys            # Sans clés JWT
    python export_env.py --no-sa-secrets          # Sans secrets SA
    
Depuis Docker:
    docker compose exec hub-api python export_env.py
    docker compose exec hub-api python export_env.py --output /app/secrets/backup.json
"""
import asyncio
import argparse
import json
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ajouter le répertoire courant au path pour les imports
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_maker, init_db
from app.models import User, Application, UserAppRole, ServiceAccount, AppRole


# Chemins des fichiers
SECRETS_DIR = Path("/code/secrets")
SERVICE_ACCOUNTS_DIR = SECRETS_DIR / "service_accounts"
JWT_PRIVATE_KEY_PATH = SECRETS_DIR / "jwt_private_key.pem"
JWT_PUBLIC_KEY_PATH = SECRETS_DIR / "jwt_public_key.pem"

# Chemins alternatifs (développement local)
LOCAL_SECRETS_DIR = Path(__file__).parent.parent / "secrets"


def get_secrets_dir():
    """Retourne le répertoire des secrets (Docker ou local)"""
    if SECRETS_DIR.exists():
        return SECRETS_DIR
    elif LOCAL_SECRETS_DIR.exists():
        return LOCAL_SECRETS_DIR
    return SECRETS_DIR  # Par défaut


def read_sa_secret(client_id: str) -> str | None:
    """Lit le secret d'un service account depuis son fichier .env"""
    secrets_dir = get_secrets_dir()
    sa_dir = secrets_dir / "service_accounts"
    
    if not sa_dir.exists():
        return None
    
    for env_file in sa_dir.glob("sa_*.env"):
        try:
            content = env_file.read_text()
            lines = content.split('\n')
            found_client_id = None
            secret = None
            
            for line in lines:
                if line.startswith('CLIENT_ID='):
                    found_client_id = line.split('=', 1)[1].strip()
                elif line.startswith('CLIENT_SECRET='):
                    secret = line.split('=', 1)[1].strip()
            
            if found_client_id == client_id and secret:
                return secret
        except Exception:
            continue
    
    return None


async def export_environment(
    include_jwt_keys: bool = True,
    include_sa_secrets: bool = True
) -> dict:
    """Exporte l'environnement complet"""
    
    await init_db()
    
    async with async_session_maker() as db:
        # 1. Export des utilisateurs
        users_result = await db.execute(
            select(User).options(
                selectinload(User.app_roles).selectinload(UserAppRole.application)
            )
        )
        users = users_result.scalars().all()
        
        users_export = [
            {
                "username": user.username,
                "email": user.email,
                "hashed_password": user.hashed_password,
                "is_active": user.is_active,
                "is_admin": user.is_admin,
                "must_change_password": user.must_change_password or False,
                "can_change_password": user.can_change_password if user.can_change_password is not None else True,
                "created_at": user.created_at.isoformat() if user.created_at else None
            }
            for user in users
        ]
        
        # 2. Export des applications
        apps_result = await db.execute(
            select(Application).options(
                selectinload(Application.available_roles),
                selectinload(Application.approved_by)
            )
        )
        apps = apps_result.scalars().all()
        
        apps_export = [
            {
                "name": app.name,
                "slug": app.slug,
                "description": app.description,
                "icon": app.icon,
                "color": app.color,
                "api_url": app.api_url,
                "frontend_url": app.frontend_url,
                "callback_url": app.callback_url,
                "is_active": app.is_active,
                "requires_auth": app.requires_auth,
                "is_shared_service": app.is_shared_service,
                "approval_status": app.approval_status.value if app.approval_status else "approved",
                "approved_by_username": app.approved_by.username if app.approved_by else None,
                "approved_at": app.approved_at.isoformat() if app.approved_at else None,
                "rejection_reason": app.rejection_reason,
                "created_at": app.created_at.isoformat() if app.created_at else None,
                "available_roles": [
                    {"name": role.name, "description": role.description}
                    for role in app.available_roles
                ]
            }
            for app in apps
        ]
        
        # 3. Export des associations user-app-role
        user_app_roles_result = await db.execute(
            select(UserAppRole).options(
                selectinload(UserAppRole.user),
                selectinload(UserAppRole.application)
            )
        )
        user_app_roles = user_app_roles_result.scalars().all()
        
        user_app_roles_export = [
            {
                "username": uar.user.username,
                "app_slug": uar.application.slug,
                "role": uar.role
            }
            for uar in user_app_roles
        ]
        
        # 4. Export des service accounts
        sa_result = await db.execute(
            select(ServiceAccount).options(selectinload(ServiceAccount.applications))
        )
        service_accounts = sa_result.scalars().all()
        
        service_accounts_export = []
        for sa in service_accounts:
            client_secret_plain = None
            if include_sa_secrets:
                client_secret_plain = read_sa_secret(sa.client_id)
            
            service_accounts_export.append({
                "name": sa.name,
                "client_id": sa.client_id,
                "client_secret_hash": sa.client_secret_hash,
                "client_secret_plain": client_secret_plain,
                "description": sa.description,
                "is_active": sa.is_active,
                "created_at": sa.created_at.isoformat() if sa.created_at else None,
                "app_slugs": [app.slug for app in sa.applications]
            })
        
        # 5. Export des clés JWT
        jwt_private_key_b64 = None
        jwt_public_key_b64 = None
        
        if include_jwt_keys:
            secrets_dir = get_secrets_dir()
            private_key_path = secrets_dir / "jwt_private_key.pem"
            public_key_path = secrets_dir / "jwt_public_key.pem"
            
            if private_key_path.exists():
                jwt_private_key_b64 = base64.b64encode(
                    private_key_path.read_bytes()
                ).decode()
                print(f"✅ Clé privée JWT incluse depuis {private_key_path}")
            else:
                print(f"⚠️  Clé privée JWT non trouvée: {private_key_path}")
            
            if public_key_path.exists():
                jwt_public_key_b64 = base64.b64encode(
                    public_key_path.read_bytes()
                ).decode()
                print(f"✅ Clé publique JWT incluse depuis {public_key_path}")
            else:
                print(f"⚠️  Clé publique JWT non trouvée: {public_key_path}")
    
    return {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": "cli",
        "users": users_export,
        "applications": apps_export,
        "user_app_roles": user_app_roles_export,
        "service_accounts": service_accounts_export,
        "jwt_private_key_b64": jwt_private_key_b64,
        "jwt_public_key_b64": jwt_public_key_b64
    }


def main():
    parser = argparse.ArgumentParser(
        description="Exporte l'environnement HUB complet pour backup/migration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python export_env.py                           # Export complet vers stdout
  python export_env.py -o backup.json            # Export vers fichier
  python export_env.py --no-jwt-keys             # Sans clés JWT
  python export_env.py --no-sa-secrets           # Sans secrets service accounts
  
Depuis Docker:
  docker compose exec hub-api python export_env.py -o /app/secrets/backup.json
        """
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Fichier de sortie (défaut: stdout)"
    )
    parser.add_argument(
        "--no-jwt-keys",
        action="store_true",
        help="Ne pas inclure les clés JWT RSA"
    )
    parser.add_argument(
        "--no-sa-secrets",
        action="store_true",
        help="Ne pas inclure les secrets des service accounts"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Formater le JSON (défaut: True)"
    )
    
    args = parser.parse_args()
    
    print("🔄 Export de l'environnement HUB...", file=sys.stderr)
    
    # Exécuter l'export
    export_data = asyncio.run(export_environment(
        include_jwt_keys=not args.no_jwt_keys,
        include_sa_secrets=not args.no_sa_secrets
    ))
    
    # Statistiques
    print(f"\n📊 Statistiques:", file=sys.stderr)
    print(f"   - Utilisateurs: {len(export_data['users'])}", file=sys.stderr)
    print(f"   - Applications: {len(export_data['applications'])}", file=sys.stderr)
    print(f"   - Associations user-app: {len(export_data['user_app_roles'])}", file=sys.stderr)
    print(f"   - Service Accounts: {len(export_data['service_accounts'])}", file=sys.stderr)
    print(f"   - Clés JWT incluses: {'✅' if export_data.get('jwt_private_key_b64') else '❌'}", file=sys.stderr)
    
    # Sérialiser en JSON
    json_output = json.dumps(export_data, indent=2 if args.pretty else None, ensure_ascii=False)
    
    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json_output)
        print(f"\n✅ Export sauvegardé dans: {output_path}", file=sys.stderr)
        print(f"   Taille: {len(json_output)} octets", file=sys.stderr)
    else:
        print(json_output)
    
    print("\n⚠️  ATTENTION: Ce fichier contient des données sensibles!", file=sys.stderr)
    print("   - Mots de passe hashés", file=sys.stderr)
    print("   - Secrets des service accounts", file=sys.stderr)
    print("   - Clés privées JWT", file=sys.stderr)
    print("   Stockez-le de manière sécurisée!", file=sys.stderr)


if __name__ == "__main__":
    main()
