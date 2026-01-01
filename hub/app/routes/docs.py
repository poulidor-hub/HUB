"""
Routes pour exposer la documentation du Hub
"""
import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pathlib import Path

router = APIRouter(prefix="/api/docs", tags=["documentation"])

# Chemin vers le fichier INTEGRATION.md (depuis le conteneur Docker)
DOCS_PATH = Path("/code/INTEGRATION.md")

# Mapping des shortcuts vers les titres de sections
SECTION_SHORTCUTS = {
    "1": "1. Principes fondamentaux",
    "2": "2. Configuration de l'application",
    "3": "3. Intégration Backend (FastAPI)",
    "4": "4. Intégration Frontend",
    "5": "5. Communication Machine-to-Machine (M2M)",
    "6": "6. Communication inter-API",
    "7": "7. Gestion des rôles",
    "8": "8. Endpoints du Hub",
    "9": "9. Configuration du Dockerfile",
    "10": "10. Résumé",
    "11": "11. Contact Hub",
    # Aliases courts
    "principes": "1. Principes fondamentaux",
    "config": "2. Configuration de l'application",
    "backend": "3. Intégration Backend (FastAPI)",
    "frontend": "4. Intégration Frontend",
    "m2m": "5. Communication Machine-to-Machine (M2M)",
    "inter-api": "6. Communication inter-API",
    "roles": "7. Gestion des rôles",
    "endpoints": "8. Endpoints du Hub",
    "docker": "9. Configuration du Dockerfile",
    "resume": "10. Résumé",
    "contact": "11. Contact Hub",
}


def extract_section(content: str, section_title: str) -> Optional[str]:
    """Extrait une section spécifique du document Markdown."""
    # Pattern pour trouver le début de la section (## suivi du titre)
    # On cherche le numéro au début du titre
    section_num = section_title.split(".")[0] if "." in section_title else None
    
    if section_num:
        # Chercher "## {num}." pour matcher la section
        pattern = rf"^## {re.escape(section_num)}\."
    else:
        pattern = rf"^## {re.escape(section_title)}"
    
    lines = content.split("\n")
    start_idx = None
    end_idx = None
    
    for i, line in enumerate(lines):
        if start_idx is None:
            if re.match(pattern, line, re.IGNORECASE):
                start_idx = i
        elif start_idx is not None:
            # Chercher la prochaine section de niveau 2 (##)
            if re.match(r"^## \d+\.", line):
                end_idx = i
                break
    
    if start_idx is None:
        return None
    
    if end_idx is None:
        end_idx = len(lines)
    
    return "\n".join(lines[start_idx:end_idx]).strip()


@router.get("/integration", response_class=PlainTextResponse)
async def get_integration_doc(
    section: Optional[str] = Query(
        None, 
        description="Shortcut de section (1-11 ou alias: principes, config, backend, frontend, m2m, inter-api, roles, endpoints, docker, resume, contact)"
    )
):
    """
    Retourne le guide d'intégration en format Markdown (texte brut).
    
    Endpoint public accessible sans authentification.
    Permet aux autres applications de consulter la dernière version
    de la documentation d'intégration avec le Hub.
    
    **Shortcuts disponibles :**
    - `?section=1` à `?section=11` : Sections par numéro
    - `?section=principes` : Section 1 - Principes fondamentaux
    - `?section=config` : Section 2 - Configuration
    - `?section=backend` : Section 3 - Intégration Backend
    - `?section=frontend` : Section 4 - Intégration Frontend
    - `?section=m2m` : Section 5 - Communication M2M
    - `?section=inter-api` : Section 6 - Communication inter-API
    - `?section=roles` : Section 7 - Gestion des rôles
    - `?section=endpoints` : Section 8 - Endpoints du Hub
    - `?section=docker` : Section 9 - Configuration Dockerfile
    - `?section=resume` : Section 10 - Résumé
    - `?section=contact` : Section 11 - Contact
    
    **Exemples :**
    ```bash
    # Doc complète
    curl http://hub:8000/api/docs/integration
    
    # Section frontend uniquement
    curl "http://hub:8000/api/docs/integration?section=frontend"
    
    # Section par numéro
    curl "http://hub:8000/api/docs/integration?section=4"
    
    # Liste des shortcuts
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
        
        # Si une section est demandée, extraire uniquement cette section
        if section:
            section_lower = section.lower().strip()
            section_title = SECTION_SHORTCUTS.get(section_lower)
            
            if not section_title:
                available = ", ".join(sorted(SECTION_SHORTCUTS.keys()))
                raise HTTPException(
                    status_code=400,
                    detail=f"Section inconnue: '{section}'. Shortcuts disponibles: {available}"
                )
            
            extracted = extract_section(content, section_title)
            if not extracted:
                raise HTTPException(
                    status_code=404,
                    detail=f"Section '{section_title}' non trouvée dans le document"
                )
            
            return extracted
        
        return content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la lecture de la documentation: {str(e)}"
        )


@router.get("/integration/shortcuts", response_class=PlainTextResponse)
async def get_shortcuts():
    """
    Liste tous les shortcuts disponibles pour naviguer dans la documentation.
    
    **Exemple :**
    ```bash
    curl http://hub:8000/api/docs/integration/shortcuts
    ```
    """
    output = """╔══════════════════════════════════════════════════════════════════════════════╗
║              SHORTCUTS - Documentation d'Intégration Hub                     ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Par numéro :                                                                ║
║  ───────────                                                                 ║
║    ?section=1   → Principes fondamentaux                                     ║
║    ?section=2   → Configuration de l'application                             ║
║    ?section=3   → Intégration Backend (FastAPI)                              ║
║    ?section=4   → Intégration Frontend                                       ║
║    ?section=5   → Communication Machine-to-Machine (M2M)                     ║
║    ?section=6   → Communication inter-API                                    ║
║    ?section=7   → Gestion des rôles                                          ║
║    ?section=8   → Endpoints du Hub                                           ║
║    ?section=9   → Configuration du Dockerfile                                ║
║    ?section=10  → Résumé                                                     ║
║    ?section=11  → Contact Hub                                                ║
║                                                                              ║
║  Par alias :                                                                 ║
║  ──────────                                                                  ║
║    ?section=principes  → Section 1                                           ║
║    ?section=config     → Section 2                                           ║
║    ?section=backend    → Section 3                                           ║
║    ?section=frontend   → Section 4                                           ║
║    ?section=m2m        → Section 5                                           ║
║    ?section=inter-api  → Section 6                                           ║
║    ?section=roles      → Section 7                                           ║
║    ?section=endpoints  → Section 8                                           ║
║    ?section=docker     → Section 9                                           ║
║    ?section=resume     → Section 10                                          ║
║    ?section=contact    → Section 11                                          ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Exemples curl :                                                             ║
║  ───────────────                                                             ║
║    curl http://hub:8000/api/docs/integration                    # Doc complète║
║    curl "http://hub:8000/api/docs/integration?section=frontend" # Section 4  ║
║    curl "http://hub:8000/api/docs/integration?section=4"        # Section 4  ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    return output
