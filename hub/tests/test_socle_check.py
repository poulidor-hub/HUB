"""Tests du checksum socle (run_socle_check) — HTTP mocké, aucun réseau.

Référence : rapport d'intégration hub↔app, 7 contrôles sans effet de bord.
Le transport httpx est injecté (MockTransport), comme test_philia_rights_proxy.
"""
import httpx
import pytest

import app.socle_check as sc


def _handler(routes):
    """routes: list de (méthode, chemin_suffisant, handler(request)->(status, json))."""
    def handle(request: httpx.Request) -> httpx.Response:
        for method, path, fn in routes:
            if request.method == method and request.url.path.endswith(path):
                status, payload = fn(request)
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"detail": "not mocked"})
    return httpx.MockTransport(handle)


def _ok_routes(**overrides):
    """Routes renvoyant le nominal du template pour chaque contrôle."""
    def health(req):
        return 200, {"status": "healthy", "version": overrides.get(
            "app_version", "1.2.3"), "hub_connected": True}
    def prefs_get(req):
        return 200, {"theme": "light"}
    def prefs_put(req):
        return 200, {"theme": "light"}
    def m2m_catalog(req):
        return 200, []
    def tables(req):
        return 200, {"tables": ["items"]}
    def backup_list(req):
        return 200, []
    routes = [
        ("GET", "/api/health/detailed", health),
        ("GET", "/api/preferences", prefs_get),
        ("PUT", "/api/preferences", prefs_put),
        ("GET", "/api/m2m/catalog", m2m_catalog),
        ("GET", "/api/hub/data/tables", tables),
        ("GET", "/api/admin/backup/list", backup_list),
    ]
    return routes


def _run(routes, **kw):
    import asyncio
    transport = (routes if isinstance(routes, httpx.AsyncBaseTransport)
                 else _handler(routes))
    return asyncio.run(sc.run_socle_check(
        "mon-app", "http://app:9000",
        user_token="user-jwt", service_token="svc-jwt", hub_version="3.52.2",
        transport=transport, **kw))


def _by_id(report):
    return {c["id"]: c for c in report["controls"]}


def test_rapport_tous_les_controles_pass():
    report = _run(_ok_routes())
    assert report["score"] == "7/7"
    assert report["passed"] == 7 and report["total"] == 7
    assert report["app_version"] == "1.2.3"
    assert report["hub_version"] == "3.52.2"
    assert report["slug"] == "mon-app"
    assert [c["id"] for c in report["controls"]] == [
        "health", "user_auth", "m2m", "preferences", "discovery", "backup", "versions"]
    for c in report["controls"]:
        assert c["status"] == "pass", c
        assert c["duration_ms"] >= 0
        assert c["label"]
    assert isinstance(report["total_ms"], int) and report["total_ms"] >= 0
    assert report["date"]  # ISO


def test_echecs_isoles_par_controle():
    # user_auth (GET preferences) reste vert ; seul le PUT (écriture) échoue
    # → preferences isolée. m2m 403 → m2m isolée.
    def prefs_put(req):
        return 500, {"detail": "db locked"}
    def m2m(req):
        return 403, {"detail": "forbidden"}
    routes = _ok_routes()
    routes = [r for r in routes if r[1] != "/api/m2m/catalog" and r[0] != "PUT"]
    routes += [("PUT", "/api/preferences", prefs_put),
               ("GET", "/api/m2m/catalog", m2m)]
    report = _run(routes)
    by = _by_id(report)
    assert report["score"] == "5/7"
    assert by["user_auth"]["status"] == "pass"
    assert by["preferences"]["status"] == "fail"
    assert "500" in by["preferences"]["detail"]
    assert by["m2m"]["status"] == "fail"
    assert by["health"]["status"] == "pass"


def test_erreur_reseau_devient_fail_sans_exception():
    def boom(request):
        raise httpx.ConnectError("refused")
    # versions rejoue /api/health/detailed : un endpoint cassé fait échouer
    # health ET versions (comportement voulu) → 5/7
    routes = [("GET", "/api/health/detailed", boom)] + _ok_routes()
    report = _run(routes)
    by = _by_id(report)
    assert by["health"]["status"] == "fail"
    assert "refused" in by["health"]["detail"]
    assert by["versions"]["status"] == "fail"
    assert report["passed"] == 5 and report["score"] == "5/7"


def test_health_hub_deconnecte_est_fail():
    def health(req):
        return 200, {"status": "healthy", "version": None, "hub_connected": False}
    routes = _ok_routes()
    routes[0] = ("GET", "/api/health/detailed", health)
    report = _run(routes)
    by = _by_id(report)
    assert by["health"]["status"] == "fail"
    assert "hub_connected" in by["health"]["detail"]
    # versions échoue aussi : app non enregistrée = incohérence majeure
    assert by["versions"]["status"] == "fail"
    assert "hub_connected" in by["versions"]["detail"]


def test_versions_warn_si_version_absente_mais_hub_connecte():
    def health(req):
        return 200, {"status": "healthy", "version": None, "hub_connected": True}
    routes = _ok_routes()
    routes[0] = ("GET", "/api/health/detailed", health)
    report = _run(routes)
    by = _by_id(report)
    assert by["versions"]["status"] == "warn"
    assert by["health"]["status"] == "pass"


def test_preferences_roundtrip_ecrit_puis_relit():
    calls = []
    def prefs_put(req):
        calls.append("PUT")
        return 200, {}
    def prefs_get(req):
        calls.append("GET")
        return 200, {}
    routes = _ok_routes()
    routes = [r for r in routes if r[1] != "/api/preferences"]
    routes += [("GET", "/api/preferences", prefs_get), ("PUT", "/api/preferences", prefs_put)]
    report = _run(routes)
    by = _by_id(report)
    assert by["preferences"]["status"] == "pass"
    # round-trip : lecture, écriture, relecture
    assert calls.count("PUT") == 1 and calls.count("GET") >= 2
    assert calls.index("PUT") > calls.index("GET")  # GET initial avant PUT


def test_tokens_envoyes_dans_les_headers():
    seen = {}
    def prefs_get(req):
        seen["auth"] = req.headers.get("Authorization")
        return 200, {}
    def m2m(req):
        seen["m2m"] = req.headers.get("Authorization")
        return 200, []
    routes = _ok_routes()
    routes = [r for r in routes if r[1] not in ("/api/preferences", "/api/m2m/catalog")]
    routes += [("GET", "/api/preferences", prefs_get), ("GET", "/api/m2m/catalog", m2m)]
    _run(routes)
    assert seen["auth"] == "Bearer user-jwt"
    assert seen["m2m"] == "Bearer svc-jwt"


# ─── Format réel du socle (health/detailed 2.23.x) ────────────────────
# Le health/detailed réel expose hub.connected et app.version IMBRIQUÉS —
# pas de clés top-level hub_connected/version. Les mocks ci-dessus reflétaient
# une hypothèse incorrecte (régression détectée par la vérif réelle 3.53.0).

def _ok_routes_format_reel(**overrides):
    """Routes au format RÉEL : payload detailed imbriqué."""
    def health(req):
        return 200, {
            "status": "healthy",
            "app": {"slug": "mon-app", "name": "Mon App",
                    "version": overrides.get("app_version", "2.5.0")},
            "hub": {"connected": overrides.get("hub_connected", True),
                    "can_authenticate": True},
        }
    return _ok_routes(**{"_health": health}) if False else [
        ("GET", "/api/health/detailed", health),
    ] + _ok_routes()[1:]


def test_format_reel_health_detailed_pass():
    report = _run(_ok_routes_format_reel())
    assert report["score"] == "7/7", {c["id"]: c["detail"] for c in report["controls"]}
    assert report["app_version"] == "2.5.0"


def test_format_reel_hub_deconnecte_fail_health_et_versions():
    report = _run(_ok_routes_format_reel(hub_connected=False))
    by_id = _by_id(report)
    assert by_id["health"]["status"] == "fail"
    assert by_id["versions"]["status"] == "fail"
    assert by_id["user_auth"]["status"] == "pass"
