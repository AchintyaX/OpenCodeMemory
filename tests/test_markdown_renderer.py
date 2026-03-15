"""Tests for markdown_renderer.render_session."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ocm.storage.db import Database
from ocm.storage.markdown_renderer import render_session, make_markdown_filename


def make_db(tmp_path: Path) -> Database:
    db_path = tmp_path / ".openCodeMemory" / "memory.db"
    db = Database.init(db_path)
    (tmp_path / ".openCodeMemory" / "sessions").mkdir(parents=True, exist_ok=True)
    return db


def insert_session(db: Database, session_id: str, **kwargs) -> None:
    now = int(time.time())
    filename = make_markdown_filename(now, kwargs.get("tool", "claude-code"), None, session_id)
    rel_path = f"sessions/{filename}"
    (db.ocm_dir / "sessions").mkdir(parents=True, exist_ok=True)
    db.execute(
        """
        INSERT INTO sessions (id, project, tool, started_at, status, markdown_path, goal, git_sha_start)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
        """,
        [
            session_id,
            kwargs.get("project", "test-project"),
            kwargs.get("tool", "claude-code"),
            now,
            rel_path,
            kwargs.get("goal"),
            kwargs.get("git_sha_start", "abc1234"),
        ],
    )
    db.commit()


def insert_chunk(db: Database, session_id: str, chunk_type: str, content: str) -> None:
    db.execute(
        "INSERT INTO session_chunks (session_id, chunk_type, content, created_at) VALUES (?, ?, ?, ?)",
        [session_id, chunk_type, content, int(time.time())],
    )
    db.commit()


def insert_file(db: Database, session_id: str, file_path: str, change_type: str) -> None:
    db.execute(
        "INSERT INTO session_files (session_id, file_path, change_type) VALUES (?, ?, ?)",
        [session_id, file_path, change_type],
    )
    db.commit()


class TestFrontmatter:
    def test_contains_session_id(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-001")
        md_path = render_session("render-001", db)
        content = md_path.read_text()
        assert "session_id: render-001" in content

    def test_contains_tool(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-002", tool="cursor")
        md_path = render_session("render-002", db)
        content = md_path.read_text()
        assert "tool: cursor" in content

    def test_yaml_delimiters(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-003")
        md_path = render_session("render-003", db)
        content = md_path.read_text()
        lines = content.splitlines()
        assert lines[0] == "---"
        # Find second ---
        second_dash = lines.index("---", 1)
        assert second_dash > 0


class TestGoalSection:
    def test_goal_from_session_row(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-004", goal="Implement the authentication flow.")
        md_path = render_session("render-004", db)
        content = md_path.read_text()
        assert "## Goal" in content
        assert "Implement the authentication flow." in content


class TestTodosSection:
    def test_work_completed_bullets(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-005")
        insert_chunk(db, "render-005", "work_completed", "Finished auth module")
        insert_chunk(db, "render-005", "work_completed", "Added unit tests")
        md_path = render_session("render-005", db)
        content = md_path.read_text()
        assert "✅ Work Completed" in content
        assert "- Finished auth module" in content
        assert "- Added unit tests" in content

    def test_work_pending_bullets(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-006")
        insert_chunk(db, "render-006", "work_pending", "Write integration tests")
        md_path = render_session("render-006", db)
        content = md_path.read_text()
        assert "🔲 Work To Be Completed" in content
        assert "- Write integration tests" in content


class TestFilesTouched:
    def test_created_files(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-007")
        insert_file(db, "render-007", "src/new_module.py", "created")
        md_path = render_session("render-007", db)
        content = md_path.read_text()
        assert "### Created" in content
        assert "`src/new_module.py`" in content

    def test_modified_files(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-008")
        insert_file(db, "render-008", "src/existing.py", "modified")
        md_path = render_session("render-008", db)
        content = md_path.read_text()
        assert "### Modified" in content
        assert "`src/existing.py`" in content

    def test_deleted_files(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-009")
        insert_file(db, "render-009", "src/old.py", "deleted")
        md_path = render_session("render-009", db)
        content = md_path.read_text()
        assert "### Deleted" in content
        assert "`src/old.py`" in content

    def test_missing_change_type_omitted(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-010")
        # No files inserted
        md_path = render_session("render-010", db)
        content = md_path.read_text()
        assert "### Created" not in content
        assert "### Deleted" not in content


class TestPlanFilesTable:
    def test_plan_file_table(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-011")
        insert_chunk(
            db, "render-011", "plan_file",
            json.dumps({"path": "docs/plan.md", "header": "## Migration Plan"})
        )
        md_path = render_session("render-011", db)
        content = md_path.read_text()
        assert "## Plan Files" in content
        assert "| File | Description |" in content
        assert "`docs/plan.md`" in content
        assert "## Migration Plan" in content


class TestArchitectureDecisions:
    def test_decision_bullets(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-012")
        insert_chunk(db, "render-012", "decision", "**SQLite over Postgres:** Simpler, no server needed.")
        md_path = render_session("render-012", db)
        content = md_path.read_text()
        assert "## Architecture Decisions" in content
        assert "**SQLite over Postgres:**" in content


class TestReferences:
    def test_reference_link(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-013")
        insert_chunk(
            db, "render-013", "reference",
            json.dumps({"url": "https://example.com/docs", "title": "Example Docs"})
        )
        md_path = render_session("render-013", db)
        content = md_path.read_text()
        assert "## References" in content
        assert "[Example Docs](https://example.com/docs)" in content


class TestDiffSummary:
    def test_diff_summary_rendered(self, tmp_path):
        db = make_db(tmp_path)
        insert_session(db, "render-014")
        insert_chunk(db, "render-014", "diff_summary", "3 files changed, +45/-12 lines.")
        md_path = render_session("render-014", db)
        content = md_path.read_text()
        assert "## Git Diff Summary" in content
        assert "3 files changed" in content


class TestFilenameGeneration:
    def test_filename_with_slug(self):
        name = make_markdown_filename(1740312000, "cursor", "tei-migration", "abc123")
        assert "cursor" in name
        assert "tei-migration" in name
        assert name.endswith(".md")

    def test_filename_without_slug_uses_id_prefix(self):
        name = make_markdown_filename(1740312000, "claude-code", None, "abcdef12345")
        assert "claude-code" in name
        assert "abcdef12" in name  # first 8 chars of session_id
        assert name.endswith(".md")
