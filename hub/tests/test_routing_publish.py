"""Chantier B Task 3 — publication (swap atomique + reloads + rollback).

publish() est async, ouvre SA propre session (async_session_maker patché vers la
SQLite de test par le conftest) et ne touche jamais la session de l'appelant :
les endpoints font leur commit MÉTIER puis appellent regenerate().
Le client Docker est injectable (_get_docker_client) pour les tests.
"""

import pytest

import app.routing_service as rs
from app.config import get_settings
from app.models import Application, AppSetting, RoutedService


@pytest.fixture
def dirs(tmp_path, settings):
    """Redirige les dossiers de publication + certs vers tmp_path (CA fraîche,
    clé RSA 2048 pour la vitesse des tests). Restaurés par la fixture settings."""
    import pki

    s = get_settings()
    s.routing_dns_dir = str(tmp_path / "dns")
    s.routing_gateway_dir = str(tmp_path / "gateway")
    s.certs_dir = str(tmp_path / "certs")
    s.pki_key_size = 2048
    pki.init_ca(force=True)
    return s


@pytest.fixture
def fake_docker(monkeypatch):
    """Remplace le client Docker : enregistre les reloads, résultat pilotable."""
    calls: list[tuple[str, str]] = []
    result = {"exit_code": 0, "output": b""}

    class FakeContainers:
        def get(self, name):
            container = type("C", (), {})()

            def exec_run(cmd, **kw):
                calls.append((name, cmd))
                return result["exit_code"], result["output"]

            container.exec_run = exec_run
            return container

    class FakeClient:
        containers = FakeContainers()

    monkeypatch.setattr(rs, "_get_docker_client", lambda: FakeClient())
    return calls, result


async def _seed(session, zone: str = "hub.example.lan"):
    session.add(AppSetting(key="hub_dns_zone", value=zone))
    session.add(Application(name="Inv", slug="exemple-app", api_url="http://192.0.2.10:9001",
                            frontend_url="http://192.0.2.10:4201", dns_name="exemple-app"))
    await session.commit()


@pytest.mark.asyncio
async def test_publish_writes_files_and_reloads(async_session, dirs, fake_docker):
    calls, _ = fake_docker
    await _seed(async_session)

    await rs.publish()

    import pathlib
    zone = pathlib.Path(dirs.routing_dns_dir, "hub.zone").read_text()
    assert "address=/exemple-app.hub.example.lan/192.0.2.10" in zone
    vhost = pathlib.Path(dirs.routing_gateway_dir, "exemple-app.conf").read_text()
    assert "server_name exemple-app.hub.example.lan;" in vhost
    # reloads : dnsmasq HUP + nginx reload
    assert ("hub-dns", "kill -HUP 1") in calls
    assert ("hub-frontend", "nginx -s reload") in calls
    # pas d'erreur persistée
    err = await async_session.get(AppSetting, "routing_last_error")
    assert err is None or (err.value or "") == ""


@pytest.mark.asyncio
async def test_publish_rolls_back_on_failed_reload(async_session, dirs, fake_docker):
    calls, result = fake_docker
    result["exit_code"] = 1  # le reload nginx échoue
    await _seed(async_session)

    # un contenu précédent existe : il doit être restauré
    import pathlib
    gw = pathlib.Path(dirs.routing_gateway_dir)
    gw.mkdir(parents=True)
    (gw / "previous.conf").write_text("# ancien vhost")

    with pytest.raises(rs.RoutingPublishError):
        await rs.publish()

    # rollback : le contenu précédent est de retour, pas de fichier du nouveau vhost
    assert (gw / "previous.conf").read_text() == "# ancien vhost"
    assert not (gw / "exemple-app.conf").exists()
    # last_error persisté (lisible depuis une session fraîche)
    err = await async_session.get(AppSetting, "routing_last_error")
    assert err is not None and "nginx" in (err.value or "").lower() or err.value


@pytest.mark.asyncio
async def test_publish_clears_error_on_success_after_failure(async_session, dirs, fake_docker):
    calls, result = fake_docker
    await _seed(async_session)
    result["exit_code"] = 1
    with pytest.raises(rs.RoutingPublishError):
        await rs.publish()
    result["exit_code"] = 0
    await rs.publish()
    err = await async_session.get(AppSetting, "routing_last_error")
    assert err is not None and (err.value or "") == ""


@pytest.mark.asyncio
async def test_publish_with_empty_zone_wipes_files(async_session, dirs, fake_docker):
    calls, _ = fake_docker
    import pathlib
    await _seed(async_session, zone="hub.example.lan")
    await rs.publish()
    # puis zone vidée → dossiers sans fichier généré
    row = await async_session.get(AppSetting, "hub_dns_zone")
    row.value = ""
    await async_session.commit()
    await rs.publish()
    assert list(pathlib.Path(dirs.routing_dns_dir).glob("*")) == []
    assert list(pathlib.Path(dirs.routing_gateway_dir).glob("*")) == []
