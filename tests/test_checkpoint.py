"""Tests for ocm__checkpoint tool."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from ocm.storage.db import Database
from ocm.tools.checkpoint import ocm__checkpoint, _rebuild_fts


def make_db(tmp_path: Path) -> Database:
    """Create an initialized DB with sessions dir."""
    db_path = tmp_path / ".openCodeMemory" / "memory.db"
    db = Database.init(db_path)
    (tmp_path / ".openCodeMemory" / "sessions").mkdir(parents=True, exist_ok=True)
    return db


def create_session(db: Database, session_id: str, tool: str = "claude-code") -> None:
    """Insert a minimal session row for testing."""
    import time
    now = int(time.time())
    from ocm.storage.markdown_renderer import make_markdown_filename
    filename = make_markdown_filename(now, tool, None, session_id)
    rel_path = f"sessions/{filename}"

    # Create blank markdown file
    sessions_dir = db.ocm_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / filename).write_text("---\n---\n", encoding="utf-8")

    db.execute(
        """
        INSERT INTO sessions (id, project, tool, started_at, status, markdown_path)
        VALUES (?, 'test-project', ?, ?, 'open', ?)
        """,
        [session_id, tool, now, rel_path],
    )
    db.commit()


class TestFirstCheckpoint:
    def test_creates_markdown_file(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-001")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        result = ocm__checkpoint(
            session_id="sess-001",
            slug="test-feature",
            goal="Build a test feature for the demo.",
            work_completed=["Set up scaffolding"],
            work_pending=["Write tests", "Add docs"],
        )

        assert result["session_id"] == "sess-001"
        assert result["status"] == "open"
        md_path = Path(result["markdown_path"])
        assert md_path.exists()
        content = md_path.read_text()
        assert "Build a test feature for the demo." in content
        assert "Set up scaffolding" in content
        assert "Write tests" in content

    def test_slug_renames_file(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-002")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        result = ocm__checkpoint(
            session_id="sess-002",
            slug="kv-cache-impl",
            goal="Implement KV cache prefill.",
        )

        md_path = Path(result["markdown_path"])
        assert "kv-cache-impl" in md_path.name
        assert md_path.exists()

    def test_goal_stored_in_sessions_table(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-003")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(
            session_id="sess-003",
            goal="Optimize embedding latency.",
        )

        row = db.execute("SELECT goal FROM sessions WHERE id = ?", ["sess-003"]).fetchone()
        assert row["goal"] == "Optimize embedding latency."


class TestSubsequentCheckpoints:
    def test_work_completed_appends(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-004")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(session_id="sess-004", work_completed=["Task A"])
        ocm__checkpoint(session_id="sess-004", work_completed=["Task B"])

        chunks = db.execute(
            "SELECT content FROM session_chunks WHERE session_id = ? AND chunk_type = 'work_completed'",
            ["sess-004"],
        ).fetchall()
        contents = [c["content"] for c in chunks]
        assert "Task A" in contents
        assert "Task B" in contents
        assert len(contents) == 2

    def test_work_pending_replaced(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-005")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(session_id="sess-005", work_pending=["Old task 1", "Old task 2"])
        ocm__checkpoint(session_id="sess-005", work_pending=["New task 1"])

        chunks = db.execute(
            "SELECT content FROM session_chunks WHERE session_id = ? AND chunk_type = 'work_pending'",
            ["sess-005"],
        ).fetchall()
        contents = [c["content"] for c in chunks]
        assert contents == ["New task 1"]
        assert "Old task 1" not in contents

    def test_decisions_appended(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-006")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(session_id="sess-006", decisions=["**Choice A:** First reason."])
        ocm__checkpoint(session_id="sess-006", decisions=["**Choice B:** Second reason."])

        chunks = db.execute(
            "SELECT content FROM session_chunks WHERE session_id = ? AND chunk_type = 'decision'",
            ["sess-006"],
        ).fetchall()
        assert len(chunks) == 2


class TestStatusTransitions:
    def test_status_frozen(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-007")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        result = ocm__checkpoint(session_id="sess-007", status="frozen")
        assert result["status"] == "frozen"

    def test_status_closed_sets_ended_at(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-008")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(session_id="sess-008", status="closed")

        row = db.execute(
            "SELECT status, ended_at FROM sessions WHERE id = ?", ["sess-008"]
        ).fetchone()
        assert row["status"] == "closed"
        assert row["ended_at"] is not None


class TestPlanFilesAndReferences:
    def test_plan_files_stored_as_json(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-009")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(
            session_id="sess-009",
            plan_files=[{"path": "docs/plan.md", "header": "## My Plan"}],
        )

        chunks = db.execute(
            "SELECT content FROM session_chunks WHERE session_id = ? AND chunk_type = 'plan_file'",
            ["sess-009"],
        ).fetchall()
        assert len(chunks) == 1
        data = json.loads(chunks[0]["content"])
        assert data["path"] == "docs/plan.md"
        assert data["header"] == "## My Plan"

    def test_references_stored_as_json(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-010")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(
            session_id="sess-010",
            references=[{"url": "https://example.com", "title": "Example Docs"}],
        )

        chunks = db.execute(
            "SELECT content FROM session_chunks WHERE session_id = ? AND chunk_type = 'reference'",
            ["sess-010"],
        ).fetchall()
        assert len(chunks) == 1
        data = json.loads(chunks[0]["content"])
        assert data["url"] == "https://example.com"
        assert data["title"] == "Example Docs"


class TestFtsRebuild:
    def test_fts_row_created(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-011")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(
            session_id="sess-011",
            goal="Implement TEI migration for embeddings.",
        )

        row = db.execute(
            "SELECT goal FROM sessions_fts WHERE session_id = ?", ["sess-011"]
        ).fetchone()
        assert row is not None
        assert "TEI" in row["goal"]

    def test_fts_row_replaced_on_update(self, tmp_path):
        db = make_db(tmp_path)
        create_session(db, "sess-012")

        from ocm.tools import checkpoint as cp_module
        cp_module._db = db

        ocm__checkpoint(session_id="sess-012", goal="Initial goal.")
        ocm__checkpoint(session_id="sess-012", goal="Updated goal.")

        rows = db.execute(
            "SELECT count(*) as cnt FROM sessions_fts WHERE session_id = ?", ["sess-012"]
        ).fetchone()
        assert rows["cnt"] == 1
