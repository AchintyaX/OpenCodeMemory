from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ocm.hooks.handler import main
from ocm.storage.db import Database


def _init_db(project_root: Path) -> None:
    Database.init(project_root / ".openCodeMemory" / "memory.db").close()


def test_post_tool_use_threshold_emits_reminder_and_gates(tmp_path: Path) -> None:
    _init_db(tmp_path)
    runner = CliRunner()
    env = {"OCM_PROJECT_DIR": str(tmp_path)}

    session_payload = {"conversation_id": "sess-threshold", "workspace_roots": [str(tmp_path)]}
    result = runner.invoke(
        main,
        ["session-start", "--tool", "cursor"],
        input=json.dumps(session_payload),
        env=env,
    )
    assert result.exit_code == 0

    tool_payload = {
        "conversation_id": "sess-threshold",
        "workspace_roots": [str(tmp_path)],
        "tool_name": "Shell",
        "tool_input": {"command": "echo hi"},
    }
    reminder_output = ""
    for _ in range(5):
        result = runner.invoke(
            main,
            ["post-tool-use", "--tool", "cursor", "--threshold", "5"],
            input=json.dumps(tool_payload),
            env=env,
        )
        assert result.exit_code == 0
        reminder_output = result.output

    assert "semantic checkpoint required" in reminder_output.lower()

    gate_result = runner.invoke(
        main,
        ["pre-tool-use", "--threshold", "5"],
        input=json.dumps(tool_payload),
        env=env,
    )
    assert gate_result.exit_code == 2
    assert "permission" in gate_result.output


def test_semantic_checkpoint_resets_gate(tmp_path: Path) -> None:
    _init_db(tmp_path)
    runner = CliRunner()
    env = {"OCM_PROJECT_DIR": str(tmp_path)}

    runner.invoke(
        main,
        ["session-start", "--tool", "cursor"],
        input=json.dumps({"conversation_id": "sess-reset", "workspace_roots": [str(tmp_path)]}),
        env=env,
    )

    tool_payload = {
        "conversation_id": "sess-reset",
        "workspace_roots": [str(tmp_path)],
        "tool_name": "Shell",
        "tool_input": {"command": "echo hi"},
    }
    for _ in range(5):
        runner.invoke(
            main,
            ["post-tool-use", "--tool", "cursor", "--threshold", "5"],
            input=json.dumps(tool_payload),
            env=env,
        )

    # Semantic checkpoint tool use resets stale state.
    semantic_payload = {
        "conversation_id": "sess-reset",
        "workspace_roots": [str(tmp_path)],
        "tool_name": "mcp__opencodememory__ocm__checkpoint",
        "tool_input": {"work_summary": ["Summarized progress"]},
    }
    result = runner.invoke(
        main,
        ["post-tool-use", "--tool", "cursor", "--threshold", "5"],
        input=json.dumps(semantic_payload),
        env=env,
    )
    assert result.exit_code == 0

    gate_result = runner.invoke(
        main,
        ["pre-tool-use", "--threshold", "5"],
        input=json.dumps(tool_payload),
        env=env,
    )
    assert gate_result.exit_code == 0
    assert gate_result.output.strip() == ""
