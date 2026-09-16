"""
Vérification de syntaxe des fichiers JavaScript du frontend.

Régression : le commit f25628b (retrait Droits Philia) a supprimé par erreur
l'accolade fermante de `class HubAPI` dans frontend/public/js/api.js. Résultat :
SyntaxError au chargement → objet `api` jamais défini → crash de init.js après le
health-check du badge → écran de chargement infini (ni login, ni dashboard),
sans aucune erreur backend. Aucun test ne couvrait la syntaxe JS : d'où celui-ci.

Stratégie : `node --check` (parse complet, zéro faux positif). Sans Node.js le
test est ignoré — un mini-lexer Python donnerait des faux positifs sur les
regex literals contenant des quotes (ex: /"/g), on ne le fait pas passer pour
un garde-fou.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
JS_DIR = REPO_ROOT / "frontend" / "public" / "js"


def _collect_js_files():
    if not JS_DIR.is_dir():
        pytest.skip(f"dossier introuvable: {JS_DIR}")
    files = sorted(JS_DIR.rglob("*.js"))
    assert files, f"aucun .js trouvé sous {JS_DIR}"
    return files


def _syntax_errors(path: Path) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js indisponible : vérification de syntaxe JS ignorée")
    proc = subprocess.run(
        [node, "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return []
    first = next(
        (l for l in proc.stderr.splitlines() if "SyntaxError" in l or "Error" in l),
        proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "erreur inconnue",
    )
    return [first]


@pytest.mark.unit
@pytest.mark.parametrize("js_file", _collect_js_files(), ids=lambda p: p.name)
def test_frontend_js_syntax(js_file):
    """Chaque .js du frontend doit être syntaxiquement valide."""
    errors = _syntax_errors(js_file)
    assert not errors, f"{js_file.relative_to(REPO_ROOT)}: {errors}"
