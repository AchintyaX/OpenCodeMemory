from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click

from ocm.hooks.file_tracker import append_file
from ocm.hooks.git import get_head_sha, get_project_name
from ocm.storage.markdown_renderer import make_markdown_filename


def _resolve_project_root(data: dict) -> Path:
    """Extract project root from hook JSON payload or environment variables."""
    cwd = (
        data.get("cwd")
        or (data.get("workspace_roots") or [None])[0]
        or os.environ.get("CURSOR_PROJECT_DIR")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )
    return Path(cwd).resolve()


def _resolve_session_id(data: dict) -> str:
    """Extract session ID from hook JSON payload or environment variables."""
    return (
        data.get("session_id")
        or data.get("conversation_id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "unknown"
    )


def _semantic_state_path(session_id: str, ocm_dir: Path) -> Path:
    return ocm_dir / f"semantic_state_{session_id}.json"


def _read_semantic_state(session_id: str, ocm_dir: Path) -> dict[str, Any]:
    spath = _semantic_state_path(session_id, ocm_dir)
    if not spath.exists():
        return {
            "tool_calls_since_semantic_checkpoint": 0,
            "last_semantic_checkpoint_at": None,
            "last_checkpoint_mode": "machine",
            "stale_semantic_required": False,
        }
    try:
        return json.loads(spath.read_text(encoding="utf-8"))
    except Exception:
        return {
            "tool_calls_since_semantic_checkpoint": 0,
            "last_semantic_checkpoint_at": None,
            "last_checkpoint_mode": "machine",
            "stale_semantic_required": False,
        }


def _write_semantic_state(session_id: str, ocm_dir: Path, state: dict[str, Any]) -> None:
    _semantic_state_path(session_id, ocm_dir).write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )


def _is_ocm_checkpoint_tool_use(data: dict[str, Any]) -> bool:
    tool_name = str(data.get("tool_name", "")).lower()
    if "checkpoint" in tool_name and "ocm" in tool_name:
        return True
    tool_input = data.get("tool_input")
    if isinstance(tool_input, dict):
        serialized = json.dumps(tool_input).lower()
        return "ocm__checkpoint" in serialized
    return False


def _is_semantic_checkpoint_tool_use(data: dict[str, Any]) -> bool:
    if not _is_ocm_checkpoint_tool_use(data):
        return False
    tool_input = data.get("tool_input")
    if isinstance(tool_input, dict):
        semantic_keys = {
            "goal",
            "work_completed",
            "work_pending",
            "work_summary",
            "decisions",
            "plan_files",
            "references",
        }
        return any(k in tool_input and tool_input.get(k) for k in semantic_keys)
    # Unknown shape from hook payload: assume semantic if checkpoint tool use occurred.
    return True


@click.group()
def main() -> None:
    """openCodeMemory hook dispatcher. Called by IDE hook scripts via stdin JSON."""


@main.command("session-start")
@click.option("--tool", default="claude-code", help="IDE tool name")
def session_start(tool: str) -> None:
    """Handle session-start event. Reads JSON from stdin."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = _resolve_session_id(data)
    project_root = _resolve_project_root(data)

    try:
        db = _open_or_skip(project_root)
        if db is None:
            return

        # No-op if session already exists
        existing = db.execute(
            "SELECT id FROM sessions WHERE id = ?", [session_id]
        ).fetchone()
        if existing is not None:
            return

        now = int(time.time())
        git_sha = get_head_sha(project_root)
        project_name = get_project_name(project_root)

        # Initial markdown path (before slug is known)
        filename = make_markdown_filename(now, tool, None, session_id)
        rel_path = f"sessions/{filename}"

        # Create sessions dir and blank markdown file (uses DB's own ocm_dir,
        # which is ~/.openCodeMemory for global DB or <project>/.openCodeMemory otherwise)
        sessions_dir = db.ocm_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        md_file = sessions_dir / filename
        md_file.write_text(
            f"---\nsession_id: {session_id}\ntool: {tool}\nproject: {project_name}\n---\n",
            encoding="utf-8",
        )

        db.execute(
            """
            INSERT INTO sessions (id, project, tool, started_at, status, markdown_path, git_sha_start)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
            """,
            [session_id, project_name, tool, now, rel_path, git_sha],
        )
        db.commit()
        _write_semantic_state(
            session_id,
            db.ocm_dir,
            {
                "tool_calls_since_semantic_checkpoint": 0,
                "last_semantic_checkpoint_at": None,
                "last_checkpoint_mode": "machine",
                "stale_semantic_required": False,
            },
        )
    except Exception as e:
        # Hook errors must not block the IDE; log to stderr and exit 0
        print(f"ocm-hook session-start error: {e}", file=sys.stderr)


@main.command("file-edited")
@click.option("--tool", default="claude-code", help="IDE tool name")
def file_edited(tool: str) -> None:
    """Handle file-edited event. Reads JSON from stdin."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = _resolve_session_id(data)

    # Extract file path: Claude Code nests under tool_input, Cursor puts it at top level
    tool_input = data.get("tool_input") or {}
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or data.get("file_path")
        or ""
    )
    tool_name = data.get("tool_name", "Edit")

    if not file_path:
        return

    project_root = _resolve_project_root(data)

    try:
        db = _open_or_skip(project_root)
        if db is None:
            return

        # Only track if session exists
        existing = db.execute(
            "SELECT id FROM sessions WHERE id = ?", [session_id]
        ).fetchone()
        if existing is None:
            return

        append_file(session_id, file_path, db.ocm_dir, tool_name=tool_name)
    except Exception as e:
        print(f"ocm-hook file-edited error: {e}", file=sys.stderr)


@main.command("session-end")
@click.option("--tool", default="claude-code", help="IDE tool name")
def session_end(tool: str) -> None:
    """Handle session-end event. Reads JSON from stdin."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = _resolve_session_id(data)
    project_root = _resolve_project_root(data)

    try:
        db = _open_or_skip(project_root)
        if db is None:
            return

        session = db.execute(
            "SELECT id, status FROM sessions WHERE id = ?", [session_id]
        ).fetchone()
        if session is None:
            return

        # Idempotent: only close if currently open
        if session["status"] == "open":
            now = int(time.time())
            git_sha = get_head_sha(project_root)
            db.execute(
                "UPDATE sessions SET status = 'closed', ended_at = ?, git_sha_end = ? WHERE id = ?",
                [now, git_sha, session_id],
            )
            db.commit()

        # Best-effort state cleanup
        try:
            _semantic_state_path(session_id, db.ocm_dir).unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as e:
        print(f"ocm-hook session-end error: {e}", file=sys.stderr)


@main.command("post-tool-use")
@click.option("--tool", default="claude-code", help="IDE tool name")
@click.option("--threshold", default=5, type=int, help="Semantic checkpoint threshold in tool calls")
def post_tool_use(tool: str, threshold: int) -> None:
    """Handle post-tool-use lifecycle. Reads JSON from stdin."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = _resolve_session_id(data)
    project_root = _resolve_project_root(data)

    try:
        db = _open_or_skip(project_root)
        if db is None:
            return

        # Ensure session exists (or auto-create via checkpoint path).
        from ocm.tools.checkpoint import ocm__checkpoint
        from ocm.tools import checkpoint as cp_module

        cp_module._db = db

        state = _read_semantic_state(session_id, db.ocm_dir)

        if _is_semantic_checkpoint_tool_use(data):
            state["tool_calls_since_semantic_checkpoint"] = 0
            state["last_semantic_checkpoint_at"] = int(time.time())
            state["last_checkpoint_mode"] = "semantic"
            state["stale_semantic_required"] = False
            _write_semantic_state(session_id, db.ocm_dir, state)
            return

        # Keep session freshness current after every tool call.
        if not _is_ocm_checkpoint_tool_use(data):
            ocm__checkpoint(session_id=session_id, tool=tool)

        count = int(state.get("tool_calls_since_semantic_checkpoint") or 0) + 1
        state["tool_calls_since_semantic_checkpoint"] = count
        state["last_checkpoint_mode"] = "machine"
        if count >= threshold:
            state["stale_semantic_required"] = True
            reminder = (
                f"openCodeMemory reminder: semantic checkpoint required for session {session_id}. "
                f"Please call ocm__checkpoint with updated goal/work_summary/decisions now "
                f"(threshold={threshold} tool calls)."
            )
            # Emit both Cursor and Claude-style context fields.
            print(json.dumps({
                "additional_context": reminder,
                "additionalContext": reminder,
            }))
        _write_semantic_state(session_id, db.ocm_dir, state)
    except Exception as e:
        print(f"ocm-hook post-tool-use error: {e}", file=sys.stderr)


@main.command("pre-tool-use")
@click.option("--threshold", default=5, type=int, help="Semantic checkpoint threshold in tool calls")
def pre_tool_use(threshold: int) -> None:
    """Gate tool use when semantic checkpoint is stale. Reads JSON from stdin."""
    _ = threshold
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    session_id = _resolve_session_id(data)
    project_root = _resolve_project_root(data)

    try:
        db = _open_or_skip(project_root)
        if db is None:
            return
        state = _read_semantic_state(session_id, db.ocm_dir)
        if not state.get("stale_semantic_required"):
            return

        # Allow checkpoint tool itself to pass through so the model can recover.
        if _is_ocm_checkpoint_tool_use(data):
            return

        msg = (
            f"Semantic checkpoint required for session {session_id}. "
            "Call ocm__checkpoint with updated semantic fields before more tool use."
        )
        # Emit responses compatible with both Cursor and Claude.
        print(json.dumps({
            "permission": "deny",
            "user_message": msg,
            "agent_message": msg,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": msg,
            },
        }))
        raise SystemExit(2)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ocm-hook pre-tool-use error: {e}", file=sys.stderr)


def _open_or_skip(project_root: Path):
    """Open per-project DB, or fall back to global DB, or return None."""
    db_path = project_root / ".openCodeMemory" / "memory.db"
    if db_path.exists():
        from ocm.storage.db import Database
        return Database.init(db_path)
    global_db = Path.home() / ".openCodeMemory" / "memory.db"
    if global_db.exists():
        from ocm.storage.db import Database
        return Database.init(global_db)
    return None


if __name__ == "__main__":
    main()
