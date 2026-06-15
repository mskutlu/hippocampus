"""Tests for the V1.5 hook context renderer."""

from __future__ import annotations

import json
import os


def test_render_context_with_working_session(hippo_env):
    """Live ledger entries appear in the rendered payload."""
    from hippocampus.clients import hook_context
    from hippocampus.mcp import tools as T

    os.environ["HIPPOCAMPUS_CLIENT"] = "pytest"
    T.log_progress(kind="goal", content="ship the compaction fix")
    T.log_progress(kind="ask", content="how do hooks behave after compact?")
    T.log_progress(kind="done", content="wrote prd-compaction-fix.md")

    payload = hook_context.render_context(client="pytest", event_name="UserPromptSubmit")

    assert "[Hippocampus] live memory snapshot" in payload
    assert "ship the compaction fix" in payload
    assert "wrote prd-compaction-fix.md" in payload
    assert "how do hooks behave after compact?" in payload


def test_render_context_no_session_is_safe(hippo_env):
    """No active session and no fragments — payload still renders without errors."""
    from hippocampus.clients import hook_context

    payload = hook_context.render_context(client="pytest", event_name="SessionStart")
    assert payload  # not empty
    assert "no working session" in payload or "current working session" in payload.lower()


def test_render_context_includes_top_fragments(hippo_env):
    """When fragments exist and no query is given, top-N fragments appear."""
    from hippocampus.clients import hook_context
    from hippocampus.mcp import tools as T

    T.remember(content="Kafka retries need idempotent consumers.", summary="kafka idempotency", tags=["kafka"])
    T.remember(content="Postgres FK cascade can lock for hours under load.", summary="fk lock", tags=["pg"])

    payload = hook_context.render_context(client="pytest", include_working=False)
    assert "Top long-term memories" in payload
    assert "kafka idempotency" in payload or "fk lock" in payload


def test_render_context_query_uses_recall(hippo_env):
    """When a query is supplied we route through recall + show 'matching' header."""
    from hippocampus.clients import hook_context
    from hippocampus.mcp import tools as T

    T.remember(content="Kafka retries need idempotent consumers.", summary="kafka idempotency", tags=["kafka"])

    payload = hook_context.render_context(
        client="pytest",
        query="kafka",
        include_working=False,
    )
    assert "matching the latest prompt" in payload
    assert "kafka idempotency" in payload


def test_render_context_respects_budget(hippo_env):
    """char_budget truncates output."""
    from hippocampus.clients import hook_context
    from hippocampus.mcp import tools as T

    for i in range(20):
        T.remember(
            content=f"fragment number {i} content " + "lorem ipsum " * 30,
            summary=f"frag {i}",
            tags=[f"t{i}"],
        )

    big = hook_context.render_context(client="pytest", char_budget=20000, include_working=False)
    small = hook_context.render_context(client="pytest", char_budget=400, include_working=False)
    assert len(small) <= 500  # tiny budget plus a few lines of header overhead
    assert len(big) > len(small)


def test_cli_context_command_emits_text(hippo_env):
    """The `hippo context` CLI prints text (not JSON) to stdout."""
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools as T

    T.log_progress(kind="goal", content="cli-context-test goal")

    runner = CliRunner()
    result = runner.invoke(cli, ["context", "--client", "pytest", "--event", "SessionStart"])

    assert result.exit_code == 0, result.output
    assert "cli-context-test goal" in result.output
    # Should NOT be JSON — it's raw markdown
    try:
        json.loads(result.output)
        is_json = True
    except Exception:
        is_json = False
    assert not is_json, "context command should emit plain markdown, not JSON"


def test_cli_context_query_passes_through(hippo_env):
    """`--query` reaches the recall path."""
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.mcp import tools as T

    T.remember(content="Kafka retries need idempotent consumers.", summary="kafka idempotency", tags=["kafka"])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["context", "--client", "pytest", "--query", "kafka", "--no-working"],
    )
    assert result.exit_code == 0, result.output
    assert "kafka idempotency" in result.output


def test_render_context_includes_wiki_status_when_enabled(hippo_env, monkeypatch):
    from hippocampus.clients import hook_context

    monkeypatch.setenv("HIPPO_WIKI_ENABLED", "true")
    payload = hook_context.render_context(client="pytest", include_working=False, include_fragments=False)

    assert "LLM Wiki" in payload
    assert "not initialized" in payload
    assert "wiki_init" in payload
