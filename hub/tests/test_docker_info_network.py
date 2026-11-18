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
