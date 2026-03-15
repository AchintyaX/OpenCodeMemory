from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

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


def ocm__list_sessions(
    limit: int = 10,
    tool_filter: str | None = None,
) -> list[dict]:
    """
    Returns recent sessions ordered by start time (newest first).
    Each result: { session_id, goal, date, tool, status, markdown_path }
    """
    db = _get_db()
    sql = "SELECT id, goal, started_at, tool, status, markdown_path FROM sessions"
    params: list = []

    if tool_filter:
        sql += " WHERE tool = ?"
        params.append(tool_filter)

    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()
    results = []
    for row in rows:
        abs_path = db.project_root / ".openCodeMemory" / row["markdown_path"]
        results.append({
            "session_id": row["id"],
            "goal": row["goal"] or "",
            "date": datetime.fromtimestamp(row["started_at"]).date().isoformat(),
            "tool": row["tool"],
            "status": row["status"],
            "markdown_path": str(abs_path),
        })
    return results


def ocm__get_session_files(session_id: str) -> list[dict]:
    """
    Returns all files touched in the session.
    Each result: { "path": str, "change_type": "created" | "modified" | "deleted" }
    Ordered by change_type (created first, then modified, then deleted).
    """
    db = _get_db()

    order_sql = "CASE change_type WHEN 'created' THEN 1 WHEN 'modified' THEN 2 WHEN 'deleted' THEN 3 ELSE 4 END"
    rows = db.execute(
        f"SELECT file_path, change_type FROM session_files WHERE session_id = ? ORDER BY {order_sql}, file_path",
        [session_id],
    ).fetchall()

    return [{"path": row["file_path"], "change_type": row["change_type"]} for row in rows]
