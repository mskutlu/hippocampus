import subprocess

import pytest

from hippocampus.clients import hooks

# template -> expected stdout when disabled
CASES = {
    "session-start": "{}",
    "user-prompt-submit": "{}",
    "post-compaction": "{}",
    "cursor-session-start": "{}",
    "cursor-before-submit": '{"continue": true}',
}


@pytest.mark.parametrize("var", ["HIPPOCAMPUS_DISABLE", "TE_SIDECALL"])
@pytest.mark.parametrize("name", CASES)
def test_kill_switch_runs_no_hippo(tmp_path, name, var):
    marker = tmp_path / "called"
    fake = tmp_path / "hippo"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake.chmod(0o755)
    script = tmp_path / "h.sh"
    script.write_text(hooks._repo_template(name).read_text().replace("__HIPPO_BIN__", str(fake)))
    r = subprocess.run(["bash", str(script)], input='{"prompt": "hi", "session_id": "s"}', capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), var: "1"})
    assert r.returncode == 0
    assert r.stdout.strip() == CASES[name]
    assert not marker.exists()
