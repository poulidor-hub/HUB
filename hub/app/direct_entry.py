# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Garde d'entrée publique des routes d'inscription (25/11/2026).

Le frontal nginx (`frontend/nginx.conf` et `frontend/nginx-ssl.conf`) pose
l'en-tête `X-Hub-Entry: public` sur toute requête qu'il proxifie vers `/api/`.
Or les routes d'inscription ne sont protégées, côté Hub, que par une garde par
adresse — qui voit alors l'adresse de nginx, membre du réseau du Hub et donc de
la liste blanche. `require_direct_entry` les refuse (403) dès que la marque est
présente : elles ne restent joignables que par l'accès direct au port 8000
(réseaux Docker et liste blanche), avec leur garde par adresse.

L'en-tête ne peut que refuser : un appel direct qui le falsifie se refuse à
lui-même. C'est le contrôle en profondeur des blocs nginx — la protection
reste effective si ces blocs viennent à être perdus.
"""
from fastapi import HTTPException, Request, status

PUBLIC_ENTRY_HEADER = "X-Hub-Entry"
PUBLIC_ENTRY_VALUE = "public"


async def require_direct_entry(request: Request) -> None:
    """Refuse (403) une requête arrivée par l'entrée publique (nginx).

    La marque est lue sans tenir compte de la casse ni des espaces
    d'extrémité. Une requête sans la marque n'est pas touchée : la route
    applique ensuite sa propre garde.
    """
    marker = request.headers.get(PUBLIC_ENTRY_HEADER, "")
    if marker.strip().lower() == PUBLIC_ENTRY_VALUE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Endpoint réservé au réseau interne du Hub",
        )
