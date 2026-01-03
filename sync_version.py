#!/usr/bin/env python3
"""
Script de synchronisation de version pour le Hub
=================================================

Ce script permet de :
1. Lire la version depuis le fichier VERSION (source de vérité)
2. Synchroniser cette version dans INTEGRATION.md et CHANGELOG.md
3. Optionnellement incrémenter la version (patch, minor, major)

Usage:
    # Synchroniser la version actuelle dans tous les fichiers
    python sync_version.py sync
    
    # Incrémenter et synchroniser
    python sync_version.py bump patch    # 2.5.3 -> 2.5.4
    python sync_version.py bump minor    # 2.5.3 -> 2.6.0
    python sync_version.py bump major    # 2.5.3 -> 3.0.0
    
    # Afficher la version actuelle
    python sync_version.py show
    
    # Vérifier la cohérence des versions
    python sync_version.py check
"""

import re
import sys
from pathlib import Path
from datetime import datetime

# Chemins des fichiers
ROOT_DIR = Path(__file__).parent
VERSION_FILE = ROOT_DIR / "VERSION"
INTEGRATION_FILE = ROOT_DIR / "INTEGRATION.md"
CHANGELOG_FILE = ROOT_DIR / "CHANGELOG.md"

# Format de date français
MOIS_FR = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril",
    5: "mai", 6: "juin", 7: "juillet", 8: "août",
    9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"
}


def get_french_date() -> str:
    """Retourne la date au format français (ex: 3 janvier 2026)"""
    now = datetime.now()
    return f"{now.day} {MOIS_FR[now.month]} {now.year}"


def read_version() -> str:
    """Lit la version depuis le fichier VERSION"""
    if not VERSION_FILE.exists():
        print(f"❌ Fichier {VERSION_FILE} non trouvé")
        sys.exit(1)
    return VERSION_FILE.read_text().strip()


def write_version(version: str) -> None:
    """Écrit la version dans le fichier VERSION"""
    VERSION_FILE.write_text(f"{version}\n")
    print(f"✅ VERSION mis à jour: {version}")


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse une version semver en tuple (major, minor, patch)"""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        print(f"❌ Format de version invalide: {version}")
        sys.exit(1)
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(version: str, bump_type: str) -> str:
    """Incrémente la version selon le type (patch, minor, major)"""
    major, minor, patch = parse_version(version)
    
    if bump_type == "patch":
        patch += 1
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        print(f"❌ Type de bump invalide: {bump_type}")
        print("   Utilisez: patch, minor, ou major")
        sys.exit(1)
    
    return f"{major}.{minor}.{patch}"


def get_version_from_integration() -> str | None:
    """Extrait la version depuis INTEGRATION.md"""
    if not INTEGRATION_FILE.exists():
        return None
    content = INTEGRATION_FILE.read_text()
    match = re.search(r"\*\*Version\*\*\s*:\s*(\d+\.\d+\.\d+)", content)
    return match.group(1) if match else None


def get_version_from_changelog() -> str | None:
    """Extrait la dernière version depuis CHANGELOG.md"""
    if not CHANGELOG_FILE.exists():
        return None
    content = CHANGELOG_FILE.read_text()
    match = re.search(r"## \[(\d+\.\d+\.\d+)\]", content)
    return match.group(1) if match else None


def sync_integration(version: str, date: str) -> bool:
    """Synchronise la version dans INTEGRATION.md"""
    if not INTEGRATION_FILE.exists():
        print(f"⚠️  {INTEGRATION_FILE} non trouvé, ignoré")
        return False
    
    content = INTEGRATION_FILE.read_text()
    
    # Mettre à jour la version
    new_content = re.sub(
        r"(\*\*Version\*\*\s*:\s*)\d+\.\d+\.\d+",
        f"\\g<1>{version}",
        content
    )
    
    # Mettre à jour la date
    new_content = re.sub(
        r"(\*\*Dernière mise à jour\*\*\s*:\s*).+?(?=\s*$|\s*\n)",
        f"\\g<1>{date}",
        new_content,
        flags=re.MULTILINE
    )
    
    if new_content != content:
        INTEGRATION_FILE.write_text(new_content)
        print(f"✅ INTEGRATION.md mis à jour: v{version} ({date})")
        return True
    else:
        print(f"ℹ️  INTEGRATION.md déjà à jour")
        return False


def sync_changelog(version: str, date: str, old_version: str = None) -> bool:
    """
    Synchronise la version dans CHANGELOG.md
    Si la version n'existe pas, crée une nouvelle entrée
    """
    if not CHANGELOG_FILE.exists():
        print(f"⚠️  {CHANGELOG_FILE} non trouvé, ignoré")
        return False
    
    content = CHANGELOG_FILE.read_text()
    current_changelog_version = get_version_from_changelog()
    
    # Si la version existe déjà, mettre à jour la date uniquement
    if current_changelog_version == version:
        new_content = re.sub(
            rf"(## \[{re.escape(version)}\] - ).+",
            f"\\g<1>{date}",
            content
        )
        if new_content != content:
            CHANGELOG_FILE.write_text(new_content)
            print(f"✅ CHANGELOG.md: date mise à jour pour v{version}")
            return True
        print(f"ℹ️  CHANGELOG.md déjà à jour")
        return False
    
    # Sinon, créer une nouvelle entrée
    new_entry = f"""## [{version}] - {date}

### 🆕 Nouvelle version

> ⚠️ Cette entrée a été créée automatiquement par `sync_version.py`.
> Veuillez compléter les détails des modifications.

| Fonctionnalité | Description |
|----------------|-------------|
| **À compléter** | Décrivez les changements de cette version |

---

"""
    
    # Insérer après le header du changelog
    insert_marker = "---\n\n## ["
    if insert_marker in content:
        new_content = content.replace(
            insert_marker,
            f"---\n\n{new_entry}## [",
            1
        )
        CHANGELOG_FILE.write_text(new_content)
        print(f"✅ CHANGELOG.md: nouvelle entrée créée pour v{version}")
        print(f"   ⚠️  Pensez à compléter les détails de la version !")
        return True
    
    print(f"⚠️  Impossible d'ajouter l'entrée au CHANGELOG")
    return False


def check_versions() -> bool:
    """Vérifie la cohérence des versions entre tous les fichiers"""
    version = read_version()
    integration_version = get_version_from_integration()
    changelog_version = get_version_from_changelog()
    
    print(f"\n📋 Vérification des versions:")
    print(f"   VERSION:        {version}")
    print(f"   INTEGRATION.md: {integration_version or '❌ Non trouvée'}")
    print(f"   CHANGELOG.md:   {changelog_version or '❌ Non trouvée'}")
    
    all_match = True
    if integration_version and integration_version != version:
        print(f"\n⚠️  INTEGRATION.md ({integration_version}) != VERSION ({version})")
        all_match = False
    if changelog_version and changelog_version != version:
        print(f"\n⚠️  CHANGELOG.md ({changelog_version}) != VERSION ({version})")
        all_match = False
    
    if all_match:
        print(f"\n✅ Toutes les versions sont synchronisées: {version}")
    else:
        print(f"\n❌ Les versions ne sont pas synchronisées !")
        print(f"   Exécutez: python sync_version.py sync")
    
    return all_match


def sync_all(version: str = None) -> None:
    """Synchronise la version dans tous les fichiers"""
    if version is None:
        version = read_version()
    
    date = get_french_date()
    
    print(f"\n🔄 Synchronisation de la version {version} ({date})")
    print("-" * 50)
    
    sync_integration(version, date)
    sync_changelog(version, date)
    
    print("-" * 50)
    print(f"✅ Synchronisation terminée")
    print(f"\n💡 N'oubliez pas de redémarrer le conteneur:")
    print(f"   docker restart hub-api")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    if command == "show":
        version = read_version()
        print(f"Version actuelle: {version}")
    
    elif command == "check":
        success = check_versions()
        sys.exit(0 if success else 1)
    
    elif command == "sync":
        sync_all()
    
    elif command == "bump":
        if len(sys.argv) < 3:
            print("❌ Type de bump requis: patch, minor, ou major")
            sys.exit(1)
        
        bump_type = sys.argv[2].lower()
        old_version = read_version()
        new_version = bump_version(old_version, bump_type)
        
        print(f"\n📦 Bump de version: {old_version} → {new_version}")
        write_version(new_version)
        sync_all(new_version)
    
    else:
        print(f"❌ Commande inconnue: {command}")
        print("   Commandes disponibles: show, check, sync, bump")
        sys.exit(1)


if __name__ == "__main__":
    main()
