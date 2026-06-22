"""
Tests du système de demande de compte (account requests).

Couvre :
- Soumission d'une demande (validations, normalisation username)
- Unicité username vs comptes existants et demandes pending
- Re-demande après refus
- Polling de statut
- Approbation et auto-login token
- Rejet avec raison obligatoire
- Endpoint auto-login à usage unique
- Endpoints admin (listing, count)
"""

import pytest
import json
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User, AccountRequest, AppSetting, UserMessage
from app.auth import get_password_hash, create_access_token
from tests.conftest import get_auth_header


# =============================================================================
# Helpers
# =============================================================================

@pytest.fixture(autouse=True)
async def _ensure_account_requests_enabled(async_session: AsyncSession):
    """Injecte le setting account_requests_enabled=true pour tous les tests."""
    async_session.add(AppSetting(key="account_requests_enabled", value="true"))
    await async_session.commit()


async def submit_request(
    client: AsyncClient,
    first_name: str = "Jean",
    last_name: str = "Dupont",
    username: str = "jean.dupont",
    password: str = "securepass123",
):
    """Helper pour soumettre une demande de compte."""
    return await client.post(
        "/api/account-requests/",
        json={
            "first_name": first_name,
            "last_name": last_name,
            "username": username,
            "password": password,
        },
    )


async def set_notification_admins(db: AsyncSession, admin_ids: list[int]):
    """Configure les admins notifies pour les demandes de compte."""
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == "account_request_notification_admin_ids")
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = json.dumps(admin_ids)
    else:
        db.add(AppSetting(
            key="account_request_notification_admin_ids",
            value=json.dumps(admin_ids),
        ))
    await db.commit()


async def create_admin(
    db: AsyncSession,
    username: str,
    is_active: bool = True,
) -> tuple[User, str]:
    """Cree un admin de test et son token."""
    admin = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=get_password_hash("admin123456"),
        is_active=is_active,
        is_admin=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    token, _, _ = create_access_token(username=admin.username, apps={})
    return admin, token


# =============================================================================
# Soumission de demande
# =============================================================================

class TestSubmitAccountRequest:
    """Tests de l'endpoint POST /api/account-requests/"""

    async def test_submit_success(self, client: AsyncClient):
        """Soumettre une demande valide retourne 201 avec request_id et polling_token."""
        resp = await submit_request(client)
        assert resp.status_code == 201
        data = resp.json()
        assert "request_id" in data
        assert "polling_token" in data
        assert isinstance(data["request_id"], int)
        assert len(data["polling_token"]) == 64  # hex(32)

    async def test_submit_normalizes_username(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Le username est normalisé (strip + lower) avant stockage."""
        resp = await submit_request(client, username="  Jean.DUPONT  ")
        assert resp.status_code == 201
        rid = resp.json()["request_id"]

        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        assert req.username == "jean.dupont"

    async def test_submit_duplicate_existing_user(
        self, client: AsyncClient, test_user: User
    ):
        """Refusé si le username existe déjà dans la table users."""
        resp = await submit_request(client, username=test_user.username)
        assert resp.status_code == 400
        assert "déjà utilisé" in resp.json()["detail"]

    async def test_submit_duplicate_existing_user_case_insensitive(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Refusé même si la casse diffère (normalisation)."""
        user = User(
            username="bob.martin",
            hashed_password=get_password_hash("pass12345678"),
            is_active=True,
        )
        async_session.add(user)
        await async_session.commit()

        resp = await submit_request(client, username="Bob.MARTIN")
        assert resp.status_code == 400
        assert "déjà utilisé" in resp.json()["detail"]

    async def test_submit_duplicate_pending_request(self, client: AsyncClient):
        """Refusé si une demande pending existe déjà pour ce username."""
        resp1 = await submit_request(client)
        assert resp1.status_code == 201

        resp2 = await submit_request(client)
        assert resp2.status_code == 400
        assert "déjà en cours" in resp2.json()["detail"]

    async def test_submit_duplicate_pending_with_spaces(self, client: AsyncClient):
        """Même avec des espaces parasites, la collision est détectée."""
        resp1 = await submit_request(client, username="jean.dupont")
        assert resp1.status_code == 201

        resp2 = await submit_request(client, username="  jean.dupont  ")
        assert resp2.status_code == 400
        assert "déjà en cours" in resp2.json()["detail"]

    async def test_submit_after_rejection_allowed(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Après un refus, une nouvelle demande avec le même username est acceptée."""
        resp1 = await submit_request(client)
        assert resp1.status_code == 201
        rid = resp1.json()["request_id"]

        # Passer la demande en rejetée directement en base
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        req.status = "rejected"
        req.rejection_reason = "Identifiant inconnu"
        await async_session.commit()

        # Nouvelle demande = acceptée
        resp2 = await submit_request(client)
        assert resp2.status_code == 201
        assert resp2.json()["request_id"] != rid

    async def test_submit_password_too_short(self, client: AsyncClient):
        """Mot de passe < 8 caractères est rejeté par la validation Pydantic."""
        resp = await submit_request(client, password="short")
        assert resp.status_code == 422

    async def test_submit_username_too_short(self, client: AsyncClient):
        """Username < 3 caractères rejeté."""
        resp = await submit_request(client, username="ab")
        assert resp.status_code == 422


# =============================================================================
# Polling de statut
# =============================================================================

class TestPollStatus:
    """Tests de l'endpoint GET /api/account-requests/{id}/status"""

    async def test_poll_pending(self, client: AsyncClient):
        """Polling d'une demande pending retourne status=pending."""
        resp = await submit_request(client)
        data = resp.json()

        poll = await client.get(
            f"/api/account-requests/{data['request_id']}/status",
            params={"token": data["polling_token"]},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "pending"
        assert poll.json().get("auto_login_token") is None

    async def test_poll_wrong_token(self, client: AsyncClient):
        """Polling avec un mauvais token retourne 404."""
        resp = await submit_request(client)
        data = resp.json()

        poll = await client.get(
            f"/api/account-requests/{data['request_id']}/status",
            params={"token": "wrong_token"},
        )
        assert poll.status_code == 404

    async def test_poll_approved_returns_auto_login_token(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Après approbation, le polling retourne auto_login_token."""
        resp = await submit_request(client)
        data = resp.json()
        rid = data["request_id"]

        # Simuler approbation en BDD
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        req.status = "approved"
        req.auto_login_token = "test_token_abc123"
        await async_session.commit()

        poll = await client.get(
            f"/api/account-requests/{rid}/status",
            params={"token": data["polling_token"]},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "approved"
        assert poll.json()["auto_login_token"] == "test_token_abc123"

    async def test_poll_rejected_shows_reason(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Après rejet, le polling retourne la raison du refus."""
        resp = await submit_request(client)
        data = resp.json()
        rid = data["request_id"]

        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        req.status = "rejected"
        req.rejection_reason = "Identifiant inconnu"
        await async_session.commit()

        poll = await client.get(
            f"/api/account-requests/{rid}/status",
            params={"token": data["polling_token"]},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "rejected"
        assert poll.json()["rejection_reason"] == "Identifiant inconnu"
        assert poll.json().get("auto_login_token") is None


# =============================================================================
# Approbation par admin
# =============================================================================

class TestApproveRequest:
    """Tests de l'endpoint POST /api/account-requests/{id}/approve"""

    async def test_approve_creates_user(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """L'approbation crée un User en base et génère un auto_login_token."""
        resp = await submit_request(client, username="nouveau.user")
        rid = resp.json()["request_id"]

        approve = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert approve.status_code == 200
        data = approve.json()
        assert data["username"] == "nouveau.user"
        assert "user_id" in data

        # Vérifier que le User existe en base
        result = await async_session.execute(
            select(User).where(User.username == "nouveau.user")
        )
        user = result.scalar_one()
        assert user.is_active is True
        assert user.is_admin is False

        # Vérifier que l'auto_login_token a été généré
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        assert req.status == "approved"
        assert req.auto_login_token is not None
        assert len(req.auto_login_token) == 64

    async def test_approve_already_treated(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Approuver une demande déjà traitée retourne 400."""
        resp = await submit_request(client, username="already.done")
        rid = resp.json()["request_id"]

        # Première approbation OK
        r1 = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert r1.status_code == 200

        # Deuxième = erreur
        r2 = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert r2.status_code == 400
        assert "déjà été traitée" in r2.json()["detail"]

    async def test_approve_requires_admin(
        self,
        client: AsyncClient,
        test_user: User,
        user_token: str,
    ):
        """Un utilisateur non-admin ne peut pas approuver."""
        resp = await submit_request(client, username="some.user")
        rid = resp.json()["request_id"]

        r = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(user_token),
        )
        assert r.status_code == 403

    async def test_approve_username_taken_meanwhile(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Si le username est pris entre demande et approbation, erreur 400."""
        resp = await submit_request(client, username="race.condition")
        rid = resp.json()["request_id"]

        # Créer le user entre-temps
        u = User(
            username="race.condition",
            hashed_password=get_password_hash("password123"),
            is_active=True,
        )
        async_session.add(u)
        await async_session.commit()

        r = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "existe déjà" in r.json()["detail"]

    async def test_approve_with_corrected_username(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """L'admin peut corriger l'identifiant ; le mot de passe est conservé."""
        resp = await submit_request(client, username="mauvais.id", password="userpass123")
        rid = resp.json()["request_id"]

        # Récupérer le hash de la demande (mot de passe choisi par l'utilisateur)
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        original_hash = result.scalar_one().hashed_password

        approve = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={"username": "Bon.ID"},
            headers=get_auth_header(admin_token),
        )
        assert approve.status_code == 200
        assert approve.json()["username"] == "bon.id"

        # Le User est créé avec l'identifiant corrigé et le mot de passe d'origine
        result = await async_session.execute(
            select(User).where(User.username == "bon.id")
        )
        user = result.scalar_one()
        assert user.hashed_password == original_hash

        # L'ancien identifiant ne doit pas exister
        result = await async_session.execute(
            select(User).where(User.username == "mauvais.id")
        )
        assert result.scalar_one_or_none() is None

        # La demande reflète l'identifiant réellement créé
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        assert req.username == "bon.id"
        assert req.status == "approved"

    async def test_approve_corrected_username_already_taken(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Une correction vers un identifiant déjà utilisé est refusée."""
        existing = User(
            username="deja.pris",
            hashed_password=get_password_hash("password123"),
            is_active=True,
        )
        async_session.add(existing)
        await async_session.commit()

        resp = await submit_request(client, username="demande.libre")
        rid = resp.json()["request_id"]

        r = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={"username": "deja.pris"},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "existe déjà" in r.json()["detail"]


# =============================================================================
# Rejet par admin
# =============================================================================

class TestRejectRequest:
    """Tests de l'endpoint POST /api/account-requests/{id}/reject"""

    async def test_reject_success(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Le rejet nécessite une raison et met à jour la demande."""
        resp = await submit_request(client, username="to.reject")
        rid = resp.json()["request_id"]

        r = await client.post(
            f"/api/account-requests/{rid}/reject",
            json={"rejection_reason": "Identifiant inconnu du SI"},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200

        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        assert req.status == "rejected"
        assert req.rejection_reason == "Identifiant inconnu du SI"
        assert req.reviewed_by_id == admin_user.id

    async def test_reject_without_reason_fails(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """Le rejet sans raison est refusé."""
        resp = await submit_request(client, username="no.reason")
        rid = resp.json()["request_id"]

        r = await client.post(
            f"/api/account-requests/{rid}/reject",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 400
        assert "raison" in r.json()["detail"].lower()

    async def test_reject_blank_reason_fails(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """Le rejet avec raison vide est refusé."""
        resp = await submit_request(client, username="blank.reason")
        rid = resp.json()["request_id"]

        r = await client.post(
            f"/api/account-requests/{rid}/reject",
            json={"rejection_reason": "   "},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 400


# =============================================================================
# Auto-login token (usage unique)
# =============================================================================

class TestAutoLogin:
    """Tests de l'endpoint POST /api/account-requests/auto-login"""

    async def test_auto_login_success(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """L'échange d'un auto_login_token valide retourne JWT + refresh."""
        resp = await submit_request(client, username="autologin.user")
        rid = resp.json()["request_id"]
        polling_token = resp.json()["polling_token"]

        # Approuver
        await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )

        # Récupérer l'auto_login_token via polling
        poll = await client.get(
            f"/api/account-requests/{rid}/status",
            params={"token": polling_token},
        )
        alt = poll.json()["auto_login_token"]
        assert alt is not None

        # Échanger contre un JWT
        login_resp = await client.post(
            "/api/account-requests/auto-login",
            json={"auto_login_token": alt},
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert "access_token" in login_data
        assert "refresh_token" in login_data
        assert login_data["token_type"] == "bearer"

    async def test_auto_login_single_use(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """Un auto_login_token ne peut être utilisé qu'une seule fois."""
        resp = await submit_request(client, username="oneshot.user")
        rid = resp.json()["request_id"]
        polling_token = resp.json()["polling_token"]

        await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )

        poll = await client.get(
            f"/api/account-requests/{rid}/status",
            params={"token": polling_token},
        )
        alt = poll.json()["auto_login_token"]

        # Premier usage = OK
        r1 = await client.post(
            "/api/account-requests/auto-login",
            json={"auto_login_token": alt},
        )
        assert r1.status_code == 200

        # Deuxième usage = 401
        r2 = await client.post(
            "/api/account-requests/auto-login",
            json={"auto_login_token": alt},
        )
        assert r2.status_code == 401

    async def test_auto_login_invalid_token(self, client: AsyncClient):
        """Un token inventé retourne 401."""
        r = await client.post(
            "/api/account-requests/auto-login",
            json={"auto_login_token": "invalid_xxx"},
        )
        assert r.status_code == 401

    async def test_auto_login_empty_token(self, client: AsyncClient):
        """Un token vide retourne 400."""
        r = await client.post(
            "/api/account-requests/auto-login",
            json={"auto_login_token": ""},
        )
        assert r.status_code == 400


# =============================================================================
# Endpoints admin : listing et count
# =============================================================================

class TestAdminEndpoints:
    """Tests des endpoints admin (listing, count)."""

    async def test_pending_count(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """GET /count retourne le nombre de demandes pending."""
        await set_notification_admins(async_session, [admin_user.id])

        # D'abord 0
        r0 = await client.get(
            "/api/account-requests/count",
            headers=get_auth_header(admin_token),
        )
        assert r0.status_code == 200
        assert r0.json()["count"] == 0

        # Soumettre 2 demandes
        await submit_request(client, username="count.one")
        await submit_request(client, username="count.two")

        r2 = await client.get(
            "/api/account-requests/count",
            headers=get_auth_header(admin_token),
        )
        assert r2.json()["count"] == 2

    async def test_pending_count_zero_for_unselected_admin(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Un admin non selectionne ne voit pas de badge pending."""
        other_admin, _ = await create_admin(async_session, "requests.receiver")
        await set_notification_admins(async_session, [other_admin.id])
        await submit_request(client, username="hidden.count")

        r = await client.get(
            "/api/account-requests/count",
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200
        assert r.json()["count"] == 0

    async def test_list_requests(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
    ):
        """GET / retourne la liste paginée des demandes."""
        await submit_request(client, username="list.user1")
        await submit_request(client, username="list.user2")

        r = await client.get(
            "/api/account-requests/",
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 2
        assert "requests" in data
        assert any(req["username"] == "list.user1" for req in data["requests"])

    async def test_list_filter_by_status(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Le filtre par statut fonctionne."""
        resp = await submit_request(client, username="filter.test")
        rid = resp.json()["request_id"]

        # Approuver
        await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )

        # Lister uniquement les approved
        r = await client.get(
            "/api/account-requests/",
            params={"status": "approved"},
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200
        for req in r.json()["requests"]:
            assert req["status"] == "approved"

    async def test_list_requires_admin(
        self,
        client: AsyncClient,
        test_user: User,
        user_token: str,
    ):
        """Un utilisateur non-admin ne peut pas lister les demandes."""
        r = await client.get(
            "/api/account-requests/",
            headers=get_auth_header(user_token),
        )
        assert r.status_code == 403

    async def test_count_requires_admin(
        self,
        client: AsyncClient,
        test_user: User,
        user_token: str,
    ):
        """Un utilisateur non-admin ne peut pas voir le count."""
        r = await client.get(
            "/api/account-requests/count",
            headers=get_auth_header(user_token),
        )
        assert r.status_code == 403


# =============================================================================
# Configuration des admins notifies
# =============================================================================

class TestNotificationAdmins:
    """Tests des destinataires de notifications des demandes de compte."""

    async def test_get_notification_admins_admin_only(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        test_user: User,
        user_token: str,
        async_session: AsyncSession,
    ):
        """Un admin lit la configuration, un non-admin est refuse."""
        await set_notification_admins(async_session, [admin_user.id])

        ok = await client.get(
            "/api/account-requests/notification-admins",
            headers=get_auth_header(admin_token),
        )
        assert ok.status_code == 200
        data = ok.json()
        assert data["selected_admin_ids"] == [admin_user.id]
        assert any(admin["id"] == admin_user.id for admin in data["admins"])
        assert all(admin["id"] != test_user.id for admin in data["admins"])

        denied = await client.get(
            "/api/account-requests/notification-admins",
            headers=get_auth_header(user_token),
        )
        assert denied.status_code == 403

    async def test_update_notification_admins_accepts_empty_list(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """Une liste vide est une configuration valide."""
        await set_notification_admins(async_session, [admin_user.id])

        resp = await client.put(
            "/api/account-requests/notification-admins",
            json={"admin_ids": []},
            headers=get_auth_header(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["selected_admin_ids"] == []

        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "account_request_notification_admin_ids")
        )
        setting = result.scalar_one()
        assert json.loads(setting.value) == []
        assert setting.updated_by_id == admin_user.id

    async def test_update_notification_admins_rejects_invalid_targets(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        test_user: User,
        async_session: AsyncSession,
    ):
        """Seuls les admins actifs peuvent etre selectionnes."""
        inactive_admin, _ = await create_admin(async_session, "inactive.admin", is_active=False)

        non_admin = await client.put(
            "/api/account-requests/notification-admins",
            json={"admin_ids": [test_user.id]},
            headers=get_auth_header(admin_token),
        )
        assert non_admin.status_code == 400

        inactive = await client.put(
            "/api/account-requests/notification-admins",
            json={"admin_ids": [inactive_admin.id]},
            headers=get_auth_header(admin_token),
        )
        assert inactive.status_code == 400

        missing = await client.put(
            "/api/account-requests/notification-admins",
            json={"admin_ids": [99999]},
            headers=get_auth_header(admin_token),
        )
        assert missing.status_code == 400

    async def test_update_notification_admins_requires_admin(
        self,
        client: AsyncClient,
        test_user: User,
        user_token: str,
    ):
        """Un non-admin ne peut pas modifier les destinataires."""
        resp = await client.put(
            "/api/account-requests/notification-admins",
            json={"admin_ids": []},
            headers=get_auth_header(user_token),
        )
        assert resp.status_code == 403

    async def test_submit_notifies_only_selected_admins(
        self,
        client: AsyncClient,
        admin_user: User,
        async_session: AsyncSession,
    ):
        """Une nouvelle demande cree un message seulement pour les admins selectionnes."""
        unselected_admin, _ = await create_admin(async_session, "unselected.admin")
        await set_notification_admins(async_session, [admin_user.id])

        resp = await submit_request(client, username="notify.selected")
        assert resp.status_code == 201

        result = await async_session.execute(
            select(UserMessage).where(UserMessage.title == "Nouvelle demande de compte")
        )
        messages = result.scalars().all()
        assert len(messages) == 1
        assert messages[0].target_user_id == admin_user.id
        assert messages[0].target_user_id != unselected_admin.id

    async def test_submit_with_empty_selection_notifies_nobody(
        self,
        client: AsyncClient,
        admin_user: User,
        async_session: AsyncSession,
    ):
        """Une selection vide ne cree aucun message admin."""
        await set_notification_admins(async_session, [])

        resp = await submit_request(client, username="notify.nobody")
        assert resp.status_code == 201

        result = await async_session.execute(
            select(UserMessage).where(UserMessage.title == "Nouvelle demande de compte")
        )
        assert result.scalars().all() == []


# =============================================================================
# Soumission avec setting désactivé
# =============================================================================

class TestSubmitWithSettingDisabled:
    """Vérifie que la soumission est bloquée quand account_requests_enabled = false."""

    async def _set_account_requests_enabled(self, db: AsyncSession, value: str):
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "account_requests_enabled")
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(AppSetting(key="account_requests_enabled", value=value))
        await db.commit()

    async def test_submit_disabled_returns_403(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Soumission refusée quand les demandes sont désactivées."""
        await self._set_account_requests_enabled(async_session, "false")

        resp = await submit_request(client, username="blocked.user")
        assert resp.status_code == 403
        assert "désactivées" in resp.json()["detail"]

    async def test_submit_no_setting_row_returns_403(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Soumission refusée quand le setting n'existe pas en BDD (sécurité fail-closed)."""
        # Supprimer le row injecté par la fixture autouse
        result = await async_session.execute(
            select(AppSetting).where(AppSetting.key == "account_requests_enabled")
        )
        existing = result.scalar_one_or_none()
        if existing:
            await async_session.delete(existing)
            await async_session.commit()

        resp = await submit_request(client, username="no.setting.user")
        assert resp.status_code == 403

    async def test_submit_reenabled_works(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """Après réactivation, la soumission fonctionne à nouveau."""
        await self._set_account_requests_enabled(async_session, "false")
        resp1 = await submit_request(client, username="toggle.user")
        assert resp1.status_code == 403

        await self._set_account_requests_enabled(async_session, "true")
        resp2 = await submit_request(client, username="toggle.user")
        assert resp2.status_code == 201


# =============================================================================
# Configuration du formulaire (message + champs)
# =============================================================================

class TestFormConfig:
    """Tests des endpoints /api/account-requests/form-config."""

    async def test_get_form_config_public_defaults(self, client: AsyncClient):
        """La config est lisible sans auth et renvoie des valeurs par défaut."""
        r = await client.get("/api/account-requests/form-config")
        assert r.status_code == 200
        data = r.json()
        assert data["show_first_name"] is True
        assert data["show_last_name"] is True
        assert data["show_email"] is False
        assert isinstance(data["important_message"], str)
        assert data["important_message"]  # message par défaut non vide

    async def test_update_form_config_admin(
        self, client: AsyncClient, admin_user: User, admin_token: str
    ):
        """Un admin met à jour la config ; la lecture publique la reflète."""
        r = await client.put(
            "/api/account-requests/form-config",
            json={
                "important_message": "Bonjour les amis",
                "show_first_name": False,
                "show_last_name": True,
                "show_email": True,
            },
            headers=get_auth_header(admin_token),
        )
        assert r.status_code == 200

        g = await client.get("/api/account-requests/form-config")
        d = g.json()
        assert d["important_message"] == "Bonjour les amis"
        assert d["show_first_name"] is False
        assert d["show_last_name"] is True
        assert d["show_email"] is True

    async def test_update_form_config_requires_admin(
        self, client: AsyncClient, test_user: User, user_token: str
    ):
        """Un non-admin ne peut pas modifier la config."""
        r = await client.put(
            "/api/account-requests/form-config",
            json={"important_message": "x"},
            headers=get_auth_header(user_token),
        )
        assert r.status_code == 403


# =============================================================================
# Email optionnel et noms optionnels
# =============================================================================

class TestRequestEmailAndOptionalNames:
    """Tests des champs optionnels (nom, prénom, email)."""

    async def test_submit_without_names_ok(self, client: AsyncClient):
        """La demande sans nom/prénom est acceptée (champs optionnels)."""
        r = await client.post(
            "/api/account-requests/",
            json={"username": "noname.user", "password": "securepass123"},
        )
        assert r.status_code == 201

    async def test_submit_with_email_stored(
        self, client: AsyncClient, async_session: AsyncSession
    ):
        """L'email fourni est stocké sur la demande."""
        r = await client.post(
            "/api/account-requests/",
            json={
                "username": "mail.user",
                "password": "securepass123",
                "email": "mail.user@example.com",
            },
        )
        assert r.status_code == 201
        rid = r.json()["request_id"]
        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        assert result.scalar_one().email == "mail.user@example.com"

    async def test_approve_sets_user_email(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """L'email de la demande est rattaché au compte créé."""
        r = await client.post(
            "/api/account-requests/",
            json={
                "username": "ue.user",
                "password": "securepass123",
                "email": "ue.user@example.com",
            },
        )
        rid = r.json()["request_id"]
        ap = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={},
            headers=get_auth_header(admin_token),
        )
        assert ap.status_code == 200
        result = await async_session.execute(
            select(User).where(User.username == "ue.user")
        )
        assert result.scalar_one().email == "ue.user@example.com"

    async def test_approve_email_and_names_override(
        self,
        client: AsyncClient,
        admin_user: User,
        admin_token: str,
        async_session: AsyncSession,
    ):
        """L'admin peut renseigner email/nom/prénom à l'approbation."""
        r = await client.post(
            "/api/account-requests/",
            json={"username": "ov.user", "password": "securepass123"},
        )
        rid = r.json()["request_id"]
        ap = await client.post(
            f"/api/account-requests/{rid}/approve",
            json={
                "email": "override@example.com",
                "first_name": "Jean",
                "last_name": "Test",
            },
            headers=get_auth_header(admin_token),
        )
        assert ap.status_code == 200

        result = await async_session.execute(
            select(User).where(User.username == "ov.user")
        )
        assert result.scalar_one().email == "override@example.com"

        result = await async_session.execute(
            select(AccountRequest).where(AccountRequest.id == rid)
        )
        req = result.scalar_one()
        assert req.first_name == "Jean"
        assert req.last_name == "Test"
        assert req.email == "override@example.com"
