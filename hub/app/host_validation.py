# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Validateur d'hôte unique : IPv4, nom d'hôte, IPv6 entre crochets.

Un hôte accepté ici finit dans `f"http://{host}:{port}"`, réécrit tel quel
dans une directive `proxy_pass` de la configuration nginx générée
(app.routing_service). Le moindre caractère de syntaxe — retour à la ligne,
`;`, `{` — y injecte des directives. Toute entrée qui alimente une telle URL
(custom_host d'inscription, host d'allocation de ports, hub_host) doit donc
passer par validate_host.

Acceptés : une IPv4 en notation décimale (cas particulier d'un nom d'hôte),
un nom d'hôte (labels de 1 à 63 caractères alphanumériques/tirets, sans tiret
en début ni fin de label, 253 caractères au total), un littéral IPv6 ENTRE
CROCHETS. Un IPv6 nu est REFUSÉ : il casserait l'URL construite.
"""

import ipaddress
import re

HOST_MAX_LENGTH = 253

# Label RFC 1123 : lettres, chiffres, tirets ; jamais de tiret en tête/queue.
_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
# Littéral IPv6 entre crochets : hexadécimal, `:` et `.` (forme IPv4 mappée).
# Pas de `%` : un identifiant de zone n'a pas sa place tel quel dans une URL.
_BRACKETED_IPV6_RE = re.compile(r"^\[([0-9A-Fa-f:.]+)\]$")

_INVALID = (
    "Hôte invalide : adresse IPv4, nom d'hôte ou IPv6 entre crochets attendu "
    "(sans schéma, port ni chemin)."
)


def validate_host(value: str) -> str | None:
    """Valide un hôte destiné à `f"http://{host}:{port}"`.

    Retourne l'hôte sans ses blancs d'extrémité, ou None si la valeur est vide.
    Lève ValueError si la valeur est refusée. Le message ne reprend JAMAIS la
    valeur : elle est contrôlée par l'appelant et peut contenir des retours à
    la ligne (elle part en 422 et dans les journaux).
    """
    host = (value or "").strip()
    if not host:
        return None
    if len(host) > HOST_MAX_LENGTH:
        raise ValueError("Hôte invalide : 253 caractères au maximum.")
    if host.startswith("["):
        match = _BRACKETED_IPV6_RE.match(host)
        if not match:
            raise ValueError(_INVALID)
        try:
            ipaddress.IPv6Address(match.group(1))
        except ValueError:
            raise ValueError(_INVALID) from None
        return host
    if ":" in host:
        # IPv6 nu ou suffixe « :port » — les deux cassent l'URL construite.
        raise ValueError(_INVALID)
    if not all(_LABEL_RE.match(label) for label in host.split(".")):
        raise ValueError(_INVALID)
    return host
