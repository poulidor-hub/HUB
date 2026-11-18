"""docker-info — compteurs réseau cumulés du conteneur (mini-cockpit fiche app).

La fiche app calcule le débit ↓/↑ entre deux ticks de docker-info : l'endpoint
doit exposer les compteurs cumulés `net_rx_bytes` / `net_tx_bytes` (somme toutes
interfaces du conteneur), présents dans le stats Docker déjà consommé.
"""
import pytest

from app.routes.apps import _network_bytes_from_stats


def test_sums_all_interfaces():
    raw = {
        "networks": {
            "eth0": {"rx_bytes": 1000, "tx_bytes": 500},
            "eth1": {"rx_bytes": 250, "tx_bytes": 125},
        }
    }
    rx, tx = _network_bytes_from_stats(raw)
    assert rx == 1250
    assert tx == 625


def test_missing_networks_returns_zeros():
    assert _network_bytes_from_stats({}) == (0, 0)
    assert _network_bytes_from_stats({"networks": {}}) == (0, 0)


def test_partial_or_invalid_values_ignored():
    raw = {
        "networks": {
            "eth0": {"rx_bytes": 100},  # tx manquant → 0
            "eth1": {"rx_bytes": "pas-un-nombre", "tx_bytes": 42},  # invalide → ignoré
        }
    }
    rx, tx = _network_bytes_from_stats(raw)
    assert rx == 100
    assert tx == 42


def test_docker_info_route_is_attached_to_get_app_docker_info():
    """Non-régression (18/11/2026) : le helper inséré entre le décorateur et la
    fonction avait détaché la route GET /apps/{slug}/docker-info → 422 sur
    l'endpoint (mini-cockpit + section Docker morts en prod)."""
    from app.main import app

    spec = app.openapi()
    paths = [p for p in spec.get('paths', {}) if p.endswith('/docker-info')]
    assert paths, "la route GET /apps/{slug}/docker-info doit exister"
    operation_ids = [spec['paths'][p].get('get', {}).get('operationId', '') for p in paths]
    assert all('get_app_docker_info' in op_id for op_id in operation_ids), \
        f"la route docker-info doit pointer sur get_app_docker_info, obtenu : {operation_ids}"
    assert not any('_network_bytes_from_stats' in (op.get('operationId') or '')
                   for ops in spec.get('paths', {}).values() for op in ops.values()), \
        "le helper réseau ne doit jamais être monté comme route"
