from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ocm.storage.db import Database


def render_session(session_id: str, db: "Database") -> Path:
    """
    Render the full markdown file for a session from session_chunks and session_files.
    Writes to .openCodeMemory/sessions/<filename>.md and returns the absolute path.
    """
    session = db.execute(
        "SELECT * FROM sessions WHERE id = ?", [session_id]
    ).fetchone()
    if session is None:
        raise ValueError(f"Session not found: {session_id}")

    chunks = db.execute(
        "SELECT chunk_type, content FROM session_chunks WHERE session_id = ? ORDER BY id ASC",
        [session_id],
    ).fetchall()

    files = db.execute(
        "SELECT file_path, change_type FROM session_files WHERE session_id = ? ORDER BY change_type, file_path",
        [session_id],
    ).fetchall()

    md = _assemble_markdown(session, chunks, files)

    # Determine output path from markdown_path stored in DB
    sessions_dir = db.ocm_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    md_path = db.ocm_dir / session["markdown_path"]
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")

    return md_path


def _assemble_markdown(session: object, chunks: list, files: list) -> str:
    lines: list[str] = []

    # --- YAML frontmatter ---
    started_dt = datetime.fromtimestamp(session["started_at"])
    started_iso = started_dt.strftime("%Y-%m-%dT%H:%M:%S")
    git_end = session["git_sha_end"] or "null"
    trigger = session["trigger"] or "null"

    lines += [
        "---",
        f"session_id: {session['id']}",
        f"tool: {session['tool']}",
        f"project: {session['project']}",
        f"started_at: {started_iso}",
        f"git_sha_start: {session['git_sha_start'] or 'null'}",
        f"git_sha_end: {git_end}",
        f"trigger: {trigger}",
        "---",
        "",
    ]

    # Collect chunks by type
    by_type: dict[str, list[str]] = {}
    for chunk in chunks:
        by_type.setdefault(chunk["chunk_type"], []).append(chunk["content"])

    # --- Goal ---
    goal_text = (session["goal"] or "").strip()
    lines += [f"## Goal", goal_text, ""]

    # --- Todos ---
    lines += ["---", "", "## Todos", ""]
    lines += ["### ✅ Work Completed"]
    for item in by_type.get("work_completed", []):
        for bullet in _split_bullets(item):
            lines.append(f"- {bullet}")
    lines.append("")

    lines += ["### 🔲 Work To Be Completed"]
    for item in by_type.get("work_pending", []):
        for bullet in _split_bullets(item):
            lines.append(f"- {bullet}")
    lines.append("")

    # --- Files Touched ---
    created_files = [f["file_path"] for f in files if f["change_type"] == "created"]
    modified_files = [f["file_path"] for f in files if f["change_type"] == "modified"]
    deleted_files = [f["file_path"] for f in files if f["change_type"] == "deleted"]

    lines += ["---", "", "## Files Touched", ""]
    if created_files:
        lines.append("### Created")
        for fp in created_files:
            lines.append(f"- `{fp}`")
        lines.append("")
    if modified_files:
        lines.append("### Modified")
        for fp in modified_files:
            lines.append(f"- `{fp}`")
        lines.append("")
    if deleted_files:
        lines.append("### Deleted")
        for fp in deleted_files:
            lines.append(f"- `{fp}`")
        lines.append("")

    # --- Git Diff Summary ---
    diff_summaries = by_type.get("diff_summary", [])
    lines += ["---", "", "## Git Diff Summary"]
    if diff_summaries:
        lines.append(diff_summaries[-1].strip())
    lines.append("")

    # --- Work Done ---
    lines += ["---", "", "## Work Done"]
    for item in by_type.get("work_summary", []):
        for bullet in _split_bullets(item):
            lines.append(f"- {bullet}")
    lines.append("")

    # --- Plan Files ---
    plan_files = by_type.get("plan_file", [])
    lines += ["---", "", "## Plan Files", ""]
    if plan_files:
        lines.append("| File | Description |")
        lines.append("|------|-------------|")
        for pf_json in plan_files:
            try:
                pf = json.loads(pf_json)
                path = pf.get("path", "")
                header = pf.get("header", "")
                lines.append(f"| `{path}` | {header} |")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"| {pf_json} |  |")
    lines.append("")

    # --- Architecture Decisions ---
    lines += ["---", "", "## Architecture Decisions", ""]
    for item in by_type.get("decision", []):
        for bullet in _split_bullets(item):
            lines.append(f"- {bullet}")
    lines.append("")

    # --- References ---
    lines += ["---", "", "## References", ""]
    for ref_json in by_type.get("reference", []):
        try:
            ref = json.loads(ref_json)
            url = ref.get("url", "")
            title = ref.get("title", url)
            lines.append(f"- [{title}]({url})")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"- {ref_json}")

    return "\n".join(lines) + "\n"


def _split_bullets(text: str) -> list[str]:
    """Split a chunk's content into individual bullet items."""
    items = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:])
        elif line:
            items.append(line)
    return items if items else [text.strip()]


def make_markdown_filename(started_at: int, tool: str, slug: str | None, session_id: str) -> str:
    """Generate a markdown filename for a session."""
    dt = datetime.fromtimestamp(started_at)
    dt_str = dt.strftime("%Y-%m-%d_%H-%M")
    suffix = slug if slug else session_id[:8]
    return f"{dt_str}_{tool}_{suffix}.md"
