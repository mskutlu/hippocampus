from __future__ import annotations

import json


def _json(output: str) -> dict:
    idx = output.find("{")
    assert idx >= 0, output
    return json.loads(output[idx:])


def test_wiki_query_blocks_before_init(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    result = CliRunner().invoke(cli, ["wiki", "query", "memory", "--project", "demo"])
    assert result.exit_code == 0, result.output
    out = _json(result.output)
    assert out["blocked"] is True
    assert out["reason"] == "wiki_not_initialized"


def test_wiki_init_materializes_markdown(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    result = CliRunner().invoke(cli, ["wiki", "init", "--project", "demo", "--materialize"])
    assert result.exit_code == 0, result.output
    out = _json(result.output)
    assert out["ok"] is True
    assert out["project"]["project_key"] == "demo"
    assert (hippo_env["vault"] / "Wiki" / "demo" / "wiki" / "index.md").exists()
    assert (hippo_env["vault"] / "Wiki" / "demo" / "wiki" / "log.md").exists()


def test_wiki_lint_clean_after_init(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    runner = CliRunner()
    runner.invoke(cli, ["wiki", "init", "--project", "demo"])
    result = runner.invoke(cli, ["wiki", "lint", "--project", "demo"])
    assert result.exit_code == 0, result.output
    out = _json(result.output)
    assert out["ok"] is True
    assert out["issues"] == []

