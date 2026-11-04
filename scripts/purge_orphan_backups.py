#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Purge one-shot des backups morts d'un Hub déployé.

Deux niveaux de reliques sont traités :

1. **Lignes DB mortes** : entrées de `app_backups` dont l'application
   propriétaire n'existe plus (FK `applications.id` mise à NULL lors de la
   suppression de l'app — cascade `SET NULL`). Sans purge, ces lignes
   protègent leurs fichiers pour toujours.
2. **Fichiers orphelins** : ZIP / exports chiffrés présents dans le stockage
   (storage/backups/) mais référencés par AUCUNE ligne `app_backups`
   survivante — cas typique : fichiers des lignes ci-dessus, ou restes d'un
   enregistrement purgé manuellement.

Le script pilote le conteneur hub-api via `docker exec` : lecture des
références DB, purge des lignes mortes, scan du stockage et suppression se
font côté conteneur (chemins cohérents, montage déjà en place). Aucun accès
direct au disque depuis l'hôte : fonctionne à l'identique sur tout Hub déjà
déployé (git pull + exécution).

Usage :
    python3 scripts/purge_orphan_backups.py                 # dry-run (liste)
    python3 scripts/purge_orphan_backups.py --apply         # purge réelle
    python3 scripts/purge_orphan_backups.py --days 30       # garde anti-course
    python3 scripts/purge_orphan_backups.py --container X   # autre conteneur

Sécurités :
- dry-run par défaut : rien n'est supprimé sans --apply.
- Garde d'âge (--days, défaut 7) : un backup en cours d'écriture n'est
  référencé en DB qu'à la fin — les fichiers trop récents ne sont jamais
  considérés orphelins.
- Les fichiers référencés par une ligne vivante sont TOUJOURS intouchables.
- Seuls les artefacts de backup (*.zip, *.encrypted) sous storage/backups
  sont éligibles ; jamais d'autre chemin (snapshots PRA, secrets, docs).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_CONTAINER = "hub-api"
DEFAULT_DAYS = 7

# Exécuté DANS le conteneur hub-api : renvoie les orphelins en JSON.
# --apply : les supprime ; sinon les liste seulement.
_CONTAINER_CODE = r"""
import asyncio, json, sys, time
from pathlib import Path

BASE = Path("/code/storage/backups")
apply = "--apply" in sys.argv
cutoff = float(sys.argv[sys.argv.index("--cutoff") + 1])
BACKUP_SUFFIXES = (".zip", ".encrypted")

from app.database import async_session_maker
from sqlalchemy import text

async def collect():
    async with async_session_maker() as db:
        # 1. Lignes « mortes » : l'application propriétaire n'existe plus
        #    (FK applications.id mise à NULL par la suppression de l'app).
        rows = await db.execute(text(
            "SELECT id, app_slug, storage_path, encrypted_path, created_at"
            " FROM app_backups WHERE app_id IS NULL"))
        dead = [{"id": r.id, "app_slug": r.app_slug,
                 "storage_path": r.storage_path,
                 "encrypted_path": r.encrypted_path,
                 "created_at": str(r.created_at)} for r in rows]

        # 2. Suppression des lignes mortes (sinon leurs fichiers resteraient
        #    référencés à jamais). Les chemins restent journalisés dans le
        #    rapport du script.
        if apply and dead:
            ids = [d["id"] for d in dead]
            await db.execute(text(
                "DELETE FROM app_backups WHERE id = ANY(:ids)"), {"ids": ids})
            await db.commit()

        # 3. Références protectrices : toutes les lignes restantes.
        rows = await db.execute(text(
            "SELECT storage_path, encrypted_path FROM app_backups"))
        refs = set()
        for storage, encrypted in rows:
            for value in (storage, encrypted):
                if value and value.strip():
                    refs.add(value.strip())
        return dead, refs

dead, refs = asyncio.run(collect())

now = time.time()
orphans = []
removed = []
if BASE.is_dir():
    for path in BASE.rglob("*"):
        if not path.is_file() or not path.name.endswith(BACKUP_SUFFIXES):
            continue
        rel = path.relative_to(BASE).as_posix()
        if rel in refs:
            continue  # référencé par une ligne vivante : intouchable
        st = path.stat()
        if st.st_mtime > cutoff:
            continue  # trop récent : backup potentiellement en cours
        orphans.append({"path": rel, "size": st.st_size,
                        "mtime": int(st.st_mtime)})
        if apply:
            try:
                path.unlink()
                removed.append(rel)
            except OSError as error:
                orphans[-1]["error"] = str(error)

removed_dirs = 0
if apply:
    # Coquilles vides : répertoires de slugs devenus inutiles (du plus
    # profond au plus haut ; rmdir échoue proprement si non vide).
    dirs = sorted((d for d in BASE.rglob("*") if d.is_dir()),
                  key=lambda p: len(p.parts), reverse=True)
    for directory in dirs:
        try:
            directory.rmdir()
            removed_dirs += 1
        except OSError:
            pass

print(json.dumps({"dead_rows": dead, "orphans": orphans,
                  "removed": removed, "removed_dirs": removed_dirs}))
"""


def find_orphans(
    files: dict[str, tuple[int, float]],
    referenced: set[str],
    cutoff_ts: float,
) -> list[dict]:
    """Logique pure : orphelins = non référencés et plus vieux que le cutoff.

    Args:
        files: {chemin relatif: (taille, mtime)}
        referenced: chemins relatifs cités par la DB
        cutoff_ts: timestamp epoch — fichiers plus récents exclus
    """
    orphans = []
    for rel, (size, mtime) in sorted(files.items()):
        if rel in referenced:
            continue
        if mtime > cutoff_ts:
            continue
        orphans.append({"path": rel, "size": int(size),
                        "mtime": int(mtime)})
    return orphans


def _run_in_container(container: str, args: list[str]) -> dict:
    """Exécute le code conteneurisé, retourne le JSON produit."""
    cmd = ["docker", "exec", "-i", container,
           "python", "-", "--cutoff", str(args["cutoff"])]
    if args["apply"]:
        cmd.append("--apply")
    result = subprocess.run(cmd, input=_CONTAINER_CODE,
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-2000:] + "\n")
        raise SystemExit(f"❌ Échec dans le conteneur {container} "
                         f"(exit {result.returncode})")
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def _human(nbytes: int) -> str:
    value = float(nbytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge les backups orphelins du stockage d'un Hub déployé.")
    parser.add_argument("--apply", action="store_true",
                        help="supprime réellement (défaut : dry-run)")
    parser.add_argument("--days", type=float, default=DEFAULT_DAYS,
                        help=f"âge minimum d'un orphelin en jours "
                             f"(défaut {DEFAULT_DAYS})")
    parser.add_argument("--container", default=DEFAULT_CONTAINER,
                        help=f"conteneur hub-api (défaut {DEFAULT_CONTAINER})")
    args = parser.parse_args(argv)

    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=args.days)).timestamp()
    payload = _run_in_container(args.container,
                                {"cutoff": cutoff, "apply": args.apply})
    orphans = payload["orphans"]
    removed = set(payload.get("removed", []))
    dead_rows = payload.get("dead_rows", [])

    if not orphans and not dead_rows and not payload.get("removed_dirs"):
        print("✅ Aucun backup orphelin — stockage cohérent.")
        return 0

    if dead_rows:
        act = "supprimée(s)" if args.apply else "morte(s) — à supprimer"
        print(f"🗄️ {len(dead_rows)} ligne(s) de backup d'application(s) "
              f"{act} (l'app n'existe plus) :")
        for row in dead_rows:
            act2 = "🗑️" if args.apply else "  "
            print(f"    {act2} #{row['id']} {row['app_slug']} "
                  f"({row['created_at'][:10]}) {row['storage_path'] or '—'}")
        print()

    if not orphans:
        print("✅ Aucun fichier orphelin dans le stockage.")
        return 0

    by_slug: dict[str, list[dict]] = {}
    for item in orphans:
        slug = item["path"].split("/", 1)[0]
        by_slug.setdefault(slug, []).append(item)

    total = sum(item["size"] for item in orphans)
    mode = "PURGÉS" if args.apply else "orphelins (dry-run — lancez avec --apply)"
    print(f"🧹 {len(orphans)} fichier(s) {mode} — {_human(total)} :\n")
    for slug in sorted(by_slug):
        items = by_slug[slug]
        size = sum(item["size"] for item in items)
        print(f"  {slug}/ — {len(items)} fichier(s), {_human(size)}")
        for item in items:
            mark = "🗑️" if item["path"] in removed else "  "
            date = datetime.fromtimestamp(item["mtime"],
                                          tz=timezone.utc).strftime("%Y-%m-%d")
            error = f" ⚠️ {item['error']}" if "error" in item else ""
            print(f"    {mark} {item['path']} ({_human(item['size'])}, "
                  f"{date}){error}")

    if args.apply:
        dirs_removed = payload.get("removed_dirs", 0)
        summary = (f"✅ {len(removed)} fichier(s) supprimé(s), "
                   f"{len(dead_rows)} ligne(s) DB purgée(s)")
        if dirs_removed:
            summary += f", {dirs_removed} répertoire(s) vide(s) retiré(s)"
        print(summary + ".")
    else:
        print(f"\nℹ️ Dry-run : aucune suppression effectuée. "
              f"Relancez avec --apply pour purger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
