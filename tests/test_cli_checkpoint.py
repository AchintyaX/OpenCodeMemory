from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ocm.install.cli import main
from ocm.storage.db import Database


def _init_db(project_root: Path) -> Database:
    db_path = project_root / ".openCodeMemory" / "memory.db"
    return Database.init(db_path)


def test_help_lists_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["help"])
    assert result.exit_code == 0
    assert "checkpoint" in result.output
    assert "help" in result.output
    assert "list" in result.output


def test_checkpoint_command_writes_session(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    db.close()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "checkpoint",
            "--session-id",
            "cli-001",
            "--tool",
            "cursor",
            "--goal",
            "Capture CLI checkpoint",
            "--completed",
            "Added CLI command",
        ],
        env={"OCM_PROJECT_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["session_id"] == "cli-001"

    db = Database.for_project(cwd=tmp_path)
    row = db.execute("SELECT goal, tool FROM sessions WHERE id = ?", ["cli-001"]).fetchone()
    assert row["goal"] == "Capture CLI checkpoint"
    assert row["tool"] == "cursor"
    db.close()


def test_checkpoint_from_stdin_uses_conversation_id(tmp_path: Path) -> None:
    db = _init_db(tmp_path)
    db.close()

    payload = {
        "conversation_id": "conv-123",
        "tool": "cursor",
        "checkpoint": {
            "work_summary": ["Mapped hook payloads"],
            "work_pending": ["Add tests"],
        },
    }
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["checkpoint", "--from-stdin"],
        input=json.dumps(payload),
        env={"OCM_PROJECT_DIR": str(tmp_path)},
    )
    assert result.exit_code == 0
    out = json.loads(result.output)
    assert out["session_id"] == "conv-123"

    db = Database.for_project(cwd=tmp_path)
    row = db.execute("SELECT id, tool FROM sessions WHERE id = ?", ["conv-123"]).fetchone()
    assert row["id"] == "conv-123"
    assert row["tool"] == "cursor"
    db.close()
