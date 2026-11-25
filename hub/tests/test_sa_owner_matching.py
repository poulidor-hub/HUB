# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Fix heuristique SA propriétaire — égalité exacte, plus de sous-chaîne.

Incidents réels :
- incident (banc) : le SA « SA - Philia Proxy » livré à Exemple App 8 ;
- 15/11/2026 (V4) : les registers parallèles de la famille « Exemple »
  se sont sabordés — le register de `exemple` a roté le
  secret de « SA - Exemple Paie » (app `exemple-paie`) car l'heuristique
  testait `app_slug in sa_name_lower` (sous-chaîne) : 'exemple' matchait
  « SA - Exemple Paie ». Secret livré à la mauvaise stack → 401 en boucle
  côté exemple-paie (fenêtre H9 sans issue self-service).

Contrat : un SA est « propriétaire » d'une app ssi son nom correspond
EXACTEMENT à l'une des formes canoniques (insensibles casse/espaces) :
  1. « SA - <App Name> » (convention posée à l'auto-registration) ;
  2. le slug pur de l'app (SA nommé directement par le slug) ;
  3. forme slug-normalisée de (1) (tirets/espaces équivalents).
AUCUN matching par sous-chaîne : les familles d'apps aux noms préfixés
(exemple/exemple-paie/exemple-pec, autre-app-*,
tiers-app-*) ne doivent jamais se voler leurs secrets entre elles.
"""
from types import SimpleNamespace

from app.routes.apps import (
    _is_owner_service_account,
    generate_slug,
    is_owner_service_account,
)


def _sa(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class TestIsOwnerServiceAccount:
    """Helper historique (sa, app_slug, app_name) — register/rotation."""

    def test_bug_base_ne_possede_pas_le_sa_de_sa_derivee(self):
        # LE bug V4 : 'exemple' ⊂ "SA - Exemple Paie" (sous-chaîne)
        sa = _sa("SA - Exemple Paie")
        assert is_owner_service_account(sa, "exemple", "Exemple") is False

    def test_bug_base_ne_possede_pas_le_sa_de_sa_derivee_pec(self):
        sa = _sa("SA - Exemple PEC")
        assert is_owner_service_account(sa, "exemple", "Exemple") is False

    def test_canonical_exact_match(self):
        sa = _sa("SA - Exemple")
        assert is_owner_service_account(sa, "exemple", "Exemple") is True

    def test_canonical_par_nom_d_app_famille(self):
        sa = _sa("SA - Exemple Paie")
        assert is_owner_service_account(sa, "exemple-paie", "Exemple Paie") is True

    def test_sa_nomme_par_le_slug(self):
        sa = _sa("exemple-paie")
        assert is_owner_service_account(sa, "exemple-paie", "Exemple Paie") is True

    def test_slug_d_une_autre_app_famille(self):
        sa = _sa("exemple")
        assert is_owner_service_account(sa, "exemple-paie", "Exemple Paie") is False

    def test_casse_et_espaces_tolerance(self):
        sa = _sa("sa  -  exemple paie")
        assert is_owner_service_account(sa, "exemple-paie", "Exemple Paie") is True

    def test_sa_hors_famille_ne_matche_pas(self):
        # Récidive historique documentée (banc)
        sa = _sa("SA - Philia Proxy")
        assert is_owner_service_account(sa, "exemple-app-8", "Exemple App 8") is False


class TestIsOwnerServiceAccountAppObject:
    """Variante (app, sa) — partage/services."""

    def _app(self, slug: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(slug=slug, name=name)

    def test_bug_famille_prefigee(self):
        app = self._app("exemple", "Exemple")
        assert _is_owner_service_account(app, _sa("SA - Exemple Paie")) is False

    def test_proprietaire_canonical(self):
        app = self._app("exemple-paie", "Exemple Paie")
        assert _is_owner_service_account(app, _sa("SA - Exemple Paie")) is True


class TestGenerateSlug:
    def test_slug_normalise(self):
        assert generate_slug("SA - Exemple Paie") == "sa-exemple-paie"

    def test_slug_sans_nom(self):
        assert generate_slug("exemple") == "exemple"
