"""Tests for the V1.5 hook context renderer."""

from __future__ import annotations

import json
import os

import pytest


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


def test_cli_progress_log_ask_skips_envelope_text(hippo_env):
    """`hippo progress log ask` on hook/task markup logs nothing, opens no session."""
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.storage import sessions

    runner = CliRunner()
    envelope = "<task-notification>background task finished</task-notification>"
    result = runner.invoke(
        cli,
        ["progress", "log", "ask", envelope, "--client", "pytest-envelope"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"logged": False, "reason": "envelope"}

    with pytest.raises(RuntimeError):
        sessions.current_session_id("pytest-envelope", open_if_missing=False)


def test_cli_progress_log_ask_normal_text_still_logs(hippo_env):
    """A real user ask still opens a session and writes a ledger entry."""
    from click.testing import CliRunner

    from hippocampus.cli.main import cli
    from hippocampus.storage import ledger as ledger_store, sessions

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["progress", "log", "ask", "what should I do next?", "--client", "pytest-envelope-2"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload.get("logged", True) is not False

    sid = sessions.current_session_id("pytest-envelope-2", open_if_missing=False)
    entries = ledger_store.current_entries(sid)
    assert any("what should I do next?" in e.content for e in entries)


def test_render_context_includes_wiki_status_when_enabled(hippo_env, monkeypatch):
    from hippocampus.clients import hook_context

    monkeypatch.setenv("HIPPO_WIKI_ENABLED", "true")
    payload = hook_context.render_context(client="pytest", include_working=False, include_fragments=False)

    assert "LLM Wiki" in payload
    assert "not initialized" in payload
    assert "wiki_init" in payload


def _setup_frags(n=3, client="claude-code"):
    from hippocampus.mcp import tools as T

    os.environ["HIPPOCAMPUS_CLIENT"] = client
    T.log_progress(kind="goal", content="injection dedupe")
    return [
        T.remember(content=f"zebra fact {i} " + "x" * 50, summary=f"zebra fact {i}", scope="global")["fragment"]["id"]
        for i in range(n)
    ]


def _render(event, client="claude-code", **kw):
    from hippocampus.clients import hook_context

    return hook_context.render_context(
        client=client, event_name=event, extra_query_streams=[], include_working=False, **kw
    )


def _sid():
    from hippocampus.storage import sessions

    return sessions.current_session_id("claude-code", open_if_missing=False)


def test_budget_dropped_fragments_not_recorded_as_injected(hippo_env):
    from hippocampus.storage import sessions

    _setup_frags()
    payload = _render("SessionStart", char_budget=1)  # header alone blows the budget
    assert "zebra fact" not in payload
    assert sessions.injected_fragment_ids(_sid()) == set()


def test_truncated_fragment_line_not_recorded(hippo_env):
    from hippocampus.storage import sessions

    ids = _setup_frags()
    full = _render("SessionStart", char_budget=20000)
    last = full.rstrip().splitlines()[-1]
    with sessions.get_conn() as conn:
        conn.execute("DELETE FROM session_accesses")
    # budget lands inside the last fragment line
    payload = _render("SessionStart", char_budget=len(full) - len(last) // 2 - 10)
    assert "…(truncated)" in payload
    assert last not in payload
    assert sessions.injected_fragment_ids(_sid()) == {i for i in ids if f"`{i}`" not in last}


def test_user_prompt_submit_omits_already_sent_fragments(hippo_env):
    ids = _setup_frags()
    first = _render("UserPromptSubmit")
    assert all(i in first for i in ids)
    second = _render("UserPromptSubmit")
    assert not any(i in second for i in ids)
    assert "## Top" not in second  # heading omitted with the fragments


def test_session_start_resends_after_earlier_emissions(hippo_env):
    ids = _setup_frags()
    _render("SessionStart")
    assert not any(i in _render("UserPromptSubmit") for i in ids)
    again = _render("SessionStart")  # compact / clear / resume
    assert all(i in again for i in ids)
    assert not any(i in _render("UserPromptSubmit") for i in ids)
    assert all(i in _render("PostCompaction") for i in ids)


def test_injected_then_recalled_still_counts_as_sent(hippo_env):
    from hippocampus.mcp import tools as T

    ids = _setup_frags()
    _render("SessionStart")
    T.recall(query="zebra fact 0", limit=1)  # flips via inject -> recall, bumps accessed_at
    assert not any(i in _render("UserPromptSubmit") for i in ids)


def test_get_stats_reports_injection_cost(hippo_env):
    from hippocampus.mcp import tools as T

    _setup_frags()
    a = _render("SessionStart")
    b = _render("UserPromptSubmit")
    inj = T.get_stats()["hook_injection"]
    assert inj["total_chars"] == len(a) + len(b)
    assert inj["total_emissions"] == 2
    assert inj["sessions"] == [
        {"session_id": _sid(), "chars": len(a) + len(b), "emissions": 2}
    ]


def test_user_prompt_submit_resends_for_client_not_known_to_refire(hippo_env):
    ids = _setup_frags(client="cursor")
    _render("UserPromptSubmit", client="cursor")
    assert all(i in _render("UserPromptSubmit", client="cursor") for i in ids)
