from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

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
    except Exception as e:
        print(f"ocm-hook session-end error: {e}", file=sys.stderr)


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
