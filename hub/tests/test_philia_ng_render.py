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

import os
import re
import shutil
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


# ---------------------------------------------------------------- branchement

MODELE_REEL = FRONTEND / "nginx-philia-ng.conf"
ENTRYPOINT = FRONTEND / "docker-entrypoint.sh"
DOCKERFILE = FRONTEND / "Dockerfile"
COMPOSE = HUB_BASE / "docker-compose.yml"

NGINX_LANCE = 99  # code du bouchon remplaçant « exec nginx » : atteint = démarrage autorisé


def _script_de_rendu() -> str:
    """Chemin du script de rendu tel que l'entrypoint l'appelle dans l'image."""
    chemins = set(re.findall(r"(/\S*render-philia-conf\.sh)", ENTRYPOINT.read_text()))
    assert len(chemins) == 1, "l'entrypoint doit appeler render-philia-conf.sh"
    return chemins.pop()


def _lancer_entrypoint(travail: Path, certificats_philia: bool,
                       domaine: str | None) -> tuple[subprocess.CompletedProcess, Path]:
    """Exécute l'entrypoint réel, ses chemins /etc/nginx/ redirigés vers `travail`.

    Trois remplacements, rien de plus : la racine nginx, le chemin du script
    de rendu (ramené à celui du checkout) et le « exec nginx » final, changé
    en « exit 99 ». `domaine=None` laisse PHILIA_NG_DOMAIN hors de l'environnement.
    """
    nginx = travail / "etc" / "nginx"
    (nginx / "templates").mkdir(parents=True)
    conf_d = nginx / "conf.d"
    conf_d.mkdir()
    for modele in ("nginx-ssl.conf", "nginx-philia-ng.conf"):
        shutil.copy(FRONTEND / modele, nginx / "templates" / modele)
    # L'entrypoint ne teste que la présence des certificats : des fichiers vides suffisent.
    certificats = ("hub", "philia-ng") if certificats_philia else ("hub",)
    for service in certificats:
        dossier = nginx / "ssl" / "server" / service
        dossier.mkdir(parents=True)
        (dossier / "server.crt").touch()
        (dossier / "server.key").touch()

    texte = ENTRYPOINT.read_text()
    texte = texte.replace("/etc/nginx/", f"{nginx}/")
    texte = texte.replace(_script_de_rendu(), str(RENDER))
    texte, remplaces = re.subn(r"^exec nginx .*$", f"exit {NGINX_LANCE}", texte, flags=re.M)
    assert remplaces == 1, "l'entrypoint doit se terminer par « exec nginx » (remplacé par un bouchon)"
    bac = travail / "docker-entrypoint.sh"
    bac.write_text(texte)

    proc = subprocess.run(
        ["sh", str(bac)], capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             **({"PHILIA_NG_DOMAIN": domaine} if domaine is not None else {})},
    )
    return proc, conf_d


def test_modele_porte_le_jeton_aux_deux_server_names():
    """Le vhost suivi par git n'a plus de nom en dur : ses deux server_name sont le jeton."""
    assert _server_names(MODELE_REEL.read_text()) == [JETON, JETON], (
        "les deux server_name du modèle doivent porter le jeton __PHILIA_NG_DOMAIN__"
    )


def test_entrypoint_rend_le_vhost_avec_philia_ng_domain(travail: Path):
    """Bout en bout : l'entrypoint réel, le script et le modèle du dépôt."""
    proc, conf_d = _lancer_entrypoint(travail, certificats_philia=True, domaine="philia.test.lan")
    assert proc.returncode == NGINX_LANCE, proc.stdout + proc.stderr
    assert _server_names((conf_d / "philia-ng.conf").read_text()) == ["philia.test.lan"] * 2, (
        "le vhost installé par l'entrypoint doit porter PHILIA_NG_DOMAIN"
    )


def test_entrypoint_rend_le_vhost_avec_le_domaine_par_defaut(travail: Path):
    proc, conf_d = _lancer_entrypoint(travail, certificats_philia=True, domaine=None)
    assert proc.returncode == NGINX_LANCE, proc.stdout + proc.stderr
    assert _server_names((conf_d / "philia-ng.conf").read_text()) == ["proxy.example.lan"] * 2, (
        "sans PHILIA_NG_DOMAIN, le vhost doit porter proxy.example.lan"
    )


def test_entrypoint_annule_le_demarrage_si_le_domaine_est_refuse(travail: Path):
    proc, conf_d = _lancer_entrypoint(travail, certificats_philia=True, domaine="proxy test.lan")
    assert proc.returncode != NGINX_LANCE, "Nginx ne doit pas être lancé sur un rendu refusé"
    assert proc.returncode == 1, f"code {proc.returncode} — {proc.stdout}{proc.stderr}"
    signaux = [l for l in proc.stderr.splitlines() if "PhiliaNG" in l and RENDER.name not in l]
    assert signaux, "l'entrypoint lui-même doit signaler l'échec, pas seulement le script"
    assert "PHILIA_NG_DOMAIN" in signaux[0], "le signal doit nommer la variable à corriger"
    residus = [p.name for p in conf_d.iterdir() if "philia" in p.name]
    assert residus == [], f"aucun vhost PhiliaNG, pas même à moitié rendu, ne doit rester dans conf.d : {residus}"


def test_entrypoint_sans_certificats_philia_ng_n_inclut_aucun_vhost(travail: Path):
    """Garde-fou : l'inclusion reste conditionnelle aux certificats, le rendu aussi."""
    proc, conf_d = _lancer_entrypoint(travail, certificats_philia=False, domaine="philia.test.lan")
    assert proc.returncode == NGINX_LANCE, proc.stdout + proc.stderr
    assert "philia-ng.conf" not in [p.name for p in conf_d.iterdir()]


def test_image_livre_le_script_de_rendu():
    """Sans COPY ni chmod, l'entrypoint échouerait au démarrage du conteneur."""
    cible = _script_de_rendu()
    dockerfile = DOCKERFILE.read_text().splitlines()
    copies = [l.split()[-1] for l in dockerfile if l.startswith("COPY ") and RENDER.name in l]
    assert cible in copies, f"le Dockerfile doit copier {RENDER.name} vers {cible}"
    chmods = " ".join(l for l in dockerfile if l.startswith("RUN chmod"))
    assert cible in chmods.split(), f"{cible} doit être rendu exécutable (l'image tourne sous nginx)"


def _environnement(bloc: str) -> dict[str, str]:
    """Entrées `KEY=value` de la section `environment:` d'un bloc de service."""
    entrees: dict[str, str] = {}
    dans_env = False
    for ligne in bloc.splitlines():
        if re.match(r"^    environment:\s*$", ligne):
            dans_env = True
        elif dans_env:
            m = re.match(r"^      - ([A-Z0-9_]+)=(.*)$", ligne)
            if m:
                entrees[m.group(1)] = m.group(2)
            elif ligne.strip() and not ligne.startswith("      #"):
                break
    return entrees


def test_compose_passe_philia_ng_domain_au_hub_frontend():
    from tests.test_compose_socket_proxy import _service_blocks

    blocs = _service_blocks(COMPOSE.read_text())
    env_frontend = _environnement(blocs["hub-frontend"])
    assert "PHILIA_NG_DOMAIN" in env_frontend, (
        "hub-frontend doit recevoir PHILIA_NG_DOMAIN : c'est lui qui rend le server_name"
    )
    assert env_frontend["PHILIA_NG_DOMAIN"] == "${PHILIA_NG_DOMAIN:-proxy.example.lan}"
    assert env_frontend["PHILIA_NG_DOMAIN"] == _environnement(blocs["hub-api"])["PHILIA_NG_DOMAIN"], (
        "même expression que hub-api : le certificat est émis pour ce nom"
    )


@pytest.mark.parametrize("fichier", ["docker-compose.yml", ".env.example", "hub/entrypoint.sh"])
def test_plus_de_correspondance_manuelle_du_server_name(fichier: str):
    """Le server_name suit PHILIA_NG_DOMAIN : « doit matcher le server_name » est faux."""
    assert "server_name de frontend/nginx-philia-ng.conf" not in (HUB_BASE / fichier).read_text(), (
        f"{fichier} demande encore une correspondance manuelle avec le vhost"
    )
