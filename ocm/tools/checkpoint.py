from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ocm.hooks.file_tracker import flush_journal
from ocm.storage.markdown_renderer import make_markdown_filename, render_session

if TYPE_CHECKING:
    from ocm.storage.db import Database

_db: "Database | None" = None


def _init(db: "Database") -> None:
    global _db
    _db = db


def _get_db() -> "Database":
    if _db is not None:
        return _db
    from ocm.storage.db import Database
    return Database.for_project()


def ocm__checkpoint(
    session_id: str,
    slug: str | None = None,
    goal: str | None = None,
    tool: str = "claude-code",
    work_completed: list[str] | None = None,
    work_pending: list[str] | None = None,
    work_summary: list[str] | None = None,
    decisions: list[str] | None = None,
    plan_files: list[dict] | None = None,
    references: list[dict] | None = None,
    status: str | None = None,
) -> dict:
    """
    Save or update the current session state. Renders the markdown file.
    Returns: { "session_id": str, "markdown_path": str, "status": str }
    """
    db = _get_db()
    now = int(time.time())

    session = db.execute(
        "SELECT * FROM sessions WHERE id = ?", [session_id]
    ).fetchone()
    if session is None:
        from ocm.hooks.git import get_head_sha, get_project_name
        try:
            git_sha = get_head_sha(db.project_root)
            project_name = get_project_name(db.project_root)
        except Exception:
            git_sha = None
            project_name = db.project_root.name

        filename = make_markdown_filename(now, tool, None, session_id)
        rel_path = f"sessions/{filename}"
        sessions_dir = db.ocm_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / filename).write_text(
            f"---\nsession_id: {session_id}\ntool: {tool}\nproject: {project_name}\n---\n",
            encoding="utf-8",
        )
        db.execute(
            """INSERT INTO sessions (id, project, tool, started_at, status, markdown_path, git_sha_start)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            [session_id, project_name, tool, now, rel_path, git_sha],
        )
        db.commit()
        session = db.execute("SELECT * FROM sessions WHERE id = ?", [session_id]).fetchone()

    # --- Update sessions row ---
    if goal is not None:
        db.execute("UPDATE sessions SET goal = ? WHERE id = ?", [goal, session_id])

    # Handle slug + possible file rename
    if slug is not None and session["slug"] is None:
        new_filename = make_markdown_filename(session["started_at"], session["tool"], slug, session_id)
        new_rel_path = f"sessions/{new_filename}"
        old_abs = db.ocm_dir / session["markdown_path"]
        new_abs = db.ocm_dir / new_rel_path
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        if old_abs.exists() and old_abs != new_abs:
            old_abs.rename(new_abs)
        db.execute(
            "UPDATE sessions SET slug = ?, markdown_path = ? WHERE id = ?",
            [slug, new_rel_path, session_id],
        )

    if status is not None:
        db.execute("UPDATE sessions SET status = ? WHERE id = ?", [status, session_id])
        if status == "closed":
            db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", [now, session_id])

    # --- Replace work_pending (and diff_summary) ---
    if work_pending is not None:
        db.execute(
            "DELETE FROM session_chunks WHERE session_id = ? AND chunk_type = 'work_pending'",
            [session_id],
        )
        for item in work_pending:
            db.execute(
                "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'work_pending', ?, ?)",
                [session_id, item, now],
            )

    # --- Compute and replace diff_summary ---
    diff_stat = _compute_diff_stat(session, db)
    if diff_stat:
        db.execute(
            "DELETE FROM session_chunks WHERE session_id = ? AND chunk_type = 'diff_summary'",
            [session_id],
        )
        db.execute(
            "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'diff_summary', ?, ?)",
            [session_id, diff_stat, now],
        )

    # --- Append new chunks ---
    append_map = [
        ("work_completed", work_completed),
        ("work_summary", work_summary),
        ("decision", decisions),
    ]
    for chunk_type, items in append_map:
        if items:
            for item in items:
                db.execute(
                    "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, ?, ?, ?)",
                    [session_id, chunk_type, item, now],
                )

    if plan_files:
        for pf in plan_files:
            db.execute(
                "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'plan_file', ?, ?)",
                [session_id, json.dumps(pf), now],
            )

    if references:
        for ref in references:
            db.execute(
                "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'reference', ?, ?)",
                [session_id, json.dumps(ref), now],
            )

    # --- Flush file journal ---
    journal_entries = flush_journal(session_id, db.ocm_dir)
    _upsert_files(session_id, journal_entries, session, db)

    # --- Render markdown ---
    md_path = render_session(session_id, db)

    # --- Rebuild FTS5 row ---
    _rebuild_fts(session_id, db)

    db.commit()

    # Re-fetch to get latest markdown_path and status
    updated = db.execute(
        "SELECT markdown_path, status FROM sessions WHERE id = ?", [session_id]
    ).fetchone()

    return {
        "session_id": session_id,
        "markdown_path": str(db.project_root / ".openCodeMemory" / updated["markdown_path"]),
        "status": updated["status"],
    }


def _compute_diff_stat(session: object, db: "Database") -> str:
    """Compute git diff --stat from git_sha_start to HEAD."""
    sha_start = session["git_sha_start"]
    if not sha_start:
        return ""
    try:
        from ocm.hooks.git import get_diff_stat
        return get_diff_stat(sha_start, db.project_root)
    except Exception:
        return ""


def _upsert_files(
    session_id: str,
    journal_entries: list[dict],
    session: object,
    db: "Database",
) -> None:
    """Upsert file changes from the journal into session_files."""
    entries = list(journal_entries)

    # Fall back to git diff when PostToolUse hook not running
    if not entries and session["git_sha_start"]:
        from ocm.hooks.git import get_changed_files
        git_files = get_changed_files(session["git_sha_start"], db.project_root)
        entries = [
            {"path": p, "tool": "Write" if ct == "created" else "Edit"}
            for p, ct in git_files
        ]

    for entry in entries:
        raw_path = entry.get("path", "")
        tool_name = entry.get("tool", "Edit")

        # Make path relative to project root
        try:
            abs_path = Path(raw_path)
            rel_path = str(abs_path.relative_to(db.project_root))
        except ValueError:
            rel_path = raw_path

        existing = db.execute(
            "SELECT change_type FROM session_files WHERE session_id = ? AND file_path = ?",
            [session_id, rel_path],
        ).fetchone()

        if existing is None:
            # Determine if created or modified based on tool
            change_type = "created" if tool_name == "Write" else "modified"
            db.execute(
                "INSERT INTO session_files (session_id, file_path, change_type) VALUES (?, ?, ?)",
                [session_id, rel_path, change_type],
            )
        # If already exists, keep existing change_type (created stays created)


def _rebuild_fts(session_id: str, db: "Database") -> None:
    """Delete and re-insert the FTS5 row for this session."""
    db.execute("DELETE FROM sessions_fts WHERE session_id = ?", [session_id])

    chunks = db.execute(
        "SELECT chunk_type, content FROM session_chunks WHERE session_id = ?",
        [session_id],
    ).fetchall()

    files = db.execute(
        "SELECT file_path FROM session_files WHERE session_id = ?",
        [session_id],
    ).fetchall()

    session = db.execute(
        "SELECT goal FROM sessions WHERE id = ?", [session_id]
    ).fetchone()

    by_type: dict[str, list[str]] = {}
    for chunk in chunks:
        by_type.setdefault(chunk["chunk_type"], []).append(chunk["content"])

    goal = session["goal"] or ""
    todos = " ".join(
        by_type.get("work_completed", []) + by_type.get("work_pending", [])
    )
    file_paths = " ".join(row[0] for row in files)
    decisions = " ".join(by_type.get("decision", []))
    work_summary = " ".join(by_type.get("work_summary", []))

    db.execute(
        "INSERT INTO sessions_fts (session_id, goal, todos, file_paths, decisions, work_summary) VALUES (?, ?, ?, ?, ?, ?)",
        [session_id, goal, todos, file_paths, decisions, work_summary],
    )
