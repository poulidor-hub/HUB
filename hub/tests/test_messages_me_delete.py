# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests suppression côté destinataire (console notifications unifiée).

Task 1 : round-trip de la colonne `hidden_at` sur MessageReadReceipt.
"""
import pytest
from datetime import datetime, timezone

from app.models import MessageReadReceipt, UserMessage


@pytest.mark.asyncio
async def test_receipt_hidden_at_roundtrip(async_session, admin_user):
    msg = UserMessage(sender_type="admin", target_type="user", target_user_id=admin_user.id,
                      priority="info", title="Hidden roundtrip")
    async_session.add(msg)
    await async_session.flush()
    r = MessageReadReceipt(message_id=msg.id, user_id=admin_user.id,
                           hidden_at=datetime.now(timezone.utc))
    async_session.add(r)
    await async_session.commit()
    await async_session.refresh(r)
    assert r.hidden_at is not None
