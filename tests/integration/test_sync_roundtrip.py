"""V11 Phase 3 — two devices converge through an in-process sync server."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "test-token"


class Device:
    """Swap config paths so one process can act as two devices."""

    def __init__(self, name, tmp_path, monkeypatch):
        from hippocampus import config
        from hippocampus.storage import db

        self.name = name
        self.home = tmp_path / name
        self.home.mkdir()
        self.db = self.home / "hippocampus.db"
        self.mp = monkeypatch
        self._cfg = config
        db.init_db(self.db)

    def __enter__(self):
        self.mp.setattr(self._cfg, "HIPPOCAMPUS_HOME", self.home)
        self.mp.setattr(self._cfg, "DB_PATH", self.db)
        self.mp.setattr(self._cfg, "_CONFIG_FILE", self.home / "config.json")
        self.mp.setattr(self._cfg, "SESSION_POINTER_DIR", self.home / "sessions")
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def server(tmp_path):
    from hippocampus.sync import server as sync_server

    app = sync_server.create_app(token=TOKEN, db_path=tmp_path / "server" / "sync.db")
    client = TestClient(app)

    def transport(method, path, body):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        r = client.request(method, path, json=body, headers=headers)
        if r.status_code >= 400:
            from hippocampus.sync.client import SyncError
            raise SyncError(f"HTTP {r.status_code}")
        return r.json()

    return client, transport


def test_server_requires_token(server):
    client, _ = server
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/pull?since=0&device=x").status_code == 401
    assert client.post("/v1/push", json={"device": "x", "ops": []}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_two_devices_converge(hippo_env, tmp_path, monkeypatch, server):
    _, transport = server
    from hippocampus.sync import client as sync
    from hippocampus.storage import fragments as F, associations
    from hippocampus import projects

    a = Device("a", tmp_path, monkeypatch)
    b = Device("b", tmp_path, monkeypatch)

    with a:
        projects.save({"acme": {"remotes": ["gitlab.com/x/*"], "paths": [], "aliases": []}})
        fa = F.create("kafka consumers are idempotent", summary="kafka", tags=["acme"], project="acme")
        fb = F.create("global rule", summary="rule")
        associations.strengthen(fa.id, fb.id)
        out = sync.sync(transport)
        assert out["ok"] and out["push"]["pushed"] >= 3

    with b:
        out = sync.sync(transport)
        assert out["pull"]["fragments"] == 2 and out["pull"]["associations"] == 1 and out["pull"]["config"] == 1
        got = F.get(fa.id)
        assert got.content == "kafka consumers are idempotent" and got.project == "acme" and got.tags == ["acme"]
        assert projects.load()["acme"]["remotes"] == ["gitlab.com/x/*"]
        # B pins and boosts, then deletes the global rule.
        F.update_fields(fa.id, pinned=True, accessed_delta=4)
        F.delete(fb.id)
        sync.sync(transport)

    with a:
        out = sync.sync(transport)
        assert out["pull"]["deleted"] == 1 and F.get(fb.id) is None
        got = F.get(fa.id)
        assert got.pinned is True and got.accessed == 4
        # Idempotent: syncing again changes nothing.
        again = sync.sync(transport)
        assert again["pull"]["fragments"] == 0 and again["pull"]["deleted"] == 0
        # A local edit newer than a remote tombstone survives.
        fc = F.create("keep me", summary="keep")
        sync.sync(transport)

    with b:
        sync.sync(transport)
        assert F.get(fc.id) is not None
        F.delete(fc.id)
        sync.sync(transport)

    with a:
        F.update_fields(fc.id, content="edited after delete")
        out = sync.sync(transport)
        assert out["pull"]["deleted"] == 0 and F.get(fc.id).content == "edited after delete"
        st = sync.status()
        assert st["last_ok_at"] and st["last_error"] is None


def test_offline_push_is_quiet_and_recorded(hippo_env):
    from hippocampus.sync import client as sync
    from hippocampus.sync.client import SyncError

    def down(method, path, body):
        raise SyncError("connection refused")

    out = sync.sync(down, quiet=True)
    assert out["ok"] is False
    assert "connection refused" in (sync.status()["last_error"] or "")
    with pytest.raises(SyncError):
        sync.sync(down)


def test_embedding_only_change_syncs_and_no_echo(hippo_env, tmp_path, monkeypatch, server):
    _, transport = server
    from hippocampus.sync import client as sync
    from hippocampus.storage import fragments as F, associations
    from hippocampus.embeddings import store

    model = "BAAI/bge-small-en-v1.5"
    monkeypatch.setenv("HIPPO_EMBEDDING_MODEL", model)
    a = Device("a", tmp_path, monkeypatch)
    b = Device("b", tmp_path, monkeypatch)

    with a:
        f1 = F.create("one", summary="one")
        f2 = F.create("two", summary="two")
        associations.strengthen(f1.id, f2.id)
        sync.sync(transport)
        # reindex after the push: only the embedding changes
        store.put(f1.id, [1.0, 0.0, 0.0], model=model)
        out = sync.sync(transport)
        assert out["push"]["pushed"] == 1

    with b:
        out = sync.sync(transport)
        assert store.get(f1.id) is not None and store.get(f1.id)[0] == [1.0, 0.0, 0.0]
        assert out["pull"]["associations"] == 1
        # nothing B received is echoed back
        again = sync.sync(transport)
        assert again["push"]["pushed"] == 0
        # a real local change on B still travels
        F.update_fields(f2.id, pinned=True)
        assert sync.sync(transport)["push"]["pushed"] == 1

    with a:
        out = sync.sync(transport)
        assert F.get(f2.id).pinned is True
        assert sync.sync(transport)["push"]["pushed"] == 0


def test_association_ops_are_batched(hippo_env, tmp_path, monkeypatch, server):
    client, transport = server
    from hippocampus.sync import client as sync
    from hippocampus.storage import fragments as F, associations

    a = Device("a", tmp_path, monkeypatch)
    with a:
        ids = [F.create(f"f{i}", summary=f"f{i}").id for i in range(12)]
        associations.strengthen_all(ids)  # 66 pairs
        out = sync.sync(transport)
        assert out["push"]["pushed"] == 12 + 1  # 12 fragments + one associations batch
    r = client.get("/v1/pull?since=0&device=other", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    kinds = [op["entity"] for op in r["ops"]]
    assert kinds.count("associations") == 1 and "association" not in kinds
    assert len(next(op for op in r["ops"] if op["entity"] == "associations")["payload"]["pairs"]) == 66
