from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self._conn = conn
        self._db_path = db_path

    @property
    def project_root(self) -> Path:
        # db is at <project>/.openCodeMemory/memory.db
        return self._db_path.parent.parent

    @property
    def ocm_dir(self) -> Path:
        return self._db_path.parent

    @classmethod
    def for_project(cls, cwd: Path | None = None) -> "Database":
        # Check env var first — set by MCP server config as OCM_PROJECT_DIR
        ocm_project_dir = os.environ.get("OCM_PROJECT_DIR")
        if ocm_project_dir:
            db_path = Path(ocm_project_dir) / ".openCodeMemory" / "memory.db"
            if db_path.exists():
                return cls._connect(db_path)

        # Walk up from cwd to find .openCodeMemory/memory.db
        start = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        current = start
        while True:
            candidate = current / ".openCodeMemory" / "memory.db"
            if candidate.exists():
                return cls._connect(candidate)
            parent = current.parent
            if parent == current:
                break
            current = parent

        # Step 3: global fallback
        global_db = Path.home() / ".openCodeMemory" / "memory.db"
        if global_db.exists():
            return cls._connect(global_db)

        raise FileNotFoundError(
            "No openCodeMemory database found. "
            "Run 'ocm init' in your project or 'ocm install' for global setup."
        )

    @classmethod
    def init(cls, db_path: Path) -> "Database":
        """Create or open a database at db_path, running schema migrations."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return cls._connect(db_path)

    @classmethod
    def _connect(cls, db_path: Path) -> "Database":
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text()
        conn.executescript(schema_sql)
        conn.commit()

        return cls(conn, db_path)

    def execute(self, sql: str, params: list | None = None) -> sqlite3.Cursor:
        return self._conn.execute(sql, params or [])

    def executemany(self, sql: str, params_seq: list) -> sqlite3.Cursor:
        return self._conn.executemany(sql, params_seq)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
