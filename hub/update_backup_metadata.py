#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Script pour mettre à jour les métadonnées des backups existants.

Ce script lit les fichiers ZIP de backup et extrait les informations
depuis data.json pour mettre à jour les champs:
- app_version
- total_items
- total_preferences  
- total_files
- description (résumé du contenu)

Usage:
    python update_backup_metadata.py
"""
import asyncio
import sys
import io
import json
import zipfile
from pathlib import Path

# Ajouter le répertoire parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select
from app.database import async_session_maker
from app.models import AppBackup

STORAGE_BASE_DIR = Path("/code/storage/backups")


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def extract_metadata_from_zip(zip_content: bytes) -> dict:
    """
    Extrait les métadonnées depuis un fichier ZIP de backup.
    """
    result = {
        "app_version": None,
        "format_version": None,
        "total_items": 0,
        "total_preferences": 0,
        "total_files": 0,
        "total_tables": 0,
        "total_rows": 0,
        "includes_vaultwarden_snapshot": False,
        "includes_secret_bundle": False,
        "restore_requires_offline": False,
        "content_summary": ""
    }
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zf:
            file_list = zf.namelist()
            normalized_names = {name.lstrip("./"): name for name in file_list}
            result["includes_vaultwarden_snapshot"] = "vaultwarden/manifest.json" in normalized_names
            result["includes_secret_bundle"] = "secrets/backup-secrets.json" in normalized_names
            result["restore_requires_offline"] = result["includes_vaultwarden_snapshot"]

            data_file = None
            for name in ['data.json', 'manifest.json', 'backup.json']:
                if name in normalized_names:
                    data_file = normalized_names[name]
                    break
            
            if data_file:
                with zf.open(data_file) as f:
                    data = json.load(f)
                    
                    metadata = data.get("metadata", {})
                    result["app_version"] = metadata.get("app_version") or metadata.get("version")
                    result["format_version"] = metadata.get("format_version")
                    result["total_tables"] = safe_int(metadata.get("total_tables"))
                    result["total_rows"] = safe_int(metadata.get("total_rows"))
                    result["includes_vaultwarden_snapshot"] = (
                        result["includes_vaultwarden_snapshot"]
                        or parse_bool(metadata.get("includes_vaultwarden_snapshot"))
                    )
                    result["includes_secret_bundle"] = (
                        result["includes_secret_bundle"]
                        or parse_bool(metadata.get("includes_secret_bundle"))
                    )
                    result["restore_requires_offline"] = (
                        result["restore_requires_offline"]
                        or parse_bool(metadata.get("restore_requires_offline"))
                    )
                    
                    backup_data = data.get("data", {})
                    
                    items = backup_data.get("items", [])
                    result["total_items"] = len(items)
                    
                    prefs = backup_data.get("user_preferences", []) or backup_data.get("preferences", [])
                    result["total_preferences"] = len(prefs)
                    
                    files = backup_data.get("uploaded_files", []) or backup_data.get("files", [])
                    result["total_files"] = len(files)
                    
                    # Résumé du contenu
                    summary_parts = []
                    for key, value in backup_data.items():
                        if isinstance(value, list) and len(value) > 0:
                            display_name = key.replace("_", " ").title()
                            summary_parts.append(f"{len(value)} {display_name}")
                    
                    if summary_parts:
                        result["content_summary"] = ", ".join(summary_parts)
                    else:
                        result["content_summary"] = "Backup vide"
            else:
                result["total_files"] = len(file_list)
                result["content_summary"] = f"{len(file_list)} fichier(s) dans l'archive"
                
    except Exception as e:
        print(f"  ⚠️ Erreur extraction: {e}")
        result["content_summary"] = "Erreur d'extraction"
    
    return result


async def update_all_backups():
    """Met à jour les métadonnées de tous les backups existants."""
    async with async_session_maker() as db:
        # Récupérer tous les backups
        result = await db.execute(select(AppBackup))
        backups = result.scalars().all()
        
        print(f"📦 {len(backups)} backup(s) trouvé(s)")
        
        updated = 0
        errors = 0
        
        for backup in backups:
            print(f"\n🔍 Traitement: {backup.app_slug}/{backup.backup_id}")
            
            # Chemin du fichier ZIP
            zip_path = STORAGE_BASE_DIR / backup.storage_path
            
            if not zip_path.exists():
                print(f"  ❌ Fichier non trouvé: {zip_path}")
                errors += 1
                continue
            
            # Lire le ZIP
            with open(zip_path, "rb") as f:
                zip_content = f.read()
            
            # Extraire les métadonnées
            metadata = extract_metadata_from_zip(zip_content)
            
            # Mettre à jour si nécessaire
            changed = False
            
            if not backup.app_version and metadata["app_version"]:
                backup.app_version = metadata["app_version"]
                changed = True
                print(f"  📌 Version: {metadata['app_version']}")
            
            if backup.total_items == 0 and metadata["total_items"] > 0:
                backup.total_items = metadata["total_items"]
                changed = True
            
            if backup.total_preferences == 0 and metadata["total_preferences"] > 0:
                backup.total_preferences = metadata["total_preferences"]
                changed = True
            
            if backup.total_files == 0 and metadata["total_files"] > 0:
                backup.total_files = metadata["total_files"]
                changed = True

            if not backup.format_version and metadata["format_version"]:
                backup.format_version = metadata["format_version"]
                changed = True

            if backup.total_tables == 0 and metadata["total_tables"] > 0:
                backup.total_tables = metadata["total_tables"]
                changed = True

            if backup.total_rows == 0 and metadata["total_rows"] > 0:
                backup.total_rows = metadata["total_rows"]
                changed = True

            if not backup.includes_vaultwarden_snapshot and metadata["includes_vaultwarden_snapshot"]:
                backup.includes_vaultwarden_snapshot = True
                changed = True
                print("  🔐 Snapshot Vaultwarden détecté")

            if not backup.includes_secret_bundle and metadata["includes_secret_bundle"]:
                backup.includes_secret_bundle = True
                changed = True
                print("  🔑 Bundle de secrets détecté")

            if not backup.restore_requires_offline and metadata["restore_requires_offline"]:
                backup.restore_requires_offline = True
                changed = True
            
            if not backup.description and metadata["content_summary"]:
                backup.description = metadata["content_summary"]
                changed = True
                print(f"  📝 Contenu: {metadata['content_summary']}")
            
            if changed:
                updated += 1
                print(f"  ✅ Mis à jour")
            else:
                print(f"  ⏭️ Pas de changement nécessaire")
        
        await db.commit()
        
        print(f"\n{'='*50}")
        print(f"✅ {updated} backup(s) mis à jour")
        print(f"❌ {errors} erreur(s)")


if __name__ == "__main__":
    asyncio.run(update_all_backups())
