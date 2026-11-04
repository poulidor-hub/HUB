#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Crée le Service Account "SA - Hub" lié à l'application hub (id=1).

Ce script est idempotent : si un SA existe déjà pour l'app hub, il ne fait rien.
À exécuter une seule fois après déploiement du endpoint /api/admin/restore.

Usage (depuis le conteneur hub-api) :
    python scripts/create_hub_sa.py
"""
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models import ServiceAccount, Application
from app.auth import get_password_hash
from app.routes.service_accounts import save_service_account_credentials


HUB_APP_SLUG = "hub"
SA_NAME = "SA - Hub"


async def main() -> int:
    async with async_session_maker() as db:
        existing = await db.execute(
            select(ServiceAccount)
            .join(ServiceAccount.applications)
            .where(Application.slug == HUB_APP_SLUG)
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            print(f"✓ Un Service Account existe déjà pour l'app '{HUB_APP_SLUG}'. Rien à faire.")
            return 0

        hub_app = (await db.execute(
            select(Application).where(Application.slug == HUB_APP_SLUG)
        )).scalar_one_or_none()
        if hub_app is None:
            print(f"✗ Application '{HUB_APP_SLUG}' introuvable en base.")
            return 1

        client_id = ServiceAccount.generate_client_id()
        client_secret = ServiceAccount.generate_client_secret()

        sa = ServiceAccount(
            name=SA_NAME,
            description="Service Account du HUB pour auto-restauration et M2M",
            client_id=client_id,
            client_secret_hash=get_password_hash(client_secret),
        )
        sa.applications = [hub_app]
        db.add(sa)
        await db.commit()
        await db.refresh(sa)

        save_service_account_credentials(
            name=HUB_APP_SLUG,
            client_id=client_id,
            client_secret=client_secret,
            app_slugs=[HUB_APP_SLUG],
        )

        print(f"✓ Service Account '{SA_NAME}' créé (id={sa.id}, client_id={client_id})")
        print(f"  lié à l'app '{HUB_APP_SLUG}' (id={hub_app.id})")
        print(f"  credentials sauvegardés dans secrets/service_accounts/sa_hub.env")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
