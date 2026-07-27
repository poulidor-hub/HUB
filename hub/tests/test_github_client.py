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
