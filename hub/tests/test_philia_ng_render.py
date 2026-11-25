# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Vhost PhiliaNG : le server_name suit PHILIA_NG_DOMAIN.

frontend/nginx-philia-ng.conf portait son server_name en dur : PHILIA_NG_DOMAIN
ne pilotait que le certificat, et corriger le fichier suivi par git à la main
était écrasé à la mise à jour suivante. Le modèle porte désormais un jeton
__PHILIA_NG_DOMAIN__, remplacé au démarrage du hub-frontend par
frontend/render-philia-conf.sh (appelé depuis docker-entrypoint.sh).

Le rendu ne touche qu'à ce jeton : les variables nginx ($host, $request_uri,
$remote_addr...) doivent survivre — envsubst les détruirait.

HOST_ONLY : frontend/ n'est pas dans l'image de test (make test-docker).
"""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HUB_BASE = Path(__file__).resolve().parents[2]
FRONTEND = HUB_BASE / "frontend"
RENDER = FRONTEND / "render-philia-conf.sh"

if not (FRONTEND / "docker-entrypoint.sh").is_file():
    pytest.skip("fichiers checkout hors image — run hôte", allow_module_level=True)

JETON = "__PHILIA_NG_DOMAIN__"

# Modèle réduit : les variables nginx des deux blocs du vhost réel, et un
# second jeton qui ne doit pas être touché.
MODELE = """\
server {
    listen 80;
    server_name __PHILIA_NG_DOMAIN__;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name __PHILIA_NG_DOMAIN__;
    # __AUTRE_JETON__ doit survivre au rendu
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass https://host.docker.internal:4443;
}
"""


@pytest.fixture
def travail(tmp_path: Path) -> Path:
    """Dossier vierge : le modèle y est écrit, `sortie/` y est vide."""
    dossier = tmp_path / "travail"
    (dossier / "sortie").mkdir(parents=True)
    return dossier


def _rendre(travail: Path, domaine: str, modele: str | None = MODELE,
            sortie: Path | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Lance le script de rendu ; `modele=None` = modèle absent."""
    fichier_modele = travail / "modele.conf"
    if modele is not None:
        fichier_modele.write_text(modele)
    if sortie is None:
        sortie = travail / "sortie" / "philia-ng.conf"
    proc = subprocess.run(
        ["sh", str(RENDER), str(fichier_modele), str(sortie), domaine],
        capture_output=True, text=True, timeout=30,
    )
    return proc, sortie


def _server_names(texte: str) -> list[str]:
    return re.findall(r"^\s*server_name\s+([^;]+);", texte, flags=re.M)


def _variables_nginx(texte: str) -> list[str]:
    return re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*", texte)


# ---------------------------------------------------------------- rendu


def test_render_pose_le_domaine_aux_deux_server_name(travail: Path):
    proc, sortie = _rendre(travail, "philia.test.lan")
    assert proc.returncode == 0, proc.stderr
    assert _server_names(sortie.read_text()) == ["philia.test.lan", "philia.test.lan"], (
        "les deux server_name du vhost rendu doivent porter le domaine"
    )


def test_render_ne_laisse_aucun_jeton(travail: Path):
    proc, sortie = _rendre(travail, "philia.test.lan")
    assert proc.returncode == 0, proc.stderr
    assert JETON not in sortie.read_text(), "le jeton ne doit pas subsister dans le vhost rendu"


def test_render_preserve_les_variables_nginx(travail: Path):
    proc, sortie = _rendre(travail, "philia.test.lan")
    assert proc.returncode == 0, proc.stderr
    assert _variables_nginx(MODELE), "le modèle de test doit contenir des variables nginx"
    assert _variables_nginx(sortie.read_text()) == _variables_nginx(MODELE), (
        "les variables nginx ($host, $request_uri, $remote_addr...) doivent survivre intactes"
    )


def test_render_ne_remplace_que_le_jeton_du_domaine(travail: Path):
    proc, sortie = _rendre(travail, "philia.test.lan")
    assert proc.returncode == 0, proc.stderr
    assert "__AUTRE_JETON__" in sortie.read_text(), "seul le jeton du domaine doit être remplacé"


def test_render_n_utilise_pas_envsubst():
    """envsubst substitue toutes les variables $… : nginx perdrait les siennes."""
    code = [l for l in RENDER.read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not any("envsubst" in l for l in code), "le rendu ne doit pas passer par envsubst"


# ---------------------------------------------------------------- refus


def test_render_refuse_un_domaine_vide(travail: Path):
    proc, sortie = _rendre(travail, "")
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    assert sorted(p.name for p in sortie.parent.iterdir()) == [], (
        "aucun fichier, pas même temporaire, ne doit être écrit"
    )


@pytest.mark.parametrize("domaine", [
    pytest.param("proxy test.lan", id="espace"),
    pytest.param("proxy.lan\nserver_name autre", id="saut-de-ligne"),
    pytest.param("proxy;test", id="point-virgule"),
    pytest.param("proxy/test", id="slash"),
    pytest.param("proxy&test", id="esperluette"),
    pytest.param("proxy\\test", id="backslash"),
    pytest.param("proxy$host", id="dollar"),
    pytest.param("proxy_test", id="souligne"),
    pytest.param("proxé.lan", id="non-ascii"),
])
def test_render_refuse_un_domaine_invalide(travail: Path, domaine: str):
    proc, sortie = _rendre(travail, domaine)
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    assert sorted(p.name for p in sortie.parent.iterdir()) == [], (
        "aucun fichier, pas même temporaire, ne doit être écrit"
    )
    assert "refus" in proc.stderr, "le refus doit être explicité sur stderr"


def test_render_refuse_un_domaine_absent(travail: Path):
    """Deux arguments au lieu de trois : pas de domaine, donc rien à rendre."""
    proc = subprocess.run(
        ["sh", str(RENDER), str(travail / "modele.conf"), str(travail / "sortie" / "philia-ng.conf")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    assert list((travail / "sortie").iterdir()) == []


# ---------------------------------------------------------------- échec


def test_render_en_echec_laisse_la_sortie_existante_intacte(travail: Path):
    """Modèle absent : code 1, l'ancien vhost reste en place, pas de résidu."""
    sortie = travail / "sortie" / "philia-ng.conf"
    sortie.write_text("ANCIEN VHOST\n")
    proc, sortie = _rendre(travail, "philia.test.lan", modele=None)
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    assert sortie.read_text() == "ANCIEN VHOST\n", "une sortie à moitié rendue ne doit pas remplacer l'ancienne"
    assert [p.name for p in sortie.parent.iterdir()] == ["philia-ng.conf"], (
        "aucun fichier temporaire ne doit rester derrière"
    )
    assert "modele.conf" in proc.stderr, "la cause de l'échec doit être nommée"


def test_render_en_echec_ne_cree_rien(travail: Path, tmp_path: Path):
    """Dossier de sortie inexistant : code 1, et il n'apparaît pas."""
    ailleurs = tmp_path / "ailleurs" / "philia-ng.conf"
    proc, _ = _rendre(travail, "philia.test.lan", sortie=ailleurs)
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    assert not ailleurs.parent.exists()
