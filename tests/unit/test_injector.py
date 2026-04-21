"""Unit tests for injector.upsert_block (create / replace / append / skip / backup)."""

from __future__ import annotations


def _block(text: str = "hello") -> str:
    from hippocampus import config
    return f"{config.INJECTION_MARKER_START}\n\n{text}\n\n{config.INJECTION_MARKER_END}\n"


def test_upsert_creates_missing_file(tmp_path):
    from hippocampus.clients.injector import upsert_block

    target = tmp_path / "sub" / "rules.md"
    changed, reason = upsert_block(target, _block())
    assert changed is True
    assert reason == "created"
    assert target.exists()
    assert "hello" in target.read_text()


def test_upsert_appends_when_no_marker(tmp_path):
    from hippocampus.clients.injector import upsert_block

    target = tmp_path / "rules.md"
    target.write_text("# Existing\n\nBody content.\n", encoding="utf-8")
    changed, reason = upsert_block(target, _block("FIRST"))
    assert changed is True
    assert reason == "appended"
    content = target.read_text()
    assert "# Existing" in content
    assert "FIRST" in content
    # Backup created
    assert target.with_suffix(target.suffix + ".pre-hippocampus.bak").exists()


def test_upsert_replaces_existing_block(tmp_path):
    from hippocampus.clients.injector import upsert_block

    target = tmp_path / "rules.md"
    target.write_text(f"# Header\n\n{_block('OLD')}\n# Footer\n", encoding="utf-8")
    changed, reason = upsert_block(target, _block("NEW"))
    assert changed is True
    assert reason == "replaced"
    content = target.read_text()
    assert "OLD" not in content
    assert "NEW" in content
    assert "# Header" in content
    assert "# Footer" in content


def test_upsert_skips_when_unchanged(tmp_path):
    from hippocampus.clients.injector import upsert_block

    target = tmp_path / "rules.md"
    block = _block("SAME")
    target.write_text(f"# H\n\n{block}", encoding="utf-8")

    changed, reason = upsert_block(target, block)
    assert changed is False
    assert reason == "unchanged"


def test_backup_is_one_shot(tmp_path):
    from hippocampus.clients.injector import upsert_block

    target = tmp_path / "rules.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    upsert_block(target, _block("A"))
    upsert_block(target, _block("B"))
    # Backup content must stay == the original file (only written once)
    bak = target.with_suffix(target.suffix + ".pre-hippocampus.bak")
    assert bak.read_text() == "ORIGINAL\n"
