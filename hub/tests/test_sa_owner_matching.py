# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Fix heuristique SA propriétaire — égalité exacte, plus de sous-chaîne.

Incidents réels :
- AN-? (banc) : le SA « SA - Philia Proxy » livré à exemple-app-7 ;
- 15/11/2026 (migration flotte, V4) : les registers parallèles de la famille
  « transmission » se sont sabordés — le register de `transmission` a roté le
  secret de « SA - Transmission Paie » (app `exemple-app`) car l'heuristique
  testait `app_slug in sa_name_lower` (sous-chaîne) : 'transmission' matchait
  « SA - Transmission Paie ». Secret livré à la mauvaise stack → 401 en boucle
  côté exemple-app (fenêtre H9 sans issue self-service).

Contrat : un SA est « propriétaire » d'une app ssi son nom correspond
EXACTEMENT à l'une des formes canoniques (insensibles casse/espaces) :
  1. « SA - <App Name> » (convention posée à l'auto-registration) ;
  2. le slug pur de l'app (SA nommé directement par le slug) ;
  3. forme slug-normalisée de (1) (tirets/espaces équivalents).
AUCUN matching par sous-chaîne : les familles d'apps aux noms préfixés
(transmission/exemple-app/exemple-app, dipec-*, contrat-*) ne doivent jamais
se voler leurs secrets entre elles.
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

    def test_bug_transmission_ne_possede_pas_le_sa_de_trans_paie(self):
        # LE bug V4 : 'transmission' ⊂ "SA - Transmission Paie" (sous-chaîne)
        sa = _sa("SA - Transmission Paie")
        assert is_owner_service_account(sa, "transmission", "Transmission") is False

    def test_bug_transmission_ne_possede_pas_le_sa_de_trans_pec(self):
        sa = _sa("SA - exemple-app")
        assert is_owner_service_account(sa, "transmission", "Transmission") is False

    def test_canonical_exact_match(self):
        sa = _sa("SA - Transmission")
        assert is_owner_service_account(sa, "transmission", "Transmission") is True

    def test_canonical_par_nom_d_app_famille(self):
        sa = _sa("SA - Transmission Paie")
        assert is_owner_service_account(sa, "exemple-app", "Transmission Paie") is True

    def test_sa_nomme_par_le_slug(self):
        sa = _sa("exemple-app")
        assert is_owner_service_account(sa, "exemple-app", "Transmission Paie") is True

    def test_slug_d_une_autre_app_famille(self):
        sa = _sa("transmission")
        assert is_owner_service_account(sa, "exemple-app", "Transmission Paie") is False

    def test_casse_et_espaces_tolerance(self):
        sa = _sa("sa  -  transmission paie")
        assert is_owner_service_account(sa, "exemple-app", "Transmission Paie") is True

    def test_philia_proxy_ne_matche_pas_attribution_bal(self):
        # Récidive historique documentée (banc)
        sa = _sa("SA - Philia Proxy")
        assert is_owner_service_account(sa, "exemple-app", "exemple-app-7") is False


class TestIsOwnerServiceAccountAppObject:
    """Variante (app, sa) — partage/services."""

    def _app(self, slug: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(slug=slug, name=name)

    def test_bug_famille_transmission(self):
        app = self._app("transmission", "Transmission")
        assert _is_owner_service_account(app, _sa("SA - Transmission Paie")) is False

    def test_proprietaire_canonical(self):
        app = self._app("exemple-app", "Transmission Paie")
        assert _is_owner_service_account(app, _sa("SA - Transmission Paie")) is True


class TestGenerateSlug:
    def test_slug_normalise(self):
        assert generate_slug("SA - Transmission Paie") == "sa-exemple-app"

    def test_slug_sans_nom(self):
        assert generate_slug("transmission") == "transmission"
