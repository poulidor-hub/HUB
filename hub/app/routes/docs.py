"""
Routes pour exposer la documentation du Hub
============================================
Le fichier VERSION est la SOURCE UNIQUE de vérité pour la version.
Les métadonnées de documentation sont extraites de INTEGRATION.md.
Les sections et shortcuts sont extraits dynamiquement du fichier.
"""
import re
from typing import Optional, Dict, List, Tuple
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/docs", tags=["documentation"])

# Chemin vers les fichiers (depuis le conteneur Docker)
DOCS_PATH = Path("/code/INTEGRATION.md")
CHANGELOG_PATH = Path("/code/CHANGELOG.md")
VERSION_PATH = Path("/code/VERSION")


def get_hub_version() -> str:
    """
    Lit la version depuis le fichier VERSION (source unique de vérité).
    Fallback sur INTEGRATION.md si VERSION n'existe pas.
    """
    if VERSION_PATH.exists():
        return VERSION_PATH.read_text().strip()
    
    # Fallback: extraire depuis INTEGRATION.md
    if DOCS_PATH.exists():
        content = DOCS_PATH.read_text(encoding="utf-8")
        match = re.search(r"\*\*Version\*\*\s*:\s*(\d+\.\d+\.\d+)", content)
        if match:
            return match.group(1)
    
    return "unknown"


# Alias statiques vers des mots-clés de recherche
# Ces alias permettent de trouver des sections par mot-clé
SECTION_ALIASES = {
    "principes": ["principes", "fondamentaux"],
    "config": ["configuration de l'application"],
    "auto-register": ["auto-enregistrement", "récupération dynamique"],
    "backend": ["backend", "fastapi"],
    "frontend": ["intégration frontend"],
    "m2m": ["machine-to-machine", "m2m"],
    "shared": ["services partagés", "apis de données"],
    "partages": ["services partagés", "apis de données"],
    "inter-api": ["inter-api", "communication inter-api"],
    "roles": ["gestion des rôles"],
    "endpoints": ["endpoints du hub"],
    "docker": ["dockerfile"],
    "resume": ["résumé"],
    "contact": ["contact"],
}


def parse_doc_sections(content: str) -> List[Tuple[int, str, int, int]]:
    """
    Parse le document et extrait toutes les sections de niveau 2.
    
    Returns:
        Liste de tuples (numéro, titre_complet, ligne_début, ligne_fin)
    """
    lines = content.split("\n")
    sections = []
    current_section = None
    
    for i, line in enumerate(lines):
        # Chercher les sections ## N. Titre
        match = re.match(r"^## (\d+)\. (.+)$", line)
        if match:
            # Fermer la section précédente
            if current_section:
                sections.append((*current_section, i))
            
            num = int(match.group(1))
            title = f"{num}. {match.group(2)}"
            current_section = (num, title, i)
    
    # Fermer la dernière section
    if current_section:
        sections.append((*current_section, len(lines)))
    
    return sections


def get_doc_metadata(content: str) -> Dict:
    """
    Extrait les métadonnées du document (version, date, etc.)
    """
    metadata = {
        "version": None,
        "date": None,
        "author": None
    }
    
    # Chercher dans les premières lignes
    lines = content.split("\n")[:20]
    for line in lines:
        if "**Version**" in line:
            match = re.search(r":\s*(\d+\.\d+\.\d+)", line)
            if match:
                metadata["version"] = match.group(1)
        elif "**Dernière mise à jour**" in line:
            match = re.search(r":\s*(.+?)\s*$", line)
            if match:
                metadata["date"] = match.group(1).strip()
        elif "**Auteur**" in line:
            match = re.search(r":\s*(.+?)\s*$", line)
            if match:
                metadata["author"] = match.group(1).strip()
    
    return metadata


def find_section_by_alias(sections: List[Tuple], alias: str) -> Optional[Tuple]:
    """
    Trouve une section par alias (recherche dans les titres)
    """
    alias_lower = alias.lower()
    
    # Chercher d'abord par numéro
    if alias_lower.isdigit():
        num = int(alias_lower)
        for section in sections:
            if section[0] == num:
                return section
        return None
    
    # Chercher par alias prédéfini
    keywords = SECTION_ALIASES.get(alias_lower, [alias_lower])
    
    for section in sections:
        title_lower = section[1].lower()
        for keyword in keywords:
            if keyword.lower() in title_lower:
                return section
    
    return None


def extract_section_content(content: str, start_line: int, end_line: int) -> str:
    """Extrait le contenu d'une section"""
    lines = content.split("\n")
    return "\n".join(lines[start_line:end_line]).strip()


@router.get("/integration", response_class=PlainTextResponse)
async def get_integration_doc(
    section: Optional[str] = Query(
        None, 
        description="Numéro de section (1, 2, 3...) ou alias (principes, config, backend, frontend, m2m, shared, roles, etc.)"
    )
):
    """
    Retourne le guide d'intégration en format Markdown (texte brut).
    
    Endpoint public accessible sans authentification.
    Le fichier INTEGRATION.md est la source unique de vérité.
    
    **Raccourcis disponibles :**
    - Par numéro : `?section=1`, `?section=2`, etc.
    - Par alias : `?section=principes`, `?section=backend`, `?section=m2m`, etc.
    
    **Exemples :**
    ```bash
    # Doc complète
    curl http://hub:8000/api/docs/integration
    
    # Section par numéro
    curl "http://hub:8000/api/docs/integration?section=4"
    
    # Section par alias
    curl "http://hub:8000/api/docs/integration?section=frontend"
    
    # Liste des sections disponibles
    curl http://hub:8000/api/docs/integration/sections
    ```
    """
    if not DOCS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Documentation d'intégration non trouvée. Vérifiez que INTEGRATION.md est présent."
        )
    
    try:
        content = DOCS_PATH.read_text(encoding="utf-8")
        
        # Si aucune section demandée, retourner tout le document
        if not section:
            return content
        
        # Parser les sections du document
        sections = parse_doc_sections(content)
        
        # Trouver la section demandée
        found = find_section_by_alias(sections, section)
        
        if not found:
            # Lister les sections disponibles dans l'erreur
            available_nums = [str(s[0]) for s in sections]
            available_aliases = list(SECTION_ALIASES.keys())
            raise HTTPException(
                status_code=400,
                detail=f"Section '{section}' non trouvée. "
                       f"Numéros disponibles: {', '.join(available_nums)}. "
                       f"Aliases: {', '.join(available_aliases)}"
            )
        
        # Extraire le contenu de la section
        return extract_section_content(content, found[2], found[3])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lecture doc: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture de la documentation: {str(e)}"
        )


@router.get("/integration/sections")
async def get_sections():
    """
    Liste toutes les sections disponibles dans la documentation.
    Extrait dynamiquement depuis INTEGRATION.md.
    
    **Exemple :**
    ```bash
    curl http://hub:8000/api/docs/integration/sections
    ```
    """
    if not DOCS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Documentation d'intégration non trouvée"
        )
    
    try:
        content = DOCS_PATH.read_text(encoding="utf-8")
        sections = parse_doc_sections(content)
        metadata = get_doc_metadata(content)
        
        # Construire la réponse
        sections_list = []
        for num, title, start, end in sections:
            # Trouver les alias correspondants
            aliases = [k for k, v in SECTION_ALIASES.items() 
                      if any(kw.lower() in title.lower() for kw in v)]
            
            sections_list.append({
                "number": num,
                "title": title,
                "aliases": aliases,
                "lines": f"{start+1}-{end}"
            })
        
        return {
            "source": "INTEGRATION.md",
            "version": metadata.get("version"),
            "last_update": metadata.get("date"),
            "total_sections": len(sections_list),
            "sections": sections_list,
            "available_aliases": list(SECTION_ALIASES.keys())
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )


@router.get("/integration/shortcuts", response_class=PlainTextResponse)
async def get_shortcuts():
    """
    Affiche un guide des raccourcis disponibles (généré dynamiquement).
    
    **Exemple :**
    ```bash
    curl http://hub:8000/api/docs/integration/shortcuts
    ```
    """
    if not DOCS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Documentation d'intégration non trouvée"
        )
    
    try:
        content = DOCS_PATH.read_text(encoding="utf-8")
        sections = parse_doc_sections(content)
        metadata = get_doc_metadata(content)
        
        # Construire l'affichage
        version = metadata.get("version", "?")
        date = metadata.get("date", "?")
        
        output = f"""╔══════════════════════════════════════════════════════════════════════════════╗
║         DOCUMENTATION D'INTÉGRATION HUB - v{version:<10}                      ║
║         Source: INTEGRATION.md ({date})                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  SECTIONS DISPONIBLES (extraites dynamiquement) :                            ║
║  ────────────────────────────────────────────────                            ║
"""
        
        for num, title, _, _ in sections:
            # Trouver les alias
            aliases = [k for k, v in SECTION_ALIASES.items() 
                      if any(kw.lower() in title.lower() for kw in v)]
            alias_str = f" ({', '.join(aliases)})" if aliases else ""
            
            # Tronquer le titre si trop long
            display_title = title[:45] + "..." if len(title) > 45 else title
            line = f"║    ?section={num:<3} → {display_title:<48}{alias_str[:10]:<10}║"
            output += line + "\n"
        
        output += f"""║                                                                              ║
║  ALIASES DISPONIBLES :                                                       ║
║  ─────────────────────                                                       ║
"""
        
        for alias in sorted(SECTION_ALIASES.keys()):
            output += f"║    ?section={alias:<15}                                                 ║\n"
        
        output += f"""║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  EXEMPLES :                                                                  ║
║  ──────────                                                                  ║
║    curl http://hub:8000/api/docs/integration              # Doc complète     ║
║    curl "http://hub:8000/api/docs/integration?section=3"  # Par numéro       ║
║    curl "http://hub:8000/api/docs/integration?section=m2m"# Par alias        ║
║    curl http://hub:8000/api/docs/integration/sections     # JSON sections    ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
        return output
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )


@router.get("/integration/version")
async def get_version():
    """
    Retourne la version de la documentation.
    Utile pour les applications clientes qui veulent vérifier les mises à jour.
    
    La version est lue depuis le fichier VERSION (source unique de vérité).
    
    **Exemple :**
    ```bash
    curl http://hub:8000/api/docs/integration/version
    ```
    """
    if not DOCS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Documentation d'intégration non trouvée"
        )
    
    try:
        content = DOCS_PATH.read_text(encoding="utf-8")
        metadata = get_doc_metadata(content)
        sections = parse_doc_sections(content)
        
        # Utiliser la version depuis le fichier VERSION (source unique)
        version = get_hub_version()
        
        return {
            "version": version,
            "last_update": metadata.get("date"),
            "author": metadata.get("author"),
            "total_sections": len(sections),
            "source_file": "VERSION",
            "endpoints": {
                "full_doc": "/api/docs/integration",
                "sections": "/api/docs/integration/sections",
                "shortcuts": "/api/docs/integration/shortcuts",
                "version": "/api/docs/integration/version",
                "changelog": "/api/docs/changelog"
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )


# =============================================================================
# CHANGELOG - Historique des modifications pour les agents IA
# =============================================================================

def parse_changelog_versions(content: str) -> List[Dict]:
    """
    Parse le changelog et extrait toutes les versions avec leurs modifications.
    
    Returns:
        Liste de dicts avec version, date, et contenu
    """
    versions = []
    lines = content.split("\n")
    current_version = None
    current_content = []
    
    for i, line in enumerate(lines):
        # Chercher les versions ## [X.Y.Z] - DATE
        match = re.match(r"^## \[(\d+\.\d+\.\d+)\] - (.+)$", line)
        if match:
            # Sauvegarder la version précédente
            if current_version:
                versions.append({
                    "version": current_version["version"],
                    "date": current_version["date"],
                    "content": "\n".join(current_content).strip(),
                    "line_start": current_version["line_start"],
                    "line_end": i
                })
            
            current_version = {
                "version": match.group(1),
                "date": match.group(2).strip(),
                "line_start": i
            }
            current_content = [line]
        elif current_version:
            current_content.append(line)
    
    # Sauvegarder la dernière version
    if current_version:
        versions.append({
            "version": current_version["version"],
            "date": current_version["date"],
            "content": "\n".join(current_content).strip(),
            "line_start": current_version["line_start"],
            "line_end": len(lines)
        })
    
    return versions


def parse_version_tuple(version: str) -> Tuple[int, int, int]:
    """Convertit une version string en tuple pour comparaison"""
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def extract_version_changes(content: str) -> Dict:
    """
    Extrait les changements structurés d'une version.
    """
    changes = {
        "features": [],
        "docs": [],
        "code": [],
        "database": [],
        "security": [],
        "fixes": [],
        "breaking": [],
        "checklist": []
    }
    
    section_map = {
        "nouvelles fonctionnalités": "features",
        "modifications documentation": "docs",
        "modifications code": "code",
        "modifications base de données": "database",
        "sécurité": "security",
        "corrections": "fixes",
        "breaking changes": "breaking",
        "checklist pour agents ia": "checklist"
    }
    
    current_section = None
    lines = content.split("\n")
    
    for line in lines:
        # Détecter les sections ### 
        if line.startswith("### "):
            section_title = line[4:].lower()
            for key, value in section_map.items():
                if key in section_title:
                    current_section = value
                    break
            else:
                current_section = None
        # Collecter les items (lignes commençant par - ou *)
        elif current_section and (line.strip().startswith("- ") or line.strip().startswith("* ")):
            item = line.strip()[2:].strip()
            if item:
                changes[current_section].append(item)
    
    return changes


@router.get("/changelog", response_class=PlainTextResponse)
async def get_changelog(
    since: Optional[str] = Query(
        None,
        description="Version depuis laquelle afficher les changements (ex: 2.0.0)"
    ),
    latest: bool = Query(
        False,
        description="Afficher uniquement la dernière version"
    ),
    format: str = Query(
        "markdown",
        description="Format de sortie: 'markdown' ou 'json'"
    )
):
    """
    Retourne l'historique des modifications (changelog).
    
    **Pour les agents IA** : Utilisez ce endpoint pour voir les modifications
    depuis votre dernière mise à jour.
    
    **Paramètres :**
    - `since` : Version depuis laquelle voir les changements (ex: `?since=2.0.0`)
    - `latest` : Voir uniquement la dernière version (`?latest=true`)
    - `format` : Format de sortie (`markdown` ou `json`)
    
    **Exemples :**
    ```bash
    # Changelog complet
    curl http://hub:8000/api/docs/changelog
    
    # Changements depuis v2.0.0
    curl "http://hub:8000/api/docs/changelog?since=2.0.0"
    
    # Dernière version uniquement
    curl "http://hub:8000/api/docs/changelog?latest=true"
    
    # Format JSON structuré
    curl "http://hub:8000/api/docs/changelog?format=json"
    ```
    """
    if not CHANGELOG_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Changelog non trouvé. Vérifiez que CHANGELOG.md est présent."
        )
    
    try:
        content = CHANGELOG_PATH.read_text(encoding="utf-8")
        versions = parse_changelog_versions(content)
        
        if not versions:
            raise HTTPException(
                status_code=404,
                detail="Aucune version trouvée dans le changelog"
            )
        
        # Filtrer si 'latest'
        if latest:
            versions = [versions[0]]  # Première version = plus récente
        
        # Filtrer si 'since'
        if since:
            try:
                since_tuple = parse_version_tuple(since)
                versions = [v for v in versions 
                           if parse_version_tuple(v["version"]) > since_tuple]
            except (ValueError, IndexError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Format de version invalide: '{since}'. Utilisez X.Y.Z"
                )
        
        # Format JSON
        if format.lower() == "json":
            result = []
            for v in versions:
                result.append({
                    "version": v["version"],
                    "date": v["date"],
                    "changes": extract_version_changes(v["content"])
                })
            return JSONResponse(content={
                "total_versions": len(result),
                "versions": result
            })
        
        # Format Markdown (par défaut)
        if not versions:
            return "Aucun changement depuis la version spécifiée."
        
        output = "# Changelog - Modifications depuis " + (since or "le début") + "\n\n"
        for v in versions:
            output += v["content"] + "\n\n---\n\n"
        
        return output.strip()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lecture changelog: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )


@router.get("/changelog/versions")
async def get_changelog_versions():
    """
    Liste toutes les versions disponibles dans le changelog.
    
    **Exemple :**
    ```bash
    curl http://hub:8000/api/docs/changelog/versions
    ```
    """
    if not CHANGELOG_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Changelog non trouvé"
        )
    
    try:
        content = CHANGELOG_PATH.read_text(encoding="utf-8")
        versions = parse_changelog_versions(content)
        
        return {
            "total_versions": len(versions),
            "latest_version": versions[0]["version"] if versions else None,
            "versions": [
                {
                    "version": v["version"],
                    "date": v["date"]
                }
                for v in versions
            ]
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )


@router.get("/changelog/diff")
async def get_changelog_diff(
    from_version: str = Query(
        ...,
        description="Version de départ (votre version actuelle)"
    ),
    to_version: Optional[str] = Query(
        None,
        description="Version cible (par défaut: dernière version)"
    )
):
    """
    Compare deux versions et retourne les changements entre elles.
    
    **Pour les agents IA** : Utilisez ce endpoint pour voir exactement
    ce qui a changé entre votre version et la version actuelle.
    
    **Exemple :**
    ```bash
    # Changements de 2.0.0 à maintenant
    curl "http://hub:8000/api/docs/changelog/diff?from_version=2.0.0"
    
    # Changements de 1.0.0 à 2.0.0
    curl "http://hub:8000/api/docs/changelog/diff?from_version=1.0.0&to_version=2.0.0"
    ```
    """
    if not CHANGELOG_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Changelog non trouvé"
        )
    
    try:
        content = CHANGELOG_PATH.read_text(encoding="utf-8")
        versions = parse_changelog_versions(content)
        
        from_tuple = parse_version_tuple(from_version)
        to_tuple = parse_version_tuple(to_version) if to_version else None
        
        # Filtrer les versions dans la plage
        filtered = []
        for v in versions:
            v_tuple = parse_version_tuple(v["version"])
            if v_tuple > from_tuple:
                if to_tuple is None or v_tuple <= to_tuple:
                    filtered.append(v)
        
        if not filtered:
            return {
                "message": "Vous êtes à jour !",
                "from_version": from_version,
                "to_version": to_version or versions[0]["version"],
                "changes_count": 0,
                "versions": []
            }
        
        # Extraire les changements de chaque version
        all_changes = {
            "features": [],
            "docs": [],
            "code": [],
            "database": [],
            "security": [],
            "fixes": [],
            "breaking": [],
            "checklist": []
        }
        
        for v in filtered:
            changes = extract_version_changes(v["content"])
            for key in all_changes:
                for item in changes[key]:
                    all_changes[key].append(f"[{v['version']}] {item}")
        
        return {
            "from_version": from_version,
            "to_version": to_version or versions[0]["version"],
            "versions_included": [v["version"] for v in filtered],
            "changes_count": sum(len(v) for v in all_changes.values()),
            "summary": {
                "new_features": len(all_changes["features"]),
                "doc_changes": len(all_changes["docs"]),
                "code_changes": len(all_changes["code"]),
                "db_changes": len(all_changes["database"]),
                "security_changes": len(all_changes["security"]),
                "bug_fixes": len(all_changes["fixes"]),
                "breaking_changes": len(all_changes["breaking"]),
                "checklist_items": len(all_changes["checklist"])
            },
            "changes": all_changes,
            "action_required": len(all_changes["breaking"]) > 0 or len(all_changes["checklist"]) > 0
        }
        
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Format de version invalide. Utilisez X.Y.Z"
        )
    except Exception as e:
        logger.error(f"Erreur diff changelog: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur: {str(e)}"
        )
