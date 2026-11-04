#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
AN-2 (banc 2026-09-10, Option B validée) — purge one-shot des liens M2M
croisés atelier↔prod dans service_account_app_access.

Retire, pour chaque service account dont le contexte est identifiable
(app propriétaire par convention de nommage, sinon contexte homogène),
les liens vers les apps du contexte opposé :
  - SA d'app prod   : retire les ateliers (-ws) liés ;
  - SA d'app atelier: retire les apps prod liées.
Les liens same-context (services partagés légitimes) ne sont PAS touchés.
Les SA ambigus (contextes mixtes, propriétaire non identifiable) sont
laissés intacts et signalés.

Les futurs grants sont déjà protégés en code (app/m2m_scope.py :
parité de contexte + claim JWT filtré) — ce script nettoie l'existant.

Usage (depuis le conteneur hub-api) :
    python scripts/purge_workshop_m2m_leaks.py            # dry-run (défaut)
    python scripts/purge_workshop_m2m_leaks.py --apply    # purge réelle
"""
import asyncio
import sys

from app.database import async_session_maker
from app.m2m_scope import purge_workshop_leaks


async def main(apply: bool) -> int:
    async with async_session_maker() as db:
        report = await purge_workshop_leaks(db)

        removed = report.get("removed", [])
        ambiguous = report.get("skipped_ambiguous", [])

        if not removed and not ambiguous:
            print("✓ Aucune fuite atelier↔prod détectée. Base déjà saine.")
            return 0

        if removed:
            print(f"{len(removed)} lien(s) croisé(s) atelier↔prod à retirer :")
            for line in removed:
                print(f"  - {line}")
        if ambiguous:
            print(f"⚠️  {len(ambiguous)} SA ambigu(s) laissé(s) intact(s) (décision manuelle) :")
            for line in ambiguous:
                print(f"  - {line}")

        if not apply:
            print("\n[DRY-RUN] Aucune modification. Relancer avec --apply pour purger.")
            return 0

        await db.commit()
        print(f"\n✅ Purge effectuée : {len(removed)} lien(s) retiré(s).")
        print("ℹ️  Les tokens M2M déjà émis gardent leur claim 'apps' jusqu'à expiration ;")
        print("    le garde runtime m2m_authorization bloque déjà le cross-workshop.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(apply="--apply" in sys.argv)))
