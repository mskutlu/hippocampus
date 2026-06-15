from __future__ import annotations

import json


def _json(output: str) -> dict:
    idx = output.find("{")
    assert idx >= 0, output
    return json.loads(output[idx:])


def test_wiki_query_file_answer_materialize(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    runner = CliRunner()
    runner.invoke(cli, ["wiki", "init", "--project", "demo", "--materialize"])
    result = runner.invoke(
        cli,
        ["wiki", "file-answer", "Answer", "--project", "demo", "--content", "Filed answer body.", "--materialize"],
    )
    assert result.exit_code == 0, result.output
    out = _json(result.output)
    assert out["page"]["type"] == "analysis"
    assert (hippo_env["vault"] / "Wiki" / "demo" / "wiki" / "analyses" / "Answer.md").exists()

    query = runner.invoke(cli, ["wiki", "query", "Filed answer", "--project", "demo"])
    assert query.exit_code == 0, query.output
    q = _json(query.output)
    assert any(p["title"] == "Answer" for p in q["pages"])

