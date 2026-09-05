"""V11 follow-ups: numpy recall, Pi manifest, embedding sync, association batching, echo suppression, doctor audit."""

from __future__ import annotations

import base64
import json
import struct

import pytest


def _vec(*xs):
    return [float(x) for x in xs]


def test_numpy_topk_matches_python_scan(hippo_env, monkeypatch):
    from hippocampus.embeddings import search, store
    from hippocampus.storage import fragments as F

    ids = [F.create(f"c{i}", summary=f"s{i}").id for i in range(4)]
    vectors = [_vec(1, 0, 0), _vec(0.9, 0.1, 0), _vec(0, 1, 0), _vec(0, 0, 1)]
    for fid, v in zip(ids, vectors):
        store.put(fid, v, model="m")

    got = search._topk_numpy(_vec(1, 0, 0), "m", 2, None)
    assert [fid for fid, _ in got] == ids[:2]
    assert got[0][1] == pytest.approx(1.0)
    assert got[1][1] == pytest.approx(search.cosine(_vec(1, 0, 0), vectors[1]), abs=1e-6)

    # allowed_ids filters before ranking and never consumes the k slots
    got = search._topk_numpy(_vec(1, 0, 0), "m", 2, {ids[2], ids[3]})
    assert [fid for fid, _ in got] == [ids[2], ids[3]]

    # cache invalidates on a new put
    new = F.create("c9", summary="s9")
    store.put(new.id, _vec(1, 0, 0), model="m")
    got = search._topk_numpy(_vec(1, 0, 0), "m", 1, None)
    assert got[0][0] in {ids[0], new.id}
    assert len(search._matrix_for("m")[0]) == 5


def test_tools_manifest_covers_every_tool(hippo_env):
    from hippocampus.mcp import server

    manifest = server.tools_manifest()
    names = {m["name"] for m in manifest}
    assert names == {t.name for t in server.TOOL_SPECS}
    assert "mark" in names and "wiki_query" in names
    json.dumps(manifest)  # serialisable
    assert all(m["label"] and m["description"] and m["inputSchema"]["type"] == "object" for m in manifest)


def test_pi_install_writes_manifest(hippo_env, tmp_path, monkeypatch):
    from hippocampus.clients import mcp_config
    from hippocampus.clients.registry import CLIENTS

    import dataclasses

    spec = dataclasses.replace(
        next(c for c in CLIENTS if c.mcp_config_format == "pi-extension"),
        mcp_config_path=tmp_path / "ext",
    )
    changed, msg = mcp_config._install_pi_extension(spec)
    assert changed and "tools.json" in msg
    manifest = json.loads((tmp_path / "ext" / "tools.json").read_text())
    assert len(manifest) >= 26
    index = (tmp_path / "ext" / "index.ts").read_text()
    assert "tools.json" in index and "TOOL_SCHEMAS" not in index
    changed, _ = mcp_config._install_pi_extension(spec)
    assert changed is False


def test_doctor_reports_audit(hippo_env):
    from click.testing import CliRunner

    from hippocampus.cli.main import cli

    out = CliRunner().invoke(cli, ["doctor"]).output
    assert "audit:" in out and "fragments" in out
