# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth import verify_password
from app.models import AuditLog, PasswordResetCode, utcnow
from tests.conftest import get_auth_header

pytestmark = pytest.mark.asyncio

CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
GEN = "/api/users/{uid}/password-reset-code"


async def test_password_reset_code_model_roundtrip(async_session):
    row = PasswordResetCode(
        user_id=1,
        code_hash="hash-bcrypt-jamais-le-code-en-clair",
        expires_at=utcnow() + timedelta(minutes=15),
        created_by_user_id=2,
    )
    async_session.add(row)
    await async_session.commit()
    await async_session.refresh(row)
    assert row.id is not None
    assert row.consumed_at is None
    assert row.code_hash != "XXXX-XXXX"


async def test_generate_requires_admin(client, test_user, user_token):
    assert (await client.post(GEN.format(uid=test_user.id))).status_code == 401
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(user_token))
    assert r.status_code == 403


async def test_generate_unknown_user_404(client, admin_token):
    r = await client.post(GEN.format(uid=999999), headers=get_auth_header(admin_token))
    assert r.status_code == 404


async def test_generate_inactive_user_400(client, test_user, admin_token, async_session):
    test_user.is_active = False
    await async_session.commit()
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r.status_code == 400


async def test_generate_success_format_hash_ttl(client, test_user, admin_token, async_session):
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r.status_code == 200
    body = r.json()
    assert CODE_RE.match(body["code"]) and body["expires_at"]
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    from app.auth import normalize_reset_code
    assert row.code_hash != body["code"]
    assert verify_password(normalize_reset_code(body["code"]), row.code_hash)  # hash du code normalisé
    assert row.consumed_at is None and row.created_by_user_id is not None
    audit = (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "password_reset_code_generated"))).scalar_one()
    assert audit.user_id is not None


async def test_generate_revokes_previous_code(client, test_user, admin_token, async_session):
    r1 = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    r2 = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r1.json()["code"] != r2.json()["code"]
    rows = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalars().all()
    assert sum(1 for x in rows if x.consumed_at is None) == 1    # seul le dernier est actif


async def _gen(client, admin_token, uid):
    r = await client.post(GEN.format(uid=uid), headers=get_auth_header(admin_token))
    assert r.status_code == 200
    return r.json()["code"]


async def test_verify_reset_code_delivers_pwd_reset(client, test_user, admin_token, async_session):
    from app.auth import decode_reset_token
    code = await _gen(client, admin_token, test_user.id)
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": code})
    assert r.status_code == 200
    payload = decode_reset_token(r.json()["reset_token"])
    assert payload["scope"] == "pwd_reset" and payload["sub"] == "testuser"
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    assert row.consumed_at is not None
    assert (await async_session.execute(
        select(AuditLog).where(AuditLog.action == "password_reset_code_verified"))).scalar_one()


async def test_verify_normalization_accepts_lowercase_and_spaces(client, test_user, admin_token):
    code = await _gen(client, admin_token, test_user.id)
    messy = f" {code.lower().replace('-', ' ')} "
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": messy})
    assert r.status_code == 200


async def test_verify_generic_errors_identical(client, test_user, admin_token, async_session):
    from app.routes.auth import _recover_attempts
    generic = "Code de récupération invalide ou récupération indisponible"
    code = await _gen(client, admin_token, test_user.id)

    async def try_code(body):
        return await client.post("/api/auth/recover/verify", json=body)

    r = await try_code({"username": "ghost", "reset_code": code})
    assert (r.status_code, r.json()["detail"]) == (401, generic)      # user inconnu
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": "ZZZZ-ZZZZ"})
    assert (r.status_code, r.json()["detail"]) == (401, generic)      # mauvais code
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": code.lower()})
    assert r.status_code == 200
    _recover_attempts.clear()
    r = await try_code({"username": "testuser", "reset_code": code})   # déjà consommé
    assert (r.status_code, r.json()["detail"]) == (401, generic)


async def test_verify_expired_code_401(client, test_user, admin_token, async_session):
    code = await _gen(client, admin_token, test_user.id)
    row = (await async_session.execute(
        select(PasswordResetCode).where(PasswordResetCode.user_id == test_user.id))).scalar_one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    await async_session.commit()
    r = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": code})
    assert r.status_code == 401


async def test_verify_rate_limit_dedicated(client, test_user, admin_token):
    from app.routes.auth import _login_attempts
    code = await _gen(client, admin_token, test_user.id)
    statuses = []
    for _ in range(6):
        r = await client.post("/api/auth/recover/verify",
                              json={"username": "testuser", "reset_code": "AAAA-AAAA"})
        statuses.append(r.status_code)
    assert statuses[-1] == 429
    r_last = await client.post("/api/auth/recover/verify",
                               json={"username": "testuser", "reset_code": code})
    assert r_last.status_code == 429 and "retry-after" in {k.lower() for k in r_last.headers}
    assert len(_login_attempts) == 0   # limiter login intact (Review Focus #2)


async def _capture_sse(monkeypatch):
    events = []
    monkeypatch.setattr("app.routes.events.notify_event",
                        lambda t, d=None: events.append((t, d or {})))
    return events


async def _force_logouts(events):
    return [e for t, e in events if t == "force_logout"]


async def test_full_code_flow_revokes_sessions(client, test_user, admin_token, async_session, monkeypatch):
    from app.models import RefreshToken
    events = await _capture_sse(monkeypatch)
    login = await client.post("/api/auth/token",
                              data={"username": "testuser", "password": "testpass123"})
    assert login.status_code == 200                        # un refresh token vivant
    code = await _gen(client, admin_token, test_user.id)
    v = await client.post("/api/auth/recover/verify",
                          json={"username": "testuser", "reset_code": code})
    assert v.status_code == 200
    r = await client.post("/api/auth/recover/reset",
                          json={"reset_token": v.json()["reset_token"],
                                "new_password": "brandnewpass123"})
    assert r.status_code == 200
    fl = await _force_logouts(events)
    assert fl and fl[-1]["reason"] == "password_reset" and fl[-1]["user_id"] == test_user.id
    rts = (await async_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == test_user.id))).scalars().all()
    assert rts and all(t.revoked_at is not None for t in rts)
    old = await client.post("/api/auth/token",
                            data={"username": "testuser", "password": "testpass123"})
    assert old.status_code == 401
    new = await client.post("/api/auth/token",
                            data={"username": "testuser", "password": "brandnewpass123"})
    assert new.status_code == 200
    again = await client.post("/api/auth/recover/reset",
                              json={"reset_token": v.json()["reset_token"],
                                    "new_password": "anotherpass123"})
    assert again.status_code == 400                        # pwd_reset usage unique (pfp)


async def test_change_password_revokes_sessions(client, test_user, user_token, async_session, monkeypatch):
    from app.models import RefreshToken
    events = await _capture_sse(monkeypatch)
    # Un refresh token vivant doit exister pour prouver la révocation
    login = await client.post("/api/auth/token",
                              data={"username": "testuser", "password": "testpass123"})
    assert login.status_code == 200
    r = await client.put("/api/auth/change-password", headers=get_auth_header(user_token),
                         json={"current_password": "testpass123", "new_password": "newpass456"})
    assert r.status_code == 200
    fl = await _force_logouts(events)
    assert fl and fl[-1]["reason"] == "password_changed" and fl[-1]["user_id"] == test_user.id
    rts = (await async_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == test_user.id))).scalars().all()
    assert rts and all(t.revoked_at is not None for t in rts)


async def test_admin_password_patch_revokes_sessions(client, test_user, admin_token, async_session, monkeypatch):
    from app.models import RefreshToken
    events = await _capture_sse(monkeypatch)
    await client.post("/api/auth/token",
                      data={"username": "testuser", "password": "testpass123"})
    r = await client.patch(f"/api/users/{test_user.id}", headers=get_auth_header(admin_token),
                           json={"password": "newadminset123"})
    assert r.status_code == 200
    fl = await _force_logouts(events)
    assert fl and fl[-1]["reason"] == "password_reset"
    rts = (await async_session.execute(
        select(RefreshToken).where(RefreshToken.user_id == test_user.id))).scalars().all()
    assert rts and all(t.revoked_at is not None for t in rts)


async def test_admin_patch_without_password_keeps_sessions(client, test_user, admin_token, monkeypatch):
    events = await _capture_sse(monkeypatch)
    r = await client.patch(f"/api/users/{test_user.id}", headers=get_auth_header(admin_token),
                           json={"prenom": "Jeanne"})
    assert r.status_code == 200
    assert await _force_logouts(events) == []              # Review Focus #3


async def test_delete_admin_who_generated_code_succeeds(tmp_path):
    """Fix revue : FK created_by_user_id doit être ON DELETE SET NULL (idiome repo).

    La suppression utilisateur est un hard delete : sans ondelete, un admin
    ayant généré un code pour un AUTRE utilisateur devient insoluble
    (IntegrityError → 500) — la ligne de code survit à la suppression du
    générateur (cascade uniquement par user_id). SQLite ignore les FK par
    défaut : on les active via un listener `connect` attaché AVANT la
    première connexion du moteur dédié (pragma effectif).
    """
    import asyncio
    from datetime import timedelta
    from sqlalchemy import event as sa_event, select as sa_select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.database import Base
    from app.models import User, utcnow

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fk_test.db")

    @sa_event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("pragma foreign_keys=ON")

    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)

    try:
        async with maker() as session:
            generator = User(username="gen_admin", email="gen@example.com",
                             hashed_password="x" * 60, is_active=True, is_admin=True)
            target = User(username="target", email="target@example.com",
                          hashed_password="x" * 60, is_active=True)
            session.add_all([generator, target])
            await session.commit()
            await session.refresh(generator)
            await session.refresh(target)

            # Le code généré pointe vers la CIBLE (user_id) et le GÉNÉRATEUR
            # (created_by) — deux utilisateurs distincts, cas réel.
            session.add(PasswordResetCode(
                user_id=target.id,
                code_hash="hash",
                expires_at=utcnow() + timedelta(minutes=15),
                created_by_user_id=generator.id,
            ))
            await session.commit()

            await session.delete(generator)
            try:
                await session.commit()
            except IntegrityError:
                raise AssertionError(
                    "FK sans ON DELETE SET NULL : suppression du générateur impossible (bug revue #1)")

            row = (await session.execute(
                sa_select(PasswordResetCode).where(
                    PasswordResetCode.created_by_user_id.is_(None)))).scalar_one()
            assert row is not None      # l'historique survit, référence neutralisée
    finally:
        await eng.dispose()


async def test_generate_expires_at_is_utc_aware(client, test_user, admin_token):
    """Fix revue #2 : expires_at doit porter le décalage UTC explicite.

    Un ISO naïf est interprété en heure LOCALE par new Date() côté navigateur :
    l'admin verrait une validité surestimée de 1-2 h sur l'écran de remise.
    """
    r = await client.post(GEN.format(uid=test_user.id), headers=get_auth_header(admin_token))
    assert r.status_code == 200
    expires_at = r.json()["expires_at"]
    assert expires_at.endswith("+00:00") or expires_at.endswith("Z"), \
        f"expires_at naïf (heure locale navigateur) : {expires_at}"
