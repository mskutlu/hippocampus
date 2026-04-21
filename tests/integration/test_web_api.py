"""Integration tests for the web UI backend API."""

from __future__ import annotations

import pytest


fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient


@pytest.fixture
def web_client(hippo_env, monkeypatch):
    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pytest")
    from hippocampus.web.server import create_app, CSRF_TOKEN

    app = create_app()
    client = TestClient(app)
    yield client, CSRF_TOKEN


def _hdr(token):
    return {"X-Hippo-Token": token}


def test_csrf_is_open(web_client):
    client, _ = web_client
    r = client.get("/api/csrf")
    assert r.status_code == 200
    assert r.json()["token"]


def test_get_stats_is_open(web_client):
    client, _ = web_client
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert "total_fragments" in r.json()


def test_mutation_requires_token(web_client):
    client, token = web_client
    # Missing header → 403
    r = client.post("/api/fragments", json={"content": "hello"})
    assert r.status_code == 403
    # With header → 200
    r = client.post("/api/fragments", json={"content": "hello"}, headers=_hdr(token))
    assert r.status_code == 200
    assert r.json()["stored"] is True


def test_crud_roundtrip(web_client):
    client, token = web_client
    r = client.post("/api/fragments", json={"content": "roundtrip", "tags": ["t"]}, headers=_hdr(token))
    fid = r.json()["fragment"]["id"]

    r = client.get(f"/api/fragments/{fid}")
    assert r.status_code == 200 and r.json()["fragment"]["id"] == fid

    r = client.post(f"/api/fragments/{fid}/pin", headers=_hdr(token))
    assert r.json()["fragment"]["pinned"] is True

    r = client.post(f"/api/fragments/{fid}/forget", json={"reason": "test"}, headers=_hdr(token))
    assert r.json()["found"] is True

    r = client.delete(f"/api/fragments/{fid}", headers=_hdr(token))
    assert r.json()["deleted"] is True
    assert client.get(f"/api/fragments/{fid}").status_code == 404


def test_recall_endpoint(web_client):
    client, token = web_client
    client.post("/api/fragments", json={"content": "kafka retries idempotent"}, headers=_hdr(token))
    r = client.post("/api/recall", json={"query": "kafka", "limit": 3}, headers=_hdr(token))
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_progress_endpoints(web_client):
    client, token = web_client
    r = client.post("/api/progress", json={"kind": "goal", "content": "ship web ui", "client": "devin"}, headers=_hdr(token))
    assert r.json()["logged"] is True

    r = client.get("/api/progress?client=devin")
    assert r.status_code == 200
    assert r.json()["count"] == 1

    r = client.post("/api/progress/undo", json={"client": "devin"}, headers=_hdr(token))
    assert r.json()["undone"] is True


def test_config_and_settings(web_client):
    client, token = web_client
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "working_block_mode" in r.json()["settings"]

    r = client.post("/api/config", json={"key": "working_block_mode", "value": "shared"}, headers=_hdr(token))
    assert r.json()["settings"]["working_block_mode"] == "shared"
