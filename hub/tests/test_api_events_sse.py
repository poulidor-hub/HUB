"""
Tests de la cadence du heartbeat SSE (/api/events/stream).

Contexte (août 2026) : Firefox 154 coupe une connexion SSE active environ
25-29 s après le dernier octet reçu (reproduit en labo headless, profil
vierge, indépendamment de nginx/hub-api). Avec un heartbeat de 30 s, la
connexion mourait entre deux pings — déconnexions toutes les ~55 s observées
en production. Le heartbeat doit rester strictement inférieur à ce seuil.

Note harness : on pilote l'app en ASGI brut (scope/receive/send) plutôt
qu'avec httpx ASGITransport, car le receive() de httpx renvoie
http.disconnect dès que le body de requête est épuisé — un GET sans body
voit donc une « déconnexion » immédiate remonter dans is_disconnected(),
ce qui tue le générateur artificiellement.
"""
import asyncio
import contextlib

import pytest

from app.main import app
from app.routes.auth import generate_beacon_token

# Fenêtre de test : le premier ping doit arriver en moins de 22 s
# (heartbeat sain ≤ 20 s ; seuil de coupure Firefox ~25-29 s).
FIRST_PING_TIMEOUT_S = 22


def _http_scope(path_qs: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path_qs.split("?", 1)[0],
        "raw_path": path_qs.split("?", 1)[0].encode(),
        "query_string": path_qs.split("?", 1)[1].encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


async def _collecter_premier_ping(path_qs: str, timeout_s: float) -> bool:
    """Conduit l'app en ASGI brut avec un client toujours connecté
    (receive() bloque indéfiniment, comme un vrai navigateur) et attend
    le premier chunk body contenant un ping."""
    loop = asyncio.get_running_loop()
    got_ping = loop.create_future()
    status_holder = {}

    async def receive():
        # Client jamais déconnecté : aucune frame http.disconnect,
        # l'attente est levée par l'annulation (is_disconnected).
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            status_holder["status"] = message["status"]
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if b'"ping"' in body and not got_ping.done():
                got_ping.set_result(True)

    task = loop.create_task(app(_http_scope(path_qs), receive, send))
    try:
        await asyncio.wait_for(got_ping, timeout=timeout_s)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestSSEHeartbeatCadence:
    """Le heartbeat SSE doit battre plus vite que le seuil de coupure Firefox."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_premier_ping_sous_22s(self):
        """
        Un client SSE authentifié (beacon_token) doit recevoir un premier
        ping en moins de 22 s, sinon Firefox tue la connexion entre deux
        pings (~25-29 s après le dernier octet reçu).
        """
        # user_id factice : beacon HMAC valide, aucune ligne de session en DB
        user_id = 999999
        beacon = generate_beacon_token(user_id)
        path_qs = f"/api/events/stream?user_id={user_id}&beacon_token={beacon}"

        ping_recu = await _collecter_premier_ping(path_qs, FIRST_PING_TIMEOUT_S)

        assert ping_recu, (
            "Aucun ping SSE reçu en 22 s : le heartbeat est trop espacé. "
            "Firefox coupe la connexion ~25-29 s après le dernier octet reçu. "
            "Vérifier asyncio.wait_for(queue.get(), timeout=...) dans "
            "app/routes/events.py."
        )
