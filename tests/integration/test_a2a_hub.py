"""A2A hub routes over FastAPI TestClient."""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": "Bearer tok"}
BODY = {"from": "codex@mac", "to": "claude-code@mac", "content": "hello", "msg_id": "m1"}


def _app(tmp_path, monkeypatch, enabled=True):
    from hippocampus.sync import server

    if enabled:
        monkeypatch.setenv("HIPPOCAMPUS_A2A_ENABLED", "1")
    else:
        monkeypatch.delenv("HIPPOCAMPUS_A2A_ENABLED", raising=False)
    monkeypatch.delenv("HIPPOCAMPUS_A2A_DB", raising=False)
    return TestClient(server.create_app(token="tok", db_path=tmp_path / "srv" / "sync.db"))


def test_flag_off_is_404(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch, enabled=False)
    assert c.post("/v1/a2a/send", json=BODY, headers=AUTH).status_code == 404
    assert c.get("/v1/a2a/agents", headers=AUTH).status_code == 404


def test_unauthenticated_is_401(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/a2a/send", json=BODY).status_code == 401
    assert c.get("/v1/a2a/inbox?address=claude-code@mac/s").status_code == 401


def test_send_inbox_ack_roundtrip(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    r = c.post("/v1/a2a/send", json={**BODY, "hop": 99}, headers=AUTH)
    assert r.status_code == 200 and r.json()["hop"] == 0  # client hop ignored
    addr = "claude-code@mac/cc-1"
    got = c.get("/v1/a2a/inbox", params={"address": addr}, headers=AUTH).json()
    assert [m["msg_id"] for m in got["messages"]] == ["m1"]
    assert c.post("/v1/a2a/ack", json={"address": addr, "msg_ids": ["m1"]}, headers=AUTH).json()["acked"] == 1
    assert c.get("/v1/a2a/inbox", params={"address": addr}, headers=AUTH).json()["messages"] == []
    assert [a["address"] for a in c.get("/v1/a2a/agents", headers=AUTH).json()["agents"]] == [addr]
    assert c.get("/v1/a2a/audit?limit=10", headers=AUTH).json()["audit"]


def test_pause_blocks_send(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    c.post("/v1/a2a/pause", headers=AUTH)
    assert c.post("/v1/a2a/send", json=BODY, headers=AUTH).status_code == 423
    c.post("/v1/a2a/resume", headers=AUTH)
    assert c.post("/v1/a2a/send", json=BODY, headers=AUTH).status_code == 200


def test_longpoll_returns_immediately_when_waiting(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    c.post("/v1/a2a/send", json=BODY, headers=AUTH)
    t = time.monotonic()
    r = c.get("/v1/a2a/inbox", params={"address": "claude-code@mac/x", "wait": 20}, headers=AUTH)
    assert len(r.json()["messages"]) == 1
    assert time.monotonic() - t < 2


def test_oversize_413(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    r = c.post("/v1/a2a/send", json={**BODY, "content": "x" * 20000}, headers=AUTH)
    assert r.status_code == 413


def test_sync_routes_unchanged_when_enabled(hippo_env, tmp_path, monkeypatch):
    c = _app(tmp_path, monkeypatch)
    assert c.get("/v1/pull", params={"device": "d"}, headers=AUTH).status_code == 200
