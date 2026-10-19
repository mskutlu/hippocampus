"""Evidence model sync: demotions propagate and old-format peers are compressed."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from tests.integration.test_sync_roundtrip import Device, server  # noqa: E402,F401


def _conf(F, fid):
    return F.get(fid).confidence


def test_old_peer_payload_is_compressed_and_demotion_syncs(hippo_env, tmp_path, monkeypatch, server):
    _, transport = server
    from hippocampus.dynamics import outcomes
    from hippocampus.storage import fragments as F
    from hippocampus.sync import client as sync

    a = Device("a", tmp_path, monkeypatch)
    b = Device("b", tmp_path, monkeypatch)
    with a:
        fid = F.create("shared fact", summary="s").id
        F.update_fields(fid, confidence=0.9)
        sync.sync(transport)
    with b:
        sync.sync(transport)
        assert _conf(F, fid) == pytest.approx(0.9)

    with a:
        monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
        outcomes.apply("sess_a", fid, "hurt", "strong", "explicit")
        sync.sync(transport)
        monkeypatch.delenv("HIPPO_CONFIDENCE_MODEL")
    with b:
        sync.sync(transport)
        assert _conf(F, fid) == pytest.approx(0.7)

    old_row = {
        "id": "frag_oldpeer", "content": "from an old peer", "summary": "", "source_type": "manual",
        "source_ref": None, "confidence": 0.9, "accessed": 3, "last_accessed_at": None,
        "created_at": "2026-01-01T00:00:00.000000Z", "updated_at": "2026-01-01T00:00:00.000000Z",
        "pinned": 0, "below_threshold_since": None, "project": None, "review_state": "approved",
        "superseded_by": None, "tags": [],
    }
    transport("POST", "/v1/push", {"device": "dev_old", "ops": [{
        "entity": "fragment", "entity_id": "frag_oldpeer", "op": "upsert",
        "updated_at": old_row["updated_at"], "payload": old_row,
    }]})
    with b:
        monkeypatch.setenv("HIPPO_CONFIDENCE_MODEL", "evidence")
        sync.sync(transport)
        assert _conf(F, "frag_oldpeer") == pytest.approx(0.7)
