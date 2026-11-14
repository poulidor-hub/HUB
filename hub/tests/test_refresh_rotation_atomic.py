# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Rotation du refresh token en transaction UNIQUE (perf heure de pointe).

Avant : `create_refresh_token` committait à l'intérieur de la rotation
(fsync intermédiaire), puis `rotate_refresh_token` committait à nouveau —
2 fsync par rotation, et une fenêtre où l'ancien token était révoqué
commité sans que le remplaçant (ni son `replaced_by_token`) ne soit
persisté. Désormais : flush intermédiaire, UN SEUL commit en fin de flow
(appartenant à l'appelant : rotation, login, launch d'app).

Garanties vérifiées :
- une rotation complète n'émet qu'UN SEUL commit ;
- le commit final persiste TOUT (nouveau token, révocation, replaced_by) —
  visible depuis une seconde session ;
- la période de grâce survit : re-présentation de l'ancien token → le
  remplaçant est re-délivré (course inter-onglets).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import pytest

from app.auth import (
    create_refresh_token,
    hash_token,
    rotate_refresh_token,
)
from app.models import RefreshToken


class TestSingleTransactionRotation:
    @pytest.mark.asyncio
    async def test_rotation_is_single_transaction(
        self, async_session, async_engine, test_user, monkeypatch
    ):
        """Une rotation complète n'émet qu'UN SEUL commit.

        Le commit intermédiaire de create_refresh_token est remplacé par un
        flush : le commit final appartient à rotate_refresh_token.
        """
        commits = {"n": 0}
        real_commit = async_session.commit

        async def counting_commit():
            commits["n"] += 1
            await real_commit()

        monkeypatch.setattr(async_session, "commit", counting_commit)

        old = await create_refresh_token(async_session, user_id=test_user.id)
        commits["n"] = 0  # le setup (création du token initial) ne compte pas

        result = await rotate_refresh_token(async_session, old)
        assert result is not None

        assert commits["n"] == 1, (
            f"rotation = {commits['n']} commits ; le commit intermédiaire "
            "de create_refresh_token doit être un flush"
        )

    @pytest.mark.asyncio
    async def test_rotation_persists_all_rows_after_final_commit(
        self, async_session, async_engine, test_user
    ):
        """Le commit final persiste TOUT : nouveau token + révocation +
        replaced_by, visibles depuis une seconde session (la rotation est
        atomique — jamais un état moitié-committé)."""
        old = await create_refresh_token(async_session, user_id=test_user.id)

        new_token, _new_refresh = await rotate_refresh_token(async_session, old)
        assert new_token is not None

        # Seconde session, même engine : lit l'état COMMITTÉ
        other_session_maker = async_sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with other_session_maker() as other:
            result = await other.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_token(new_token)
                )
            )
            new_row = result.scalar_one_or_none()
            assert new_row is not None, (
                "le token remplaçant doit être persisté par le commit final"
            )
            assert new_row.revoked_at is None

            result = await other.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_token(old)
                )
            )
            old_row = result.scalar_one()
            assert old_row.revoked_at is not None
            assert old_row.replaced_by_token == new_token


class TestGraceRegression:
    @pytest.mark.asyncio
    async def test_rotation_survives_and_grace_works(
        self, async_session, test_user
    ):
        """Régression grâce : rotation puis re-présentation de l'ancien
        token dans la fenêtre de grâce → le remplaçant est re-délivré
        (course inter-onglets, cf. TestRefreshTokenGrace de test_auth.py)."""
        old = await create_refresh_token(async_session, user_id=test_user.id)

        first = await rotate_refresh_token(async_session, old)
        assert first is not None
        new_token, _ = first

        # Onglet B : re-présente l'ancien token pendant la grâce
        second = await rotate_refresh_token(async_session, old)
        assert second is not None
        # Re-délivrance idempotente : le MÊME remplaçant que la 1re rotation
        assert second[0] == new_token
