# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du moniteur de vivacité du token GitHub (github_token_monitor).

Tâche périodique : GET /user sur api.github.com avec le token courant
(env prioritaire, sinon app_settings.github_token). Alerte aux admins
(UserMessage system/warning + audit) UNIQUEMENT sur la transition
ok→invalid. Anti-spam par état persisté (AppSetting). Erreurs réseau
transitoires non-alarmantes et sans effet sur l'état.
"""
import asyncio

import httpx
import pytest
from sqlalchemy import select

from app import github_token_monitor as mon
from app.models import AppSetting, UserMessage


def _transport(status: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.url.path == "/user"
        return httpx.Response(status, json={})
    return httpx.MockTransport(handler)


async def _set_state(db, key: str, value: str) -> None:
    db.add(AppSetting(key=key, value=value))
    await db.commit()


async def _get_state(db, key: str):
    row = await db.get(AppSetting, key)
    return row.value if row else None


# ─── Fonction pure : décision d'alerte ────────────────────────────────

class TestDecideAlert:

    def test_valid_rearme_et_n_alerte_pas(self):
        assert mon.decide_alert("ok", "valid") == ("ok", False)
        assert mon.decide_alert("alerted", "valid") == ("ok", False)
        assert mon.decide_alert(None, "valid") == ("ok", False)

    def test_invalid_alerte_sur_transition_puis_silence(self):
        assert mon.decide_alert("ok", "invalid") == ("alerted", True)
        assert mon.decide_alert(None, "invalid") == ("alerted", True)
        assert mon.decide_alert("alerted", "invalid") == ("alerted", False)

    def test_missing_rearme_sans_alerte(self):
        assert mon.decide_alert("ok", "missing") == ("ok", False)
        assert mon.decide_alert("alerted", "missing") == ("ok", False)

    def test_error_inchangeant_et_muet(self):
        """Une panne réseau ne déclenche rien et ne réarme pas l'anti-spam."""
        assert mon.decide_alert("ok", "error") == ("ok", False)
        assert mon.decide_alert("alerted", "error") == ("alerted", False)


# ─── check_token : classification du statut ───────────────────────────

class TestCheckToken:

    async def test_200_est_valid(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ok")
        assert await mon.check_token(async_session, transport=_transport(200)) == "valid"

    async def test_401_et_403_sont_invalid(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ko")
        assert await mon.check_token(async_session, transport=_transport(401)) == "invalid"
        assert await mon.check_token(async_session, transport=_transport(403)) == "invalid"

    async def test_5xx_est_error_non_alarmant(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ok")
        assert await mon.check_token(async_session, transport=_transport(502)) == "error"

    async def test_sans_token_nulle_part_est_missing(
            self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "")
        assert await mon.check_token(async_session, transport=_transport(200)) == "missing"

    async def test_erreur_reseau_est_error(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ok")

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        result = await mon.check_token(async_session, transport=httpx.MockTransport(boom))
        assert result == "error"

    async def test_token_env_envoye_en_bearer(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_env_token")
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={})

        await mon.check_token(async_session, transport=httpx.MockTransport(handler))
        assert seen == ["Bearer ghp_env_token"]

    async def test_fallback_token_db(self, async_session, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "")
        await _set_state(async_session, "github_token", "ghp_db_token")
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={})

        result = await mon.check_token(async_session, transport=httpx.MockTransport(handler))
        assert result == "valid"
        assert seen == ["Bearer ghp_db_token"]


# ─── run_check_once : orchestration, persistance, alertes ─────────────

class TestRunCheckOnce:

    async def test_transition_ok_vers_invalid_alerte_les_admins(
            self, async_session, admin_user, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ko")
        result, alert = await mon.run_check_once(
            async_session, transport=_transport(401))

        assert (result, alert) == ("invalid", True)

        msgs = (await async_session.execute(
            select(UserMessage).where(UserMessage.target_user_id == admin_user.id)
        )).scalars().all()
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.sender_type == "system"
        assert msg.priority == "warning"
        assert msg.title
        assert msg.action_url  # lien vers la vue de correction

        assert await _get_state(async_session, "github_token_alert_state") == "alerted"
        assert await _get_state(async_session, "github_token_last_result") == "invalid"
        assert await _get_state(async_session, "github_token_last_check")

    async def test_pas_de_re_alerte_tant_que_deja_alerte(
            self, async_session, admin_user, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ko")
        await _set_state(async_session, "github_token_alert_state", "alerted")
        result, alert = await mon.run_check_once(
            async_session, transport=_transport(401))

        assert (result, alert) == ("invalid", False)
        msgs = (await async_session.execute(select(UserMessage))).scalars().all()
        assert len(msgs) == 0

    async def test_retour_valid_rearme_sans_message(
            self, async_session, admin_user, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ok")
        await _set_state(async_session, "github_token_alert_state", "alerted")
        result, alert = await mon.run_check_once(
            async_session, transport=_transport(200))

        assert (result, alert) == ("valid", False)
        assert await _get_state(async_session, "github_token_alert_state") == "ok"
        msgs = (await async_session.execute(select(UserMessage))).scalars().all()
        assert len(msgs) == 0

    async def test_erreur_reseau_ne_change_pas_letat(
            self, async_session, admin_user, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token", "ghp_ok")

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        result, alert = await mon.run_check_once(
            async_session, transport=httpx.MockTransport(boom))

        assert (result, alert) == ("error", False)
        # une erreur réseau ne doit jamais armer l'état d'alerte
        assert await _get_state(async_session, "github_token_alert_state") != "alerted"
        # le dernier résultat est tout de même journalisé pour l'UI
        assert await _get_state(async_session, "github_token_last_result") == "error"


# ─── Boucle périodique ────────────────────────────────────────────────

class TestPeriodicLoop:

    async def test_kill_switch_desactive_le_moniteur(self, settings, monkeypatch):
        monkeypatch.setattr(settings, "github_token_check_interval", 0)
        # la boucle doit rendre la main immédiatement (pas de sleep 3600)
        await asyncio.wait_for(mon.periodic_github_token_check(), timeout=1.0)
