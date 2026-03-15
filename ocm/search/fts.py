from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ocm.search.preprocessor import ParsedQuery, extract_filters

if TYPE_CHECKING:
    from ocm.storage.db import Database

THRESHOLD = 0.0  # BM25 score; lower (more negative) = better match. FTS5 returns no rows for non-matching queries, so 0.0 accepts all term matches.


@dataclass
class SearchResult:
    rank: int
    score: float
    session_id: str
    goal: str
    date: str
    tool: str
    top_files: list[str]
    markdown_path: str

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "score": self.score,
            "session_id": self.session_id,
            "goal": self.goal,
            "date": self.date,
            "tool": self.tool,
            "top_files": self.top_files,
            "markdown_path": self.markdown_path,
        }


def sanitize_fts_query(query: str) -> str:
    """Remove FTS5 special chars that cause parse errors."""
    safe = re.sub(r'[",\-:()\^*\[\]]', " ", query)
    return " ".join(safe.split())


def fts_search(clean_query: str, db: "Database") -> list[tuple[str, float]]:
    """
    Run BM25 FTS5 search.
    Returns (session_id, bm25_score) pairs ordered by score (lower = better).
    """
    safe = sanitize_fts_query(clean_query)
    if not safe.strip():
        return []
    try:
        rows = db.execute(
            """
            SELECT session_id, bm25(sessions_fts, 0, 10, 5, 5, 8, 3) AS score
            FROM sessions_fts
            WHERE sessions_fts MATCH ?
            ORDER BY score
            LIMIT 20
            """,
            [safe],
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
    except Exception:
        return []


def path_search(path_hint: str, db: "Database") -> list[str]:
    """Return session_ids where any touched file matches the path hint."""
    rows = db.execute(
        "SELECT DISTINCT session_id FROM session_files WHERE file_path LIKE ?",
        [f"%{path_hint}%"],
    ).fetchall()
    return [row[0] for row in rows]


def apply_filters(
    candidate_ids: list[str],
    parsed: ParsedQuery,
    tool_filter: str | None,
    db: "Database",
) -> list[str]:
    """Filter candidate session_ids by date range and tool."""
    if not candidate_ids:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    params: list = list(candidate_ids)
    sql = f"SELECT id FROM sessions WHERE id IN ({placeholders})"
    conditions = []

    if parsed.date_after is not None:
        conditions.append("started_at >= ?")
        params.append(parsed.date_after)
    if parsed.date_before is not None:
        conditions.append("started_at <= ?")
        params.append(parsed.date_before)
    if parsed.tool_hint:
        conditions.append("tool = ?")
        params.append(parsed.tool_hint)
    if tool_filter:
        conditions.append("tool = ?")
        params.append(tool_filter)

    if conditions:
        sql += " AND " + " AND ".join(conditions)

    rows = db.execute(sql, params).fetchall()
    return [row[0] for row in rows]


def enrich(session_id: str, score: float, rank: int, db: "Database") -> SearchResult:
    """Fetch session metadata and top 3 files for a result."""
    session = db.execute(
        "SELECT goal, started_at, tool, markdown_path FROM sessions WHERE id = ?",
        [session_id],
    ).fetchone()
    top_files_rows = db.execute(
        "SELECT file_path FROM session_files WHERE session_id = ? LIMIT 3",
        [session_id],
    ).fetchall()
    abs_path = db.project_root / ".openCodeMemory" / session["markdown_path"]
    return SearchResult(
        rank=rank,
        score=round(-score, 3),
        session_id=session_id,
        goal=session["goal"] or "",
        date=datetime.fromtimestamp(session["started_at"]).date().isoformat(),
        tool=session["tool"],
        top_files=[row[0] for row in top_files_rows],
        markdown_path=str(abs_path),
    )


def search(
    query: str,
    db: "Database",
    limit: int = 5,
    tool_filter: str | None = None,
) -> list[SearchResult]:
    """Full search pipeline: parse → FTS → path fallback → filter → threshold → enrich."""
    limit = min(limit, 5)

    parsed = extract_filters(query)
    fts_results = fts_search(parsed.clean_query, db)
    result_map: dict[str, float] = {sid: score for sid, score in fts_results}

    if parsed.has_path_hint:
        for sid in path_search(parsed.path_hint, db):
            if sid not in result_map:
                result_map[sid] = -0.20

    candidate_ids = list(result_map.keys())
    filtered_ids = apply_filters(candidate_ids, parsed, tool_filter, db)

    above = [
        (sid, result_map[sid])
        for sid in filtered_ids
        if result_map[sid] <= THRESHOLD
    ]

    above.sort(key=lambda x: x[1])
    top = above[:limit]

    return [
        enrich(sid, score, rank + 1, db)
        for rank, (sid, score) in enumerate(top)
    ]
