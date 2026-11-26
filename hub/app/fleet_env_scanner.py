# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Revendications (claims) APP_SUBNET/ports écrites dans les .env de la flotte.

Les `.env` des apps (montage `apps_host_path`) sont la source de vérité
DURABLE du parc : ils survivent à un réseau Docker pruné et aux redémarrages,
contrairement aux scans Docker qui ne voient que le vivant. Une app legacy
(jamais passée par `allocate-ports`, donc absente du registre) ou simplement
arrêtée au moment d'une allocation doit néanmoins protéger ses ports et son
sous-réseau (bug 2026-11-13 : `10.50.16.0/24` et le port 9019 ré-attribués
alors que tiers-app-ws / exemple-app-9 les revendiquaient dans leur .env).

Symétrique de `docker_scanner.get_used_*` : best-effort, ne lève jamais —
montage absent ou fichier illisible → ensembles vides + warning.
"""

import ipaddress
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_LINE = re.compile(r'^([A-Z_][A-Z0-9_]*)=(.*)$')


def _read_env_keys(env_path: Path, keys: set[str]) -> dict[str, str]:
    """Valeurs brutes des clés demandées dans un fichier .env (best-effort)."""
    values: dict[str, str] = {}
    try:
        with open(env_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                match = ENV_LINE.match(line.strip())
                if match and match.group(1) in keys:
                    values.setdefault(match.group(1), match.group(2).strip())
    except OSError as e:
        logger.warning("Claim .env illisible (%s) : %s", env_path, e)
    return values


def _parse_port(raw: str) -> int | None:
    """Port TCP valide (1-65535) sinon None (valeur invalide ignorée)."""
    try:
        port = int(raw)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def read_env_claims_by_slug(apps_dir) -> dict[str, tuple[set[str], set[int]]]:
    """Scanne `{apps_dir}/*/.env` → claims PAR app (slug = nom du dossier).

    Mêmes règles que `read_env_claims` (un seul niveau, `.env` exact, valeurs
    invalides ignorées) mais structurées par app : permet à l'appelant
    d'exclure les revendications d'une app précise (ex. le slug demandeur
    d'une allocation, dont le propre `.env` ne doit pas bloquer ses ports
    souhaités — bug E2E 26/11/2026).
    """
    claims: dict[str, tuple[set[str], set[int]]] = {}

    root = Path(apps_dir)
    try:
        entries = sorted(root.iterdir())
    except OSError as e:
        logger.warning("Scan des claims flotte impossible (%s) : %s", root, e)
        return claims

    for entry in entries:
        env_path = entry / ".env"
        if not entry.is_dir() or not env_path.is_file():
            continue
        values = _read_env_keys(
            env_path, {"APP_SUBNET", "API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT"}
        )

        subnet = values.get("APP_SUBNET", "")
        subnets: set[str] = set()
        if subnet:
            try:
                ipaddress.ip_network(subnet, strict=False)
                subnets.add(subnet)
            except ValueError:
                logger.warning("APP_SUBNET invalide ignoré (%s) : %s", env_path, subnet)

        ports: set[int] = set()
        for key in ("API_PORT", "FRONTEND_PORT", "FRONTEND_HTTPS_PORT"):
            port = _parse_port(values.get(key, ""))
            if port is not None:
                ports.add(port)

        claims[entry.name] = (subnets, ports)

    return claims


def read_env_claims(apps_dir) -> tuple[set[str], set[int]]:
    """Scanne `{apps_dir}/*/.env` → (sous-réseaux, ports) revendiqués.

    - Un seul niveau : les sous-dossiers directs de APPS/ (une app = un
      dossier) ; fichiers et imbrications plus profondes ignorés.
    - Seul le fichier exact `.env` compte : les sauvegardes horodatées
      (`.env.pre-subnet-*`, `.env.bak`, …) ne sont pas des claims.
    - Valeurs invalides (CIDR non parseable, port non numérique/hors plage)
      ignorées silencieusement.
    """
    subnets: set[str] = set()
    ports: set[int] = set()

    for app_subnets, app_ports in read_env_claims_by_slug(apps_dir).values():
        subnets |= app_subnets
        ports |= app_ports

    return subnets, ports
