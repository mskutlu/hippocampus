"""V9 W9 — hippo observe ingests JSONL → fragments."""

from __future__ import annotations

import json


def _parse_cli_json(output: str) -> dict:
    """The fastembed loader writes progress bars to stdout in test runs.
    Strip everything before the first '{' so json.loads sees clean payload.
    """
    idx = output.find("{")
    assert idx >= 0, f"no JSON in output: {output!r}"
    return json.loads(output[idx:])


def test_observe_ingests_new_lines(hippo_env, monkeypatch):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli
    from hippocampus.storage import fragments as F

    monkeypatch.setenv("HIPPOCAMPUS_CLIENT", "pytest")
    monkeypatch.setenv("HIPPO_OBSERVE_DEFAULT_CONFIDENCE", "0.30")

    obs_file = hippo_env["home"] / "observations.jsonl"
    obs_file.write_text(
        json.dumps({"content": "git commit on acme-orders: fix idempotent consumer", "tags": ["git", "acme-orders"]}) + "\n"
        + json.dumps({"content": "edit on admin-ui: dark-mode polish", "summary": "ui polish", "source_ref": "commit:abc123"}) + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["observe", "--source", str(obs_file)])
    assert result.exit_code == 0, result.output
    out = _parse_cli_json(result.output)
    assert out["count"] == 2
    assert out["new_offset"] > 0

    # Each fragment exists, has auto-observed tag + lower confidence
    for c in out["created"]:
        frag = F.get(c["id"])
        assert frag is not None
        assert "auto-observed" in frag.tags
        assert frag.source_type == "auto-observed"
        assert frag.confidence == 0.30


def test_observe_resumes_from_offset(hippo_env, monkeypatch):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    obs_file = hippo_env["home"] / "observations.jsonl"
    obs_file.write_text(
        json.dumps({"content": "line one"}) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    runner.invoke(cli, ["observe", "--source", str(obs_file)])

    # Append a 2nd line; re-running must only create one new fragment.
    with obs_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"content": "line two"}) + "\n")

    result = runner.invoke(cli, ["observe", "--source", str(obs_file)])
    out = _parse_cli_json(result.output)
    assert out["count"] == 1
    assert out["created"][0]["summary"] == "line two"


def test_observe_dry_run_creates_nothing(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli
    from hippocampus.storage import fragments as F

    obs_file = hippo_env["home"] / "observations.jsonl"
    obs_file.write_text(
        json.dumps({"content": "dry run preview", "tags": ["preview"]}) + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["observe", "--source", str(obs_file), "--dry-run"])
    out = _parse_cli_json(result.output)
    assert out["count"] == 1
    assert out["dry_run"] is True
    # No fragment was persisted
    assert all(f.source_type != "auto-observed" for f in F.list_all(limit=10))
