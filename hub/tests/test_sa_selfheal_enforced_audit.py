"""
Incidents SA M2M des 09/11 et 16/11/2026 — self-healing enforced + audit des mutations.

T1. Self-healing enforced : une app en mode « enforced » dont le SA owner a
    disparu (supprimé) doit pouvoir se réinscrire SANS token M2M depuis le
    réseau Docker — la branche T1 (m2m_sa_recreated) recrée alors le SA et
    retourne des credentials frais.
    Avant fix : 401 « Token M2M requis » (gate apps.py:1407) → app bloquée en
    chicken-and-egg (incident du 16/11/2026, serveur distant).

T2. Garde-fou : si le SA owner existe toujours, la réinscription sans token
    en enforced doit rester refusée (401) — on n'ouvre pas le gate plus large.

T3-T5. Audit des mutations de service accounts : PUT (bascule is_active),
    DELETE et regenerate-secret doivent produire une entrée AuditLog.
    Avant fix : seul can_admin_read était audité — désactivations et
    suppressions de SA non imputables (comptes de service désactivés et
    supprimés le 16/11 sans trace, serveur distant).
"""
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.models import Application, ServiceAccount, AuditLog


def _docker_ip():
    return patch('app.routes.apps.is_docker_network_ip', return_value=True)


def _make_sa(name: str, client_id: str, is_active: bool = True) -> ServiceAccount:
    return ServiceAccount(
        name=name,
        client_id=client_id,
        client_secret_hash="test-hash",
        is_active=is_active,
    )


# ---------------------------------------------------------------------------
# T1 — Self-healing enforced (SA owner absent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_without_token_selfheals_when_owner_sa_absent(client, async_session):
    """Enforced + SA owner supprimé + réinscription sans token → 200 + SA recréé."""
    app = Application(
        name="Orphan App",
        slug="orphan-app",
        api_url="http://localhost:8000",
        is_active=True,
        m2m_enforcement_mode="enforced",
    )
    async_session.add(app)
    await async_session.commit()

    with _docker_ip():
        response = await client.post(
            "/api/apps/register",
            json={"slug": "orphan-app", "api_port": 8080, "frontend_port": 3000},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["credentials"] is not None
    assert data["credentials"]["client_id"]
    assert data["credentials"]["client_secret"]

    # Le SA owner a été recréé par la branche T1 (m2m_sa_recreated)
    res = await async_session.execute(
        select(ServiceAccount).where(ServiceAccount.name == "SA - Orphan App")
    )
    recreated = res.scalar_one_or_none()
    assert recreated is not None
    assert recreated.is_active is True

    # La recréation est auditée
    res_audit = await async_session.execute(
        select(AuditLog).where(AuditLog.action == "m2m_sa_recreated")
    )
    assert res_audit.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_register_without_token_still_rejected_when_owner_sa_present(client, async_session):
    """Enforced + SA owner présent + réinscription sans token → 401 (inchangé)."""
    app = Application(
        name="Guard App",
        slug="guard-app",
        api_url="http://localhost:8000",
        is_active=True,
        m2m_enforcement_mode="enforced",
    )
    sa = _make_sa("SA - Guard App", "sa_guard000000000000000000001", True)
    sa.applications.append(app)
    async_session.add(sa)
    await async_session.commit()

    with _docker_ip():
        response = await client.post(
            "/api/apps/register",
            json={"slug": "guard-app", "api_port": 8080, "frontend_port": 3000},
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T3 — Audit de la bascule is_active (PUT)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_is_active_toggle_is_audited(client, async_session, admin_user, admin_token):
    sa = _make_sa("SA - Audit Toggle", "sa_audittoggle00000000000001", True)
    async_session.add(sa)
    await async_session.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.put(
        f"/api/service-accounts/{sa.id}", json={"is_active": False}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    res = await async_session.execute(
        select(AuditLog).where(
            AuditLog.action == "sa_deactivated",
            AuditLog.service_account_id == sa.id,
        )
    )
    assert res.scalar_one_or_none() is not None

    resp2 = await client.put(
        f"/api/service-accounts/{sa.id}", json={"is_active": True}, headers=headers
    )
    assert resp2.status_code == 200, resp2.text
    res2 = await async_session.execute(
        select(AuditLog).where(
            AuditLog.action == "sa_reactivated",
            AuditLog.service_account_id == sa.id,
        )
    )
    assert res2.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# T4 — Audit de la suppression (DELETE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_sa_is_audited(client, async_session, admin_user, admin_token):
    sa = _make_sa("SA - Audit Delete", "sa_auditdelete0000000000001", True)
    async_session.add(sa)
    await async_session.commit()
    sa_id = sa.id
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.delete(f"/api/service-accounts/{sa_id}", headers=headers)
    assert resp.status_code == 204, resp.text

    # Le SA n'existe plus → la ref FK passera à NULL : on audite par target/name
    res = await async_session.execute(
        select(AuditLog).where(
            AuditLog.action == "sa_deleted",
            AuditLog.target == "SA - Audit Delete",
        )
    )
    assert res.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# T5 — Audit de la régénération de secret
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_secret_is_audited(client, async_session, admin_user, admin_token):
    sa = _make_sa("SA - Audit Regen", "sa_auditregen00000000000001", True)
    async_session.add(sa)
    await async_session.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.post(
        f"/api/service-accounts/{sa.id}/regenerate-secret", headers=headers
    )
    assert resp.status_code == 200, resp.text

    res = await async_session.execute(
        select(AuditLog).where(
            AuditLog.action == "sa_secret_regenerated",
            AuditLog.service_account_id == sa.id,
        )
    )
    assert res.scalar_one_or_none() is not None
