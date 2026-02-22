#!/usr/bin/env python3
"""
Script CLI pour importer/restaurer un environnement HUB.

Usage:
    python import_env.py backup.json                    # Import avec mode skip
    python import_env.py backup.json --mode update      # Met à jour les existants
    python import_env.py backup.json --mode error       # Échoue si conflit
    python import_env.py backup.json --restore-jwt      # Restaure aussi les clés JWT
    
Depuis Docker:
    docker compose exec hub-api python import_env.py /app/secrets/backup.json
    docker compose exec hub-api python import_env.py /app/secrets/backup.json --mode update --restore-jwt
"""
import asyncio
import argparse
import json
import base64
import sys
from datetime import datetime
from pathlib import Path
from enum import Enum

# Ajouter le répertoire courant au path pour les imports
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.database import async_session_maker, init_db
from app.models import User, Application, UserAppRole, ServiceAccount, AppRole, ApprovalStatus, service_account_app_access


class ImportMode(str, Enum):
    skip = "skip"
    update = "update"
    error = "error"


# Chemins des fichiers
SECRETS_DIR = Path("/code/secrets")
LOCAL_SECRETS_DIR = Path(__file__).parent.parent / "secrets"


def get_secrets_dir():
    """Retourne le répertoire des secrets (Docker ou local)"""
    if SECRETS_DIR.exists():
        return SECRETS_DIR
    elif LOCAL_SECRETS_DIR.exists():
        return LOCAL_SECRETS_DIR
    return SECRETS_DIR


def write_sa_secret(client_id: str, client_secret: str, app_slugs: list):
    """Écrit le secret d'un service account dans un fichier .env"""
    secrets_dir = get_secrets_dir()
    sa_dir = secrets_dir / "service_accounts"
    
    try:
        sa_dir.mkdir(parents=True, exist_ok=True)
        
        if app_slugs:
            app_name = app_slugs[0].replace('-', '_')
            filename = f"sa_{app_name}.env"
        else:
            filename = f"sa_{client_id[:16]}.env"
        
        filepath = sa_dir / filename
        
        content = f"""CLIENT_ID={client_id}
CLIENT_SECRET={client_secret}
# Applications autorisées: {', '.join(app_slugs)}
# Restauré lors de l'import le {datetime.now(timezone.utc).isoformat()}
"""
        filepath.write_text(content)
        print(f"   📄 Fichier créé: {filepath}")
        return True
    except Exception as e:
        print(f"   ⚠️  Erreur écriture {filename}: {e}")
        return False


async def import_environment(
    data: dict,
    mode: ImportMode = ImportMode.skip,
    restore_jwt_keys: bool = False,
    dry_run: bool = False,
    include_users: bool = True,
    include_applications: bool = True,
    include_user_app_roles: bool = True,
    include_service_accounts: bool = True
) -> dict:
    """Importe l'environnement depuis le fichier JSON"""
    
    await init_db()
    
    result = {
        "success": True,
        "users_created": 0,
        "users_updated": 0,
        "users_skipped": 0,
        "apps_created": 0,
        "apps_updated": 0,
        "apps_skipped": 0,
        "service_accounts_created": 0,
        "service_accounts_updated": 0,
        "service_accounts_skipped": 0,
        "user_app_roles_created": 0,
        "errors": [],
        "warnings": []
    }
    
    async with async_session_maker() as db:
        try:
            # 1. Import des applications (d'abord car référencées)
            print("\n📱 Import des applications...")
            app_id_map = {}
            
            if include_applications:
                for app_data in data.get("applications", []):
                    existing = await db.execute(
                        select(Application).where(Application.slug == app_data["slug"])
                    )
                    existing_app = existing.scalar_one_or_none()
                    
                    if existing_app:
                        if mode == ImportMode.error:
                            raise ValueError(f"Application '{app_data['slug']}' existe déjà")
                        elif mode == ImportMode.skip:
                            print(f"   ⏭️  Skip: {app_data['name']} ({app_data['slug']})")
                            result["apps_skipped"] += 1
                            app_id_map[app_data["slug"]] = existing_app.id
                            continue
                        elif mode == ImportMode.update:
                            if not dry_run:
                                existing_app.name = app_data["name"]
                                existing_app.description = app_data.get("description")
                                existing_app.icon = app_data.get("icon", "bi-app")
                                existing_app.color = app_data.get("color", "#0d6efd")
                                existing_app.api_url = app_data["api_url"]
                                existing_app.frontend_url = app_data.get("frontend_url")
                                existing_app.callback_url = app_data.get("callback_url")
                                existing_app.is_active = app_data.get("is_active", True)
                                existing_app.requires_auth = app_data.get("requires_auth", True)
                                existing_app.is_shared_service = app_data.get("is_shared_service", False)
                                # Restaurer le statut d'approbation
                                if app_data.get("approval_status"):
                                    existing_app.approval_status = ApprovalStatus(app_data["approval_status"])
                                if app_data.get("approved_at"):
                                    from datetime import datetime
                                    existing_app.approved_at = datetime.fromisoformat(app_data["approved_at"].replace("Z", "+00:00")) if isinstance(app_data["approved_at"], str) else app_data["approved_at"]
                                existing_app.rejection_reason = app_data.get("rejection_reason")
                            print(f"   🔄 Update: {app_data['name']} ({app_data['slug']})")
                            result["apps_updated"] += 1
                            app_id_map[app_data["slug"]] = existing_app.id
                    else:
                        if not dry_run:
                            # Préparer le statut d'approbation
                            approval_status = ApprovalStatus(app_data.get("approval_status", "approved"))
                            approved_at = None
                            if app_data.get("approved_at"):
                                from datetime import datetime as dt
                                approved_at = dt.fromisoformat(app_data["approved_at"].replace("Z", "+00:00")) if isinstance(app_data["approved_at"], str) else app_data["approved_at"]
                            
                            new_app = Application(
                                name=app_data["name"],
                                slug=app_data["slug"],
                                description=app_data.get("description"),
                                icon=app_data.get("icon", "bi-app"),
                                color=app_data.get("color", "#0d6efd"),
                                api_url=app_data["api_url"],
                                frontend_url=app_data.get("frontend_url"),
                                callback_url=app_data.get("callback_url"),
                                is_active=app_data.get("is_active", True),
                                requires_auth=app_data.get("requires_auth", True),
                                is_shared_service=app_data.get("is_shared_service", False),
                                approval_status=approval_status,
                                approved_at=approved_at,
                                rejection_reason=app_data.get("rejection_reason")
                            )
                            db.add(new_app)
                            await db.flush()
                            app_id_map[app_data["slug"]] = new_app.id
                            
                            # Créer les rôles disponibles
                            for role_data in app_data.get("available_roles", []):
                                app_role = AppRole(
                                    app_id=new_app.id,
                                    name=role_data["name"],
                                    description=role_data.get("description")
                                )
                                db.add(app_role)
                        
                        print(f"   ✅ Créé: {app_data['name']} ({app_data['slug']})")
                        result["apps_created"] += 1
            else:
                print("   ⏭️  Import des applications désactivé")
                result["warnings"].append("Import des applications désactivé")
            
            # Résoudre les app_id manquants depuis la base
            for app_data in data.get("applications", []):
                if app_data["slug"] not in app_id_map:
                    existing = await db.execute(
                        select(Application).where(Application.slug == app_data["slug"])
                    )
                    existing_app = existing.scalar_one_or_none()
                    if existing_app:
                        app_id_map[app_data["slug"]] = existing_app.id
            
            # 2. Import des utilisateurs
            print("\n👤 Import des utilisateurs...")
            user_id_map = {}
            
            if include_users:
                for user_data in data.get("users", []):
                    existing = await db.execute(
                        select(User).where(User.username == user_data["username"])
                    )
                    existing_user = existing.scalar_one_or_none()
                    
                    # Si non trouvé par username, vérifier par email (contrainte unique)
                    if not existing_user and user_data.get("email"):
                        existing_by_email = await db.execute(
                            select(User).where(User.email == user_data["email"])
                        )
                        existing_user = existing_by_email.scalar_one_or_none()
                        if existing_user:
                            result["warnings"].append(
                                f"User '{user_data['username']}' matché par email '{user_data['email']}' "
                                f"(username existant: '{existing_user.username}')"
                            )
                            print(f"   ⚠️  Match par email: {user_data['username']} -> {existing_user.username}")
                    
                    if existing_user:
                        if mode == ImportMode.error:
                            raise ValueError(f"Utilisateur '{user_data['username']}' existe déjà")
                        elif mode == ImportMode.skip:
                            print(f"   ⏭️  Skip: {user_data['username']}")
                            result["users_skipped"] += 1
                            user_id_map[user_data["username"]] = existing_user.id
                            continue
                        elif mode == ImportMode.update:
                            if not dry_run:
                                existing_user.email = user_data.get("email")
                                existing_user.hashed_password = user_data["hashed_password"]
                                existing_user.is_active = user_data.get("is_active", True)
                                existing_user.is_admin = user_data.get("is_admin", False)
                                existing_user.must_change_password = user_data.get("must_change_password", False)
                                existing_user.can_change_password = user_data.get("can_change_password", True)
                            print(f"   🔄 Update: {user_data['username']}")
                            result["users_updated"] += 1
                            user_id_map[user_data["username"]] = existing_user.id
                    else:
                        if not dry_run:
                            try:
                                new_user = User(
                                    username=user_data["username"],
                                    email=user_data.get("email"),
                                    hashed_password=user_data["hashed_password"],
                                    is_active=user_data.get("is_active", True),
                                    is_admin=user_data.get("is_admin", False),
                                    must_change_password=user_data.get("must_change_password", False),
                                    can_change_password=user_data.get("can_change_password", True)
                                )
                                db.add(new_user)
                                await db.flush()
                                user_id_map[user_data["username"]] = new_user.id
                            except IntegrityError as e:
                                await db.rollback()
                                result["warnings"].append(
                                    f"User '{user_data['username']}' ignoré (conflit d'intégrité: {str(e).split(chr(10))[0]})"
                                )
                                print(f"   ⚠️  Conflit: {user_data['username']} (email déjà utilisé?)")
                                result["users_skipped"] += 1
                                continue
                        
                        admin_flag = " (admin)" if user_data.get("is_admin") else ""
                        print(f"   ✅ Créé: {user_data['username']}{admin_flag}")
                        result["users_created"] += 1
            else:
                print("   ⏭️  Import des utilisateurs désactivé")
                result["warnings"].append("Import des utilisateurs désactivé")
            
            # Résoudre les user_id manquants depuis la base
            for user_data in data.get("users", []):
                if user_data["username"] not in user_id_map:
                    existing = await db.execute(
                        select(User).where(User.username == user_data["username"])
                    )
                    existing_user = existing.scalar_one_or_none()
                    if existing_user:
                        user_id_map[user_data["username"]] = existing_user.id
            
            # 3. Import des associations user-app-role
            print("\n🔗 Import des associations user-app...")
            
            if include_user_app_roles:
                for uar_data in data.get("user_app_roles", []):
                    user_id = user_id_map.get(uar_data["username"])
                    app_id = app_id_map.get(uar_data["app_slug"])
                    
                    if not user_id:
                        result["warnings"].append(f"User '{uar_data['username']}' non trouvé")
                        continue
                    if not app_id:
                        result["warnings"].append(f"App '{uar_data['app_slug']}' non trouvée")
                        continue
                    
                    existing_uar = await db.execute(
                        select(UserAppRole).where(
                            UserAppRole.user_id == user_id,
                            UserAppRole.app_id == app_id
                        )
                    )
                    if existing_uar.scalar_one_or_none():
                        if mode == ImportMode.update and not dry_run:
                            await db.execute(
                                UserAppRole.__table__.update()
                                .where(UserAppRole.user_id == user_id)
                                .where(UserAppRole.app_id == app_id)
                                .values(role=uar_data["role"])
                            )
                            print(f"   🔄 Update: {uar_data['username']} -> {uar_data['app_slug']} ({uar_data['role']})")
                        continue
                    
                    if not dry_run:
                        new_uar = UserAppRole(
                            user_id=user_id,
                            app_id=app_id,
                            role=uar_data["role"]
                        )
                        db.add(new_uar)
                    print(f"   ✅ Créé: {uar_data['username']} -> {uar_data['app_slug']} ({uar_data['role']})")
                    result["user_app_roles_created"] += 1
            else:
                print("   ⏭️  Import des associations user-app désactivé")
                result["warnings"].append("Import des associations user-app désactivé")
            
            # 4. Import des service accounts
            print("\n🤖 Import des service accounts...")
            
            if include_service_accounts:
                for sa_data in data.get("service_accounts", []):
                    existing = await db.execute(
                        select(ServiceAccount).options(selectinload(ServiceAccount.applications)).where(ServiceAccount.client_id == sa_data["client_id"])
                    )
                    existing_sa = existing.scalar_one_or_none()
                    
                    if existing_sa:
                        if mode == ImportMode.error:
                            raise ValueError(f"Service Account '{sa_data['client_id']}' existe déjà")
                        elif mode == ImportMode.skip:
                            print(f"   ⏭️  Skip: {sa_data['name']}")
                            result["service_accounts_skipped"] += 1
                            continue
                        elif mode == ImportMode.update:
                            if not dry_run:
                                existing_sa.name = sa_data["name"]
                                existing_sa.description = sa_data.get("description")
                                existing_sa.client_secret_hash = sa_data["client_secret_hash"]
                                existing_sa.is_active = sa_data.get("is_active", True)
                                
                                # Mettre à jour les applications via la table d'association
                                app_ids = [app_id_map[slug] for slug in sa_data.get("app_slugs", []) if slug in app_id_map]
                                # Supprimer les anciennes associations
                                await db.execute(
                                    service_account_app_access.delete().where(
                                        service_account_app_access.c.service_account_id == existing_sa.id
                                    )
                                )
                                # Insérer les nouvelles associations
                                for aid in app_ids:
                                    await db.execute(
                                        service_account_app_access.insert().values(
                                            service_account_id=existing_sa.id, app_id=aid
                                        )
                                    )
                            print(f"   🔄 Update: {sa_data['name']}")
                            result["service_accounts_updated"] += 1
                    else:
                        if not dry_run:
                            new_sa = ServiceAccount(
                                name=sa_data["name"],
                                client_id=sa_data["client_id"],
                                client_secret_hash=sa_data["client_secret_hash"],
                                description=sa_data.get("description"),
                                is_active=sa_data.get("is_active", True)
                            )
                            db.add(new_sa)
                            await db.flush()
                            
                            # Associer les applications via la table d'association
                            app_ids = [app_id_map[slug] for slug in sa_data.get("app_slugs", []) if slug in app_id_map]
                            for aid in app_ids:
                                await db.execute(
                                    service_account_app_access.insert().values(
                                        service_account_id=new_sa.id, app_id=aid
                                    )
                                )
                        
                        print(f"   ✅ Créé: {sa_data['name']} ({sa_data['client_id'][:20]}...)")
                        result["service_accounts_created"] += 1
                        
                        # Sauvegarder le secret en clair si disponible
                        if sa_data.get("client_secret_plain") and not dry_run:
                            write_sa_secret(
                                sa_data["client_id"],
                                sa_data["client_secret_plain"],
                                sa_data.get("app_slugs", [])
                            )
            else:
                print("   ⏭️  Import des service accounts désactivé")
                result["warnings"].append("Import des service accounts désactivé")
            
            # 5. Restauration des clés JWT
            if restore_jwt_keys:
                print("\n🔐 Restauration des clés JWT...")
                secrets_dir = get_secrets_dir()
                
                if data.get("jwt_private_key_b64") and data.get("jwt_public_key_b64"):
                    if not dry_run:
                        try:
                            secrets_dir.mkdir(parents=True, exist_ok=True)
                            
                            private_key_path = secrets_dir / "jwt_private_key.pem"
                            private_key_path.write_bytes(
                                base64.b64decode(data["jwt_private_key_b64"])
                            )
                            print(f"   ✅ Clé privée restaurée: {private_key_path}")
                            
                            public_key_path = secrets_dir / "jwt_public_key.pem"
                            public_key_path.write_bytes(
                                base64.b64decode(data["jwt_public_key_b64"])
                            )
                            print(f"   ✅ Clé publique restaurée: {public_key_path}")
                            
                            result["warnings"].append("Clés JWT restaurées - Redémarrage du Hub recommandé")
                        except Exception as e:
                            result["errors"].append(f"Erreur restauration JWT: {str(e)}")
                else:
                    print("   ⚠️  Pas de clés JWT dans le fichier d'import")
            
            if not dry_run:
                await db.commit()
                print("\n💾 Commit effectué")
            else:
                print("\n🔍 Mode dry-run: aucune modification effectuée")
        
        except Exception as e:
            await db.rollback()
            result["success"] = False
            result["errors"].append(str(e))
            print(f"\n❌ Erreur: {e}")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Importe/restaure un environnement HUB depuis un backup JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes d'import:
  skip    - Ignore les entités existantes (défaut)
  update  - Met à jour les entités existantes
  error   - Échoue si une entité existe déjà

Exemples:
  python import_env.py backup.json                     # Import avec skip
  python import_env.py backup.json -m update           # Met à jour les existants
  python import_env.py backup.json --restore-jwt       # Restaure les clés JWT
  python import_env.py backup.json --dry-run           # Simulation sans modification
  python import_env.py backup.json --no-users          # Import tout sauf les users
  python import_env.py backup.json --no-users --no-roles  # Seulement apps et SA
  
Depuis Docker:
  docker compose exec hub-api python import_env.py /app/secrets/backup.json
  docker compose exec hub-api python import_env.py /app/secrets/backup.json --no-users
        """
    )
    parser.add_argument(
        "input_file",
        type=str,
        help="Fichier JSON d'import"
    )
    parser.add_argument(
        "-m", "--mode",
        type=str,
        choices=["skip", "update", "error"],
        default="skip",
        help="Mode de gestion des conflits (défaut: skip)"
    )
    parser.add_argument(
        "--restore-jwt",
        action="store_true",
        help="Restaurer les clés JWT RSA"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulation sans modification"
    )
    parser.add_argument(
        "--no-users",
        action="store_true",
        help="Ne pas importer les utilisateurs"
    )
    parser.add_argument(
        "--no-apps",
        action="store_true",
        help="Ne pas importer les applications"
    )
    parser.add_argument(
        "--no-roles",
        action="store_true",
        help="Ne pas importer les associations user-app-role"
    )
    parser.add_argument(
        "--no-service-accounts",
        action="store_true",
        help="Ne pas importer les service accounts"
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"❌ Fichier non trouvé: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    print(f"📂 Lecture de: {input_path}")
    data = json.loads(input_path.read_text())
    
    print(f"📋 Version backup: {data.get('version', 'inconnue')}")
    print(f"📅 Exporté le: {data.get('exported_at', 'inconnu')}")
    print(f"👤 Exporté par: {data.get('exported_by', 'inconnu')}")
    print(f"\n🔧 Mode d'import: {args.mode.upper()}")
    
    if args.dry_run:
        print("🔍 MODE DRY-RUN ACTIVÉ")
    
    # Afficher les catégories désactivées
    exclusions = []
    if args.no_users:
        exclusions.append("users")
    if args.no_apps:
        exclusions.append("applications")
    if args.no_roles:
        exclusions.append("associations user-app")
    if args.no_service_accounts:
        exclusions.append("service accounts")
    if exclusions:
        print(f"🚫 Catégories exclues: {', '.join(exclusions)}")
    
    # Exécuter l'import
    result = asyncio.run(import_environment(
        data=data,
        mode=ImportMode(args.mode),
        restore_jwt_keys=args.restore_jwt,
        dry_run=args.dry_run,
        include_users=not args.no_users,
        include_applications=not args.no_apps,
        include_user_app_roles=not args.no_roles,
        include_service_accounts=not args.no_service_accounts
    ))
    
    # Afficher le résumé
    print("\n" + "="*50)
    print("📊 RÉSUMÉ DE L'IMPORT")
    print("="*50)
    print(f"✅ Succès: {'Oui' if result['success'] else 'Non'}")
    print(f"\n👤 Utilisateurs:")
    print(f"   Créés: {result['users_created']}")
    print(f"   Mis à jour: {result['users_updated']}")
    print(f"   Ignorés: {result['users_skipped']}")
    print(f"\n📱 Applications:")
    print(f"   Créées: {result['apps_created']}")
    print(f"   Mises à jour: {result['apps_updated']}")
    print(f"   Ignorées: {result['apps_skipped']}")
    print(f"\n🤖 Service Accounts:")
    print(f"   Créés: {result['service_accounts_created']}")
    print(f"   Mis à jour: {result['service_accounts_updated']}")
    print(f"   Ignorés: {result['service_accounts_skipped']}")
    print(f"\n🔗 Associations user-app: {result['user_app_roles_created']}")
    
    if result["warnings"]:
        print(f"\n⚠️  Avertissements:")
        for w in result["warnings"]:
            print(f"   - {w}")
    
    if result["errors"]:
        print(f"\n❌ Erreurs:")
        for e in result["errors"]:
            print(f"   - {e}")
        sys.exit(1)
    
    if not args.dry_run and args.restore_jwt:
        print("\n⚠️  IMPORTANT: Redémarrez le Hub pour prendre en compte les nouvelles clés JWT")
        print("   docker compose restart hub-api")


if __name__ == "__main__":
    main()
