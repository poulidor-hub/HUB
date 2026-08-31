"""Tests d'exposition des ports et de l'instance_id par l'API."""
from unittest.mock import patch


async def test_hub_host_exposes_ports_and_instance(client):
    resp = await client.get("/api/config/hub-host")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert isinstance(data["api_port"], int)
    assert isinstance(data["frontend_port"], int)
    assert isinstance(data["instance_id"], str) and len(data["instance_id"]) == 32


async def test_health_exposes_instance_id(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert isinstance(data["instance_id"], str) and data["instance_id"]
