from __future__ import annotations

from typing import TYPE_CHECKING

from ocm.search.fts import search as _search

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


def ocm__search_sessions(
    query: str,
    limit: int = 5,
    scope: str = "project",
    tool_filter: str | None = None,
) -> list[dict] | str:
    """
    Search for sessions matching a natural language query.

    Returns a list of up to 5 matching sessions, each with:
    { rank, score, session_id, goal, date, tool, top_files, markdown_path }

    Returns the string "No sessions found matching your query." if no results
    exceed the relevance threshold.
    """
    db = _get_db()
    limit = min(max(limit, 1), 5)

    if scope == "global":
        results = _search_global(query, limit, tool_filter)
    else:
        results = _search(query, db, limit=limit, tool_filter=tool_filter)

    if not results:
        return "No sessions found matching your query."

    return [r.to_dict() for r in results]


def _search_global(
    query: str,
    limit: int,
    tool_filter: str | None,
) -> list:
    """Search across all registered project databases."""
    import json
    from pathlib import Path

    registry_path = Path.home() / ".openCodeMemory" / "registry.json"
    if not registry_path.exists():
        db = _get_db()
        return _search(query, db, limit=limit, tool_filter=tool_filter)

    try:
        registry = json.loads(registry_path.read_text())
    except Exception:
        db = _get_db()
        return _search(query, db, limit=limit, tool_filter=tool_filter)

    from ocm.storage.db import Database

    all_results = []
    for entry in registry:
        db_path = Path(entry.get("db_path", ""))
        if not db_path.exists():
            continue
        try:
            project_db = Database.init(db_path)
            results = _search(query, project_db, limit=limit, tool_filter=tool_filter)
            all_results.extend(results)
        except Exception:
            continue

    all_results.sort(key=lambda r: -r.score)
    return all_results[:limit]
