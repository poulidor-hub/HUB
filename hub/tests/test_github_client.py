# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du client GitHub factorisé (Task 2)."""
import pytest

from app.github_client import parse_github_url, slugify_repo


class TestParseGithubUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/MYCOMPANY/thetipitap.git", ("MYCOMPANY", "thetipitap")),
        ("https://github.com/MYCOMPANY/thetipitap", ("MYCOMPANY", "thetipitap")),
        ("git@github.com:MYCOMPANY/thetipitap.git", ("MYCOMPANY", "thetipitap")),
        ("https://gitlab.com/x/y", None),  # pas github
        ("", None),
        (None, None),
    ])
    def test_parse(self, url, expected):
        assert parse_github_url(url) == expected


class TestSlugifyRepo:
    @pytest.mark.parametrize("name,expected", [
        ("thetipitap", "thetipitap"),
        ("Mon-Projet_Cool", "mon-projet-cool"),
        ("App v2.0!", "app-v2-0"),
        ("  --spaces--  ", "spaces"),
        ("café", "cafe"),  # accents retirés
    ])
    def test_slugify(self, name, expected):
        assert slugify_repo(name) == expected
        # doit matcher le pattern de slug du HUB
        import re
        assert re.match(r'^[a-z0-9-]+$', slugify_repo(name))


class TestNormalizeGithubUrl:
    """normalize_github_url : URLs SSH converties en HTTPS (le PAT ne sert
    qu'en HTTPS ; le conteneur hub n'a pas de binaire ssh — bug 2026-10-03,
    4 échecs de déploiement d'une application à l'étape clone)."""

    @pytest.mark.parametrize("url,expected", [
        # scp-like GitHub
        ("git@github.com:MYCOMPANY/thetipitap.git",
         "https://github.com/MYCOMPANY/thetipitap.git"),
        # scp-like sans .git
        ("git@github.com:MYCOMPANY/thetipitap",
         "https://github.com/MYCOMPANY/thetipitap.git"),
        # ssh:// explicite
        ("ssh://git@github.com/MYCOMPANY/thetipitap.git",
         "https://github.com/MYCOMPANY/thetipitap.git"),
        ("ssh://git@gitlab.com/group/projet.git",
         "https://gitlab.com/group/projet.git"),
        # HTTPS : inchangé
        ("https://github.com/MYCOMPANY/thetipitap.git",
         "https://github.com/MYCOMPANY/thetipitap.git"),
        ("https://github.com/MYCOMPANY/thetipitap",
         "https://github.com/MYCOMPANY/thetipitap"),
        # non-git / vide / None : rendus tels quels
        ("", ""),
        (None, None),
        ("not a url", "not a url"),
    ])
    def test_normalize(self, url, expected):
        from app.github_client import normalize_github_url
        assert normalize_github_url(url) == expected
