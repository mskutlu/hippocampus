from __future__ import annotations

import json


def _json(output: str) -> dict:
    idx = output.find("{")
    assert idx >= 0, output
    return json.loads(output[idx:])


def test_wiki_ingest_query_and_file_answer(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    src = hippo_env["home"] / "source.md"
    src.write_text("# Memory\n\nDurable knowledge should be compiled once.\n", encoding="utf-8")

    runner = CliRunner()
    assert runner.invoke(cli, ["wiki", "init", "--project", "demo"]).exit_code == 0

    dry = runner.invoke(cli, ["wiki", "ingest", str(src), "--project", "demo", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert _json(dry.output)["dry_run"] is True

    ingest = runner.invoke(cli, ["wiki", "ingest", str(src), "--project", "demo", "--materialize"])
    assert ingest.exit_code == 0, ingest.output
    out = _json(ingest.output)
    assert out["created"] is True
    assert out["source_record"]["title"] == "Source"
    source_id = out["source_record"]["id"]

    query = runner.invoke(cli, ["wiki", "query", "Durable knowledge", "--project", "demo"])
    assert query.exit_code == 0, query.output
    q = _json(query.output)
    assert q["count"] >= 1
    assert any(p["type"] == "source" for p in q["pages"])

    answer = runner.invoke(
        cli,
        [
            "wiki",
            "file-answer",
            "Durable Knowledge",
            "--project",
            "demo",
            "--content",
            "Use compiled wiki pages.",
            "--source-id",
            source_id,
        ],
    )
    assert answer.exit_code == 0, answer.output
    a = _json(answer.output)
    assert a["page"]["type"] == "analysis"
    assert a["page"]["sources"] == [source_id]

    filed_query = runner.invoke(
        cli,
        ["wiki", "query", "compiled wiki pages", "--project", "demo"],
    )
    filed = next(page for page in _json(filed_query.output)["pages"] if page["title"] == "Durable Knowledge")
    assert filed["source_records"][0]["id"] == source_id
    assert filed["source_records"][0]["source_ref"] == str(src.resolve())


def test_wiki_duplicate_ingest_is_noop(hippo_env):
    from click.testing import CliRunner
    from hippocampus.cli.main import cli

    src = hippo_env["home"] / "source.md"
    src.write_text("same content", encoding="utf-8")

    runner = CliRunner()
    runner.invoke(cli, ["wiki", "init", "--project", "demo"])
    first = runner.invoke(cli, ["wiki", "ingest", str(src), "--project", "demo"])
    assert _json(first.output)["created"] is True
    second = runner.invoke(cli, ["wiki", "ingest", str(src), "--project", "demo"])
    assert _json(second.output)["duplicate"] is True
