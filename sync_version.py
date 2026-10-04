#!/usr/bin/env python3
"""
Script de synchronisation de version pour le Hub
=================================================

Ce script permet de :
1. Lire la version depuis le fichier VERSION (source de vérité)
2. Synchroniser cette version dans INTEGRATION.md, CHANGELOG.md et
   frontend/public/index.html (cache-busting ?v= des assets JS/CSS
   + badge de version affiché dans la navbar)
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
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# Chemins des fichiers
ROOT_DIR = Path(__file__).parent
VERSION_FILE = ROOT_DIR / "VERSION"
INTEGRATION_FILE = ROOT_DIR / "INTEGRATION.md"
CHANGELOG_FILE = ROOT_DIR / "CHANGELOG.md"
INDEX_HTML_FILE = ROOT_DIR / "frontend" / "public" / "index.html"

# Pattern de cache-busting des assets : href/src "...?v=X.Y.Z"
ASSET_VERSION_RE = re.compile(r"(\?v=)\d+\.\d+\.\d+")

# Pattern de la version affichée dans la navbar :
# <small class="navbar-version ...">vX.Y.Z</small>
# Tolère tout attribut entre class et le contenu (ex: id="navbarVersionBadge")
NAVBAR_VERSION_RE = re.compile(r'(class="navbar-version[^"]*"[^>]*>\s*v)\d+\.\d+\.\d+')

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


def get_versions_from_index() -> set[str]:
    """Retourne l'ensemble des versions ?v= présentes dans index.html"""
    if not INDEX_HTML_FILE.exists():
        return set()
    content = INDEX_HTML_FILE.read_text()
    return set(re.findall(r"\?v=(\d+\.\d+\.\d+)", content))


def get_version_from_navbar() -> str | None:
    """Extrait la version affichée dans la navbar de index.html"""
    if not INDEX_HTML_FILE.exists():
        return None
    content = INDEX_HTML_FILE.read_text()
    # Tolère tout attribut entre class et le contenu (ex: id="navbarVersionBadge")
    match = re.search(r'class="navbar-version[^"]*"[^>]*>\s*v(\d+\.\d+\.\d+)', content)
    return match.group(1) if match else None


def sync_index_html(version: str) -> bool:
    """
    Réécrit dans index.html :
    - les paramètres de cache-busting ?v=X.Y.Z des assets (JS/CSS/favicon)
    - la version affichée dans la navbar (badge à côté de la brand)
    pour qu'ils correspondent à la version.
    """
    if not INDEX_HTML_FILE.exists():
        print(f"⚠️  {INDEX_HTML_FILE} non trouvé, ignoré")
        return False

    content = INDEX_HTML_FILE.read_text()
    new_content, asset_count = ASSET_VERSION_RE.subn(f"\\g<1>{version}", content)
    new_content, navbar_count = NAVBAR_VERSION_RE.subn(f"\\g<1>{version}", new_content)

    if new_content != content:
        INDEX_HTML_FILE.write_text(new_content)
        print(f"✅ index.html mis à jour: {asset_count} asset(s) → ?v={version}, navbar → v{version}")
        return True
    else:
        if navbar_count == 0:
            print(f"⚠️  index.html: badge navbar-version introuvable (assets déjà à jour)")
        else:
            print(f"ℹ️  index.html déjà à jour ({asset_count} asset(s), navbar v{version})")
        return False


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


# ─── Garde-fous anti-écrasement croisé (incident 2026-10-04) ──────────
# Deux sessions poussant des releases en parallèle ont produit un doublon
# de numéro (méta) puis une régression de VERSION (0.71.6 après 0.71.7).
# Ces gardes s'exécutent AVANT toute écriture du bump.

def get_current_branch() -> str | None:
    """Branche courante, None si indéterminable (HEAD détaché...)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=ROOT_DIR,
        )
        if out.returncode != 0:
            return None
        branch = out.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_origin_version() -> str | None:
    """VERSION tel que poussé sur l'upstream de la branche courante.

    Retourne None si indéterminable (pas d'upstream, réseau indisponible,
    timeout) : le garde-fou se met alors en silencieux — il ne doit jamais
    bloquer un workflow hors-ligne légitime.
    """
    branch = get_current_branch()
    if not branch:
        return None
    try:
        up = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}"],
            capture_output=True, text=True, timeout=10, cwd=ROOT_DIR,
        )
        if up.returncode != 0:
            return None
        upstream = up.stdout.strip()
        if not upstream:
            return None
        fetch = subprocess.run(
            ["git", "fetch", upstream.split("/")[0], branch, "--quiet"],
            capture_output=True, text=True, timeout=30, cwd=ROOT_DIR,
        )
        if fetch.returncode != 0:
            return None
        show = subprocess.run(
            ["git", "show", f"{upstream}:VERSION"],
            capture_output=True, text=True, timeout=10, cwd=ROOT_DIR,
        )
        if show.returncode != 0:
            return None
        origin_version = show.stdout.strip()
        return origin_version or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_origin_not_ahead(local_version: str) -> bool:
    """True si le bump peut se faire : origin n'est pas strictement en avance.

    origin en avance = une autre session a poussé une release pendant notre
    travail → il faut `git pull` (qui déclenchera la résolution
    CHANGELOG/VERSION) puis re-bump, sinon notre bump écrase la version
    parallèle (régression poussée, incident 2026-10-04).
    """
    origin = get_origin_version()
    if origin is None:
        print("   ℹ️  Version origin indéterminable (hors-ligne ?) — garde en silencieux")
        return True
    if parse_version(origin) > parse_version(local_version):
        print(f"❌ COLLISION DE RELEASE : origin/{get_current_branch()} est en version {origin},")
        print(f"   le dépôt local est en {local_version}. Une autre session a poussé une release")
        print(f"   pendant votre travail.")
        print(f"   → git pull origin {get_current_branch()} (résolvez VERSION/CHANGELOG si conflit)")
        print(f"   → puis relancez le bump.")
        return False
    if parse_version(origin) < parse_version(local_version):
        print(f"   ℹ️  Version locale en avance sur origin ({local_version} > {origin}) — commits non poussés.")
    return True


def check_changelog_collision(new_version: str) -> bool:
    """True si le numéro cible n'est pas déjà pris dans le CHANGELOG.

    Deux sessions bumpant « patch » en parallèle depuis la même base
    produisent le même numéro : la deuxième écriture fabrique un doublon
    d'entrée (incident 2026-10-04, méta-repo 0.71.6 en double).
    """
    try:
        content = CHANGELOG_FILE.read_text()
    except OSError:
        return True
    if f"## [{new_version}]" in content:
        print(f"❌ COLLISION DE RELEASE : l'entrée [{new_version}] existe déjà dans le CHANGELOG.")
        print(f"   Une autre session a probablement pris ce numéro en parallèle.")
        print(f"   → git pull, résolvez, puis re-bump (le numéro suivant sera alloué).")
        return False
    return True


def check_versions() -> bool:
    """Vérifie la cohérence des versions entre tous les fichiers"""
    version = read_version()
    integration_version = get_version_from_integration()
    changelog_version = get_version_from_changelog()
    index_versions = get_versions_from_index()
    navbar_version = get_version_from_navbar()

    if not index_versions:
        index_display = "❌ Non trouvée"
    elif index_versions == {version}:
        index_display = version
    else:
        index_display = ", ".join(sorted(index_versions)) + " (incohérent)"

    print(f"\n📋 Vérification des versions:")
    print(f"   VERSION:        {version}")
    print(f"   INTEGRATION.md: {integration_version or '❌ Non trouvée'}")
    print(f"   CHANGELOG.md:   {changelog_version or '❌ Non trouvée'}")
    print(f"   index.html:     {index_display}")
    print(f"   navbar:         {navbar_version or '❌ Non trouvée'}")

    all_match = True
    if integration_version and integration_version != version:
        print(f"\n⚠️  INTEGRATION.md ({integration_version}) != VERSION ({version})")
        all_match = False
    if changelog_version and changelog_version != version:
        print(f"\n⚠️  CHANGELOG.md ({changelog_version}) != VERSION ({version})")
        all_match = False
    if index_versions and index_versions != {version}:
        print(f"\n⚠️  index.html ({', '.join(sorted(index_versions))}) != VERSION ({version})")
        all_match = False
    if navbar_version != version:
        print(f"\n⚠️  navbar ({navbar_version or 'absente'}) != VERSION ({version})")
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
    sync_index_html(version)
    
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

        # Garde-fous anti-écrasement croisé (AVANT toute écriture) :
        # 1. origin ne doit pas être en avance (autre session a poussé une release)
        if not check_origin_not_ahead(old_version):
            sys.exit(2)
        # 2. le numéro cible ne doit pas déjà exister (double bump parallèle)
        candidate = bump_version(old_version, bump_type)
        if not check_changelog_collision(candidate):
            sys.exit(2)

        new_version = candidate

        print(f"\n📦 Bump de version: {old_version} → {new_version}")
        write_version(new_version)
        sync_all(new_version)
    
    else:
        print(f"❌ Commande inconnue: {command}")
        print("   Commandes disponibles: show, check, sync, bump")
        sys.exit(1)


if __name__ == "__main__":
    main()
