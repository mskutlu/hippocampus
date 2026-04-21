"""Integration test: DB ↔ Obsidian mirror stays consistent across mutations."""

from __future__ import annotations


def test_create_writes_mirror(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("body", summary="one-liner", tags=["t1"])
    mirror = hippo_env["fragments_dir"] / f"{f.id}.md"
    assert mirror.exists()
    text = mirror.read_text()
    assert f.id in text
    assert "one-liner" in text
    assert "t1" in text


def test_update_refreshes_mirror(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("body", summary="before")
    mirror = hippo_env["fragments_dir"] / f"{f.id}.md"
    F.update_fields(f.id, summary="after")
    assert "after" in mirror.read_text()
    assert "before" not in mirror.read_text()


def test_archive_moves_mirror(hippo_env):
    from hippocampus.storage import fragments as F

    f = F.create("body", summary="s")
    mirror = hippo_env["fragments_dir"] / f"{f.id}.md"
    archive_path = hippo_env["fragments_dir"] / ".archive" / f"{f.id}.md"
    assert mirror.exists()
    F.archive(f.id)
    assert not mirror.exists()
    assert archive_path.exists()


def test_mcp_tools_round_trip(hippo_env):
    from hippocampus.mcp import tools as T

    stored = T.remember(content="Kafka retries.", summary="kafka", tags=["k"])
    fid = stored["fragment"]["id"]
    recalled = T.recall(query="kafka")
    assert recalled["count"] == 1
    assert recalled["fragments"][0]["id"] == fid
    assert recalled["fragments"][0]["confidence"] > 0.5  # boosted

    assert T.pin(fid)["fragment"]["pinned"] is True
    assert T.unpin(fid)["fragment"]["pinned"] is False

    # Forget → confidence drops
    conf_before = T.get_fragment(fid, boost_on_read=False)["fragment"]["confidence"]
    T.forget(fid, reason="stale")
    conf_after = T.get_fragment(fid, boost_on_read=False)["fragment"]["confidence"]
    assert conf_after < conf_before
