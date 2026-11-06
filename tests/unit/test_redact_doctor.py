import json

from click.testing import CliRunner


def _cli():
    from hippocampus.cli.main import cli
    return cli


def test_redacted_helper(hippo_env):
    from hippocampus import config
    out = config.redacted({
        "sync_token": "VNLfABCDEF", "api_key": "sk-123456", "client_secret": "abcdefg",
        "db_password": "hunter2", "embedding_model": "bge", "empty_token": "", "none_token": None,
        "num_token": 5,
    })
    assert out["sync_token"] == "VNLf…(redacted)"
    assert out["api_key"].endswith("(redacted)") and out["client_secret"].endswith("(redacted)")
    assert out["db_password"].endswith("(redacted)")
    assert out["embedding_model"] == "bge"
    assert out["empty_token"] == "" and out["none_token"] is None and out["num_token"] == 5


def test_config_show_and_set_hide_token(hippo_env):
    r = CliRunner()
    res = r.invoke(_cli(), ["config", "set", "sync_token", "VNLfSECRETVALUE"])
    assert res.exit_code == 0, res.output
    assert "SECRETVALUE" not in res.output and "(redacted)" in res.output and "VNLf" in res.output
    res = r.invoke(_cli(), ["config", "show"])
    assert "SECRETVALUE" not in res.output and "(redacted)" in res.output and "VNLf" in res.output
    # stored value untouched
    assert json.loads((hippo_env["home"] / "config.json").read_text())["sync_token"] == "VNLfSECRETVALUE"


def _doctor_line(plugin_entry):
    from hippocampus import config
    from pathlib import Path
    config.set_setting("claude_code_plugin", True)
    if plugin_entry:
        f = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"version": 2, "plugins": {"hippocampus@hippocampus": [{"scope": "user"}]}}))
    res = CliRunner().invoke(_cli(), ["doctor"])
    return next(l for l in res.output.splitlines() if "Claude Code" in l)


def test_doctor_plugin_mode_installed(hippo_env):
    assert "mcp:plugin" in _doctor_line(True)


def test_doctor_plugin_mode_missing(hippo_env):
    assert "mcp:x (plugin not installed)" in _doctor_line(False)


def test_set_setting_refuses_redacted_value(hippo_env):
    import pytest
    from hippocampus import config

    config.set_setting("sync_token", "real-secret-value")
    masked = config.redacted(config.all_settings())["sync_token"]
    with pytest.raises(ValueError):
        config.set_setting("sync_token", masked)
    assert config.get_setting("sync_token") == "real-secret-value"
