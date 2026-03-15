"""Tests for the search pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ocm.search.fts import search, fts_search, sanitize_fts_query, THRESHOLD
from ocm.search.preprocessor import extract_filters
from ocm.storage.db import Database
from ocm.tools.checkpoint import _rebuild_fts


def make_db(tmp_path: Path) -> Database:
    db_path = tmp_path / ".openCodeMemory" / "memory.db"
    db = Database.init(db_path)
    (tmp_path / ".openCodeMemory" / "sessions").mkdir(parents=True, exist_ok=True)
    return db


def insert_full_session(
    db: Database,
    session_id: str,
    goal: str,
    tool: str = "claude-code",
    decisions: list[str] | None = None,
    work_summary: list[str] | None = None,
    files: list[tuple[str, str]] | None = None,
) -> None:
    """Insert a session with chunks and index it in FTS."""
    from ocm.storage.markdown_renderer import make_markdown_filename
    now = int(time.time())
    filename = make_markdown_filename(now, tool, None, session_id)
    rel_path = f"sessions/{filename}"
    (db.ocm_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (db.ocm_dir / "sessions" / filename).write_text("---\n---\n", encoding="utf-8")

    db.execute(
        """
        INSERT INTO sessions (id, project, tool, started_at, status, markdown_path, goal)
        VALUES (?, 'test-project', ?, ?, 'open', ?, ?)
        """,
        [session_id, tool, now, rel_path, goal],
    )

    for decision in (decisions or []):
        db.execute(
            "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'decision', ?, ?)",
            [session_id, decision, now],
        )

    for ws in (work_summary or []):
        db.execute(
            "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, 'work_summary', ?, ?)",
            [session_id, ws, now],
        )

    for fpath, ftype in (files or []):
        db.execute(
            "INSERT INTO session_files (session_id, file_path, change_type) VALUES (?, ?, ?)",
            [session_id, fpath, ftype],
        )

    db.commit()
    _rebuild_fts(session_id, db)
    db.commit()


class TestQuerySanitization:
    def test_removes_fts_special_chars(self):
        result = sanitize_fts_query('TEI-migration "embedding" latency')
        assert '"' not in result
        assert "-" not in result

    def test_preserves_alphanumeric(self):
        result = sanitize_fts_query("kv cache prefill optimization")
        assert "kv" in result
        assert "cache" in result


class TestQueryPreprocessor:
    def test_last_week_sets_date_after(self):
        parsed = extract_filters("kv cache session last week")
        assert parsed.date_after is not None
        assert parsed.date_after > 0
        assert "last week" not in parsed.clean_query

    def test_cursor_tool_hint(self):
        parsed = extract_filters("tei migration cursor session")
        assert parsed.tool_hint == "cursor"
        assert "cursor" not in parsed.clean_query.lower()

    def test_claude_code_tool_hint(self):
        parsed = extract_filters("embedding latency claude code session")
        assert parsed.tool_hint == "claude-code"

    def test_path_hint_extracted(self):
        parsed = extract_filters("changes to embedding_server.py last week")
        assert parsed.has_path_hint
        assert parsed.path_hint == "embedding_server.py"

    def test_stop_words_removed(self):
        parsed = extract_filters("the session where I fixed the bug")
        words = parsed.clean_query.split()
        assert "the" not in words
        assert "where" not in words
        assert "i" not in words


class TestFtsSearch:
    def test_relevant_session_returned(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "fts-001",
            goal="Migrate BGE-M3 embedding from vLLM to TEI for latency reduction.",
            decisions=["**TEI over Infinity:** Better BGE-M3 support."],
        )
        insert_full_session(
            db, "fts-002",
            goal="Implement JWT authentication for the REST API.",
        )

        results = fts_search("TEI migration embedding", db)
        session_ids = [r[0] for r in results]
        assert "fts-001" in session_ids

    def test_irrelevant_session_not_top(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "fts-003",
            goal="Optimize database query performance.",
            work_summary=["Added indexes to user table"],
        )
        insert_full_session(
            db, "fts-004",
            goal="Implement real-time embedding endpoint with TEI.",
            decisions=["**TEI selected:** 40% lower latency than vLLM."],
        )

        results = fts_search("TEI embedding endpoint", db)
        session_ids = [r[0] for r in results]
        if len(session_ids) >= 2:
            # fts-004 should rank higher than fts-003
            idx_relevant = session_ids.index("fts-004") if "fts-004" in session_ids else 999
            idx_irrelevant = session_ids.index("fts-003") if "fts-003" in session_ids else 999
            assert idx_relevant < idx_irrelevant


class TestThreshold:
    def test_weak_match_excluded(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "thresh-001",
            goal="Fix minor typo in README.",
        )

        # Query very unrelated to the session content
        results = search("complex distributed systems quantum computing", db)
        session_ids = [r.session_id for r in results]
        assert "thresh-001" not in session_ids

    def test_strong_match_included(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "thresh-002",
            goal="Implement distributed caching with Redis for session management.",
            decisions=["**Redis chosen:** In-memory speed, pub/sub support."],
            work_summary=["Integrated Redis client", "Added TTL-based eviction"],
        )

        results = search("Redis caching distributed session", db)
        assert len(results) > 0
        assert results[0].session_id == "thresh-002"


class TestPathFallback:
    def test_path_hint_finds_session(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "path-001",
            goal="Refactor the embedding server module.",
            files=[("src/inference/embedding_server.py", "modified")],
        )

        results = search("embedding_server.py", db)
        session_ids = [r.session_id for r in results]
        assert "path-001" in session_ids


class TestToolFilter:
    def test_tool_filter_excludes_wrong_tool(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "tool-001",
            goal="Implement TEI embedding pipeline.",
            tool="cursor",
        )
        insert_full_session(
            db, "tool-002",
            goal="Implement TEI embedding pipeline.",
            tool="claude-code",
        )

        results = search("TEI embedding pipeline", db, tool_filter="claude-code")
        session_ids = [r.session_id for r in results]
        assert "tool-002" in session_ids
        assert "tool-001" not in session_ids


class TestSearchResultStructure:
    def test_result_has_required_fields(self, tmp_path):
        db = make_db(tmp_path)
        insert_full_session(
            db, "struct-001",
            goal="Build the authentication service.",
            files=[("auth/service.py", "created")],
        )

        results = search("authentication service", db)
        if results:
            r = results[0]
            assert hasattr(r, "rank")
            assert hasattr(r, "score")
            assert hasattr(r, "session_id")
            assert hasattr(r, "goal")
            assert hasattr(r, "date")
            assert hasattr(r, "tool")
            assert hasattr(r, "top_files")
            assert hasattr(r, "markdown_path")
            assert r.score >= 0  # Positive (negated BM25)
            assert r.rank == 1
