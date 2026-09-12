"""Checksum socle — rapport d'intégration hub↔app en 7 contrôles.

Exécuté à la demande (vue santé) ou après ajout/maj d'une application :
vérifie en conditions réelles que le socle de l'app fonctionne avec le hub
(auth user via JWKS, M2M service account, préférences, discovery, backup,
versions). Aucun effet de bord : le round-trip préférences réécrit la valeur
lue, aucun backup n'est exécuté.

Le transport httpx est injectable (tests : MockTransport, zéro réseau).
"""
import logging
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("hub.socle_check")

CONTROL_TIMEOUT = 5.0  # secondes par contrôle — l'app est locale au parc


async def _request(client: httpx.AsyncClient, method: str, path: str,
                   token: str | None = None) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await client.request(method, path, headers=headers)
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload


async def _check_health(client: httpx.AsyncClient) -> tuple[str, str]:
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/health/detailed")
    if status != 200:
        return "fail", f"HTTP {status} sur /api/health/detailed"
    if not isinstance(payload, dict):
        return "fail", "réponse /api/health/detailed inattendue (non-JSON objet)"
    connected = payload.get("hub_connected") is True
    healthy = payload.get("status") == "healthy"
    if healthy and connected:
        return "pass", f"status healthy, hub_connected, version {payload.get('version')}"
    if not connected:
        return "fail", "app démarrée mais hub_connected=false (registration hub absente)"
    return "fail", f"status={payload.get('status')!r} (healthy attendu)"


async def _check_user_auth(client: httpx.AsyncClient, user_token: str) -> tuple[str, str]:
    """JWT user signé par le hub → prouve que l'app valide la signature (JWKS)."""
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/preferences", user_token)
    if status == 401:
        return "fail", "JWT user hub refusé (401) — validation JWKS en échec"
    if status == 403:
        return "fail", "JWT user accepté mais rôle refusé (403) — claim apps incorrect"
    if status != 200:
        return "fail", f"HTTP {status} sur /api/preferences avec JWT user"
    return "pass", "JWT user validé par l'app (endpoint protégé accessible)"


async def _check_m2m(client: httpx.AsyncClient, service_token: str) -> tuple[str, str]:
    """Token service account → prouve le canal machine-à-machine et son périmètre."""
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/m2m/catalog", service_token)
    if status == 401:
        return "fail", "token service refusé (401) — type service_account non validé"
    if status == 403:
        return "fail", "token service refusé (403) — périmètre M2M incorrect (claim apps)"
    if status != 200:
        return "fail", f"HTTP {status} sur /api/m2m/catalog avec token service"
    return "pass", "canal M2M opérationnel (service account accepté)"


async def _check_preferences(client: httpx.AsyncClient,
                             user_token: str) -> tuple[str, str]:
    """Round-trip sans effet de bord : lecture → réécriture à l'identique → relecture."""
    status, original = await _request(client, "GET", "/api/preferences", user_token)
    if status != 200:
        return "fail", f"lecture initiale impossible (HTTP {status})"
    # PUT avec le payload lu (idempotent) :
    resp = await client.request("PUT", "/api/preferences",
                                headers={"Authorization": f"Bearer {user_token}"},
                                json=original if isinstance(original, dict) else {})
    if resp.status_code != 200:
        return "fail", f"écriture refusée (HTTP {resp.status_code}) — permissions DB ?"
    status, _ = await _request(client, "GET", "/api/preferences", user_token)
    if status != 200:
        return "fail", f"relecture après écriture impossible (HTTP {status})"
    return "pass", "round-trip préférences OK (lecture/écriture/relecture)"


async def _check_discovery(client: httpx.AsyncClient, user_token: str) -> tuple[str, str]:
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/hub/data/tables", user_token)
    if status == 403:
        return "fail", "discovery refusée (403) — rôle admin non reconnu dans le claim apps"
    if status != 200:
        return "fail", f"HTTP {status} sur /api/hub/data/tables"
    if not isinstance(payload, dict) or "tables" not in payload:
        return "fail", "payload discovery inattendu (clé 'tables' absente)"
    return "pass", f"discovery OK ({len(payload.get('tables') or [])} tables exposées)"


async def _check_backup(client: httpx.AsyncClient, user_token: str) -> tuple[str, str]:
    """Endpoint de liste uniquement — aucun backup n'est exécuté."""
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/admin/backup/list", user_token)
    if status == 403:
        return "fail", "endpoint backup refusé (403) — garde admin non opérationnelle"
    if status != 200:
        return "fail", f"HTTP {status} sur /api/admin/backup/list"
    if not isinstance(payload, list):
        return "fail", "payload backup list inattendu (liste attendue)"
    return "pass", f"module backup opérationnel ({len(payload)} backup(s) listé(s), rien n'a été exécuté)"


async def _check_versions(client: httpx.AsyncClient) -> tuple[str, str]:
    started = time.monotonic()
    status, payload = await _request(client, "GET", "/api/health/detailed")
    if status != 200 or not isinstance(payload, dict):
        return "fail", "health détaillé indisponible — cohérence non vérifiable"
    version = payload.get("version")
    connected = payload.get("hub_connected") is True
    if not connected:
        return "fail", "hub_connected=false — l'app n'est pas enregistrée auprès du hub"
    if not version:
        return "warn", "version de l'app non exposée par /api/health (évolution template : socle_version)"
    return "pass", f"app v{version} enregistrée et connectée au hub"


async def run_socle_check(slug: str, api_url: str, *, user_token: str,
                          service_token: str, hub_version: str,
                          transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Exécute les 7 contrôles séquentiellement et retourne le rapport complet."""
    started_all = time.monotonic()
    controls = []
    async with httpx.AsyncClient(base_url=api_url, timeout=CONTROL_TIMEOUT,
                                 transport=transport) as client:
        checks = [
            ("health", "Health détaillé & connexion hub", _check_health(client)),
            ("user_auth", "Authentification user (JWT hub / JWKS)",
             _check_user_auth(client, user_token)),
            ("m2m", "Canal machine-à-machine (service account)",
             _check_m2m(client, service_token)),
            ("preferences", "Préférences hub↔app (round-trip)",
             _check_preferences(client, user_token)),
            ("discovery", "Discovery hub_data", _check_discovery(client, user_token)),
            ("backup", "Module backup (liste, sans exécution)",
             _check_backup(client, user_token)),
            ("versions", "Versions & enregistrement", _check_versions(client)),
        ]
        for control_id, label, coro in checks:
            started = time.monotonic()
            try:
                status, detail = await coro
            except httpx.HTTPError as e:
                status, detail = "fail", f"erreur réseau : {e}"
            except Exception as e:  # défensif : un contrôle ne doit jamais casser le rapport
                logger.warning("socle-check %s/%s : %s", slug, control_id, e)
                status, detail = "fail", f"erreur inattendue : {e}"
            controls.append({
                "id": control_id,
                "label": label,
                "status": status,  # pass | fail | warn
                "detail": detail,
                "duration_ms": int((time.monotonic() - started) * 1000),
            })

    passed = sum(1 for c in controls if c["status"] == "pass")
    total = len(controls)
    return {
        "slug": slug,
        "hub_version": hub_version,
        "app_version": _last_app_version(controls),
        "score": f"{passed}/{total}",
        "passed": passed,
        "total": total,
        "total_ms": int((time.monotonic() - started_all) * 1000),
        "date": datetime.now(timezone.utc).isoformat(),
        "controls": controls,
    }


def _last_app_version(controls: list) -> str | None:
    """Version annoncée par l'app, extraite du détail du contrôle health."""
    for c in controls:
        if c["id"] == "health" and c["status"] == "pass" and "version " in c["detail"]:
            return c["detail"].rsplit("version ", 1)[1]
    return None
