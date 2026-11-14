# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests de verify_password_async (offload bcrypt dans un thread).

Régression perf : bcrypt cost 12 dure ~200 ms et bloquait l'event loop du
worker unique à chaque login (et chaque rate-limit / dummy check). Le helper
doit déléguer à asyncio.to_thread pour que la boucle reste disponible.
`verify_password` sync reste inchangé (tâches de fond type main.py).
"""
import asyncio

import bcrypt

from app.auth import get_password_hash, verify_password, verify_password_async


async def test_verify_password_async_true():
    hashed = get_password_hash("s3cret!")
    assert await verify_password_async("s3cret!", hashed) is True


async def test_verify_password_async_false():
    hashed = get_password_hash("s3cret!")
    assert await verify_password_async("wrong-password", hashed) is False


async def test_verify_password_async_does_not_block_loop():
    """Pendant l'attente du verify dans le thread, un asyncio.sleep(0)
    programmé doit s'exécuter AVANT que la vérification ne finisse :
    la preuve que l'event loop n'a pas été bloquée par le bcrypt."""
    # Hash à coût élevé (rounds 14 ≈ 1 s) : discrimination sans ambiguïté
    hashed = bcrypt.hashpw(b"thread-offload", bcrypt.gensalt(rounds=14)).decode()
    assert verify_password("thread-offload", hashed)  # hash exploitable

    verify_task = asyncio.create_task(verify_password_async("thread-offload", hashed))
    witness = asyncio.create_task(asyncio.sleep(0))
    await asyncio.wait_for(witness, timeout=5)

    # Le témoin a obtenu la main pendant que le bcrypt tournait dans le thread
    assert not verify_task.done(), (
        "verify_password_async a bloqué l'event loop : la vérification était "
        "terminée avant que le témoin asyncio.sleep(0) ne s'exécute"
    )
    assert await asyncio.wait_for(verify_task, timeout=30) is True
