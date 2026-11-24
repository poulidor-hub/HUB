# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Validateur d'hôte unique : app.host_validation.validate_host.

Un hôte accepté alimente `f"http://{host}:{port}"`, réécrit tel quel dans une
directive `proxy_pass` de la configuration nginx générée (routing_service) :
le moindre caractère de syntaxe (retour à la ligne, `;`, `{`…) y injecte des
directives. Ne passent donc qu'une IPv4, un nom d'hôte (labels RFC 1123) ou
un littéral IPv6 ENTRE CROCHETS — un IPv6 nu casserait l'URL construite.
"""

import pytest

from app.host_validation import validate_host

# Charge utile d'injection : ferme le bloc server, ouvre un location qui
# proxifie vers le socket Docker du Hub.
INJECTION_PAYLOAD = (
    "evil:8080;\n}\nlocation /x { proxy_pass http://docker-socket-proxy:2375"
)

# 63 + 1 + 63 + 1 + 63 + 1 + 61 : borne haute exacte (253 caractères)
_HOST_253 = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])


# ---------------------------------------------------------------- acceptés


@pytest.mark.parametrize("value, expected", [
    pytest.param("192.168.1.200", "192.168.1.200", id="ipv4"),
    pytest.param("127.0.0.1", "127.0.0.1", id="ipv4-loopback"),
    pytest.param("localhost", "localhost", id="nom-1-label"),
    pytest.param("hub.exemple.lan", "hub.exemple.lan", id="nom-domaine"),
    pytest.param("Mon-Serveur.LAN", "Mon-Serveur.LAN", id="casse-conservee"),
    pytest.param("a" * 63, "a" * 63, id="label-63-caracteres"),
    pytest.param(_HOST_253, _HOST_253, id="longueur-253"),
    pytest.param("[2001:db8::1]", "[2001:db8::1]", id="ipv6-entre-crochets"),
    pytest.param("[::1]", "[::1]", id="ipv6-loopback-entre-crochets"),
    pytest.param("[::ffff:192.0.2.1]", "[::ffff:192.0.2.1]", id="ipv6-ipv4-mappee"),
    pytest.param(" 192.0.2.1 \n", "192.0.2.1", id="blancs-extremites-retires"),
])
def test_validate_host_accepts(value, expected):
    assert validate_host(value) == expected


@pytest.mark.parametrize("value", [
    pytest.param("", id="vide"),
    pytest.param("   ", id="blancs"),
    pytest.param(None, id="absent"),
])
def test_validate_host_vide_retourne_none(value):
    assert validate_host(value) is None


# ---------------------------------------------------------------- refusés


@pytest.mark.parametrize("value", [
    pytest.param(INJECTION_PAYLOAD, id="injection-nginx"),
    pytest.param("evil:8080", id="suffixe-port"),
    pytest.param("http://host", id="schema-http"),
    pytest.param("https://host:8080/x", id="schema-https"),
    pytest.param("host:8080/path", id="port-et-chemin"),
    pytest.param("host/path", id="chemin"),
    pytest.param("host?x=1", id="query"),
    pytest.param("host#frag", id="fragment"),
    pytest.param("user@host", id="userinfo"),
    pytest.param("host;8080", id="point-virgule"),
    pytest.param("host{x", id="accolade-ouverte"),
    pytest.param("x}host", id="accolade-fermee"),
    pytest.param('ho"st', id="guillemet-double"),
    pytest.param("ho'st", id="guillemet-simple"),
    pytest.param("ho\\st", id="antislash"),
    pytest.param("evil host", id="espace"),
    pytest.param("evil\thost", id="tabulation"),
    pytest.param("evil\nhost", id="retour-ligne"),
    pytest.param("evil\r\nhost", id="crlf"),
    pytest.param("host\x00", id="caractere-nul"),
    pytest.param("x" * 254, id="plus-de-253"),
    pytest.param("a" * 64, id="label-plus-de-63"),
    pytest.param("-host", id="tiret-en-tete"),
    pytest.param("host-", id="tiret-en-queue"),
    pytest.param("a.-b", id="tiret-en-tete-de-label"),
    pytest.param("a.b-", id="tiret-en-queue-de-label"),
    pytest.param("a..b", id="label-vide"),
    pytest.param(".host", id="point-initial"),
    pytest.param("host.", id="point-final"),
    pytest.param("hôte.example", id="non-ascii"),
    pytest.param("host_name", id="underscore"),
    pytest.param("2001:db8::1", id="ipv6-nu"),
    pytest.param("::1", id="ipv6-nu-loopback"),
    pytest.param("[2001:db8::1", id="crochet-non-ferme"),
    pytest.param("2001:db8::1]", id="crochet-non-ouvert"),
    pytest.param("[]", id="crochets-vides"),
    pytest.param("[localhost]", id="crochets-non-ipv6"),
    pytest.param("[2001:db8::1]:443", id="ipv6-crochets-et-port"),
    pytest.param("[2001:db8::1]x", id="ipv6-crochets-et-suffixe"),
    pytest.param("[fe80::1%eth0]", id="ipv6-zone"),
    pytest.param("$host", id="variable-nginx"),
    pytest.param("${hub}", id="variable-nginx-accolades"),
    pytest.param("host`id`", id="backticks"),
    pytest.param("javascript:alert(1)", id="pseudo-schema"),
])
def test_validate_host_refuse(value):
    with pytest.raises(ValueError):
        validate_host(value)


def test_validate_host_erreur_ne_reprend_pas_la_valeur():
    """La valeur est contrôlée par l'appelant : le message d'erreur (renvoyé
    en 422, journalisé) ne doit la recopier ni contenir de retour à la ligne."""
    with pytest.raises(ValueError) as excinfo:
        validate_host(INJECTION_PAYLOAD)
    message = str(excinfo.value)
    assert "docker-socket-proxy" not in message
    assert "\n" not in message
