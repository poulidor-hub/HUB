# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Garde d'entrée publique des routes d'inscription (25/11/2026).

Les routes d'inscription ne sont protégées, côté Hub, que par une garde par
adresse. Or la requête qui arrive par un frontal nginx ne porte pas l'adresse
de l'appelant :

- le frontal du Hub (`frontend/nginx.conf`, `frontend/nginx-ssl.conf`) proxifie
  `/api/` vers le Hub et pose l'en-tête `X-Hub-Entry: public` ;
- les frontaux des applications (socle et template) proxifient `/hub/` vers le
  port publié du Hub : la requête arrive de la passerelle Docker, membre de la
  liste blanche.

Dans les deux cas, la garde par adresse voit une adresse autorisée.
`require_direct_entry` refuse (403) dès que la requête porte la marque du frontal
du Hub OU un en-tête de transfert (`X-Forwarded-For`, `X-Real-IP`) : un appel
légitime — conteneur d'application ou hubctl, en direct sur le port 8000 — n'en
porte jamais. Les routes ne restent joignables que par cet accès direct, avec
leur garde par adresse.

Ces en-têtes ne peuvent que refuser : un appel direct qui les falsifie se refuse
à lui-même. La garde ne dépend d'aucun fichier de configuration nginx.
"""
from fastapi import HTTPException, Request, status

PUBLIC_ENTRY_HEADER = "X-Hub-Entry"
PUBLIC_ENTRY_VALUE = "public"
# Posés par tout frontal nginx, Hub comme applications ; absents d'un appel direct.
FORWARDED_HEADERS = ("X-Forwarded-For", "X-Real-IP")

_REFUS = "Endpoint réservé au réseau interne du Hub"


async def require_direct_entry(request: Request) -> None:
    """Refuse (403) une requête arrivée par un frontal (entrée publique).

    Est refusée une requête qui porte la marque du frontal du Hub (sans tenir
    compte de la casse ni des espaces d'extrémité) ou un en-tête de transfert
    non vide. Une requête directe n'est pas touchée : la route applique ensuite
    sa propre garde.
    """
    marque = request.headers.get(PUBLIC_ENTRY_HEADER, "")
    transmis = marque.strip().lower() == PUBLIC_ENTRY_VALUE or any(
        request.headers.get(nom, "").strip() for nom in FORWARDED_HEADERS
    )
    if transmis:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_REFUS)
