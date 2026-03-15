CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    -- The session identifier. For Claude Code this is CLAUDE_SESSION_ID.
    -- For Cursor this is the conversation_id.

    project         TEXT NOT NULL,
    -- Derived from the git remote URL or the project directory name.

    tool            TEXT NOT NULL,
    -- One of: 'claude-code', 'cursor', 'antigravity'

    started_at      INTEGER NOT NULL,
    -- Unix timestamp. Recorded when the session-start hook fires.

    ended_at        INTEGER,
    -- Unix timestamp. Null until session-end hook fires.

    trigger         TEXT,
    -- How the last checkpoint was triggered:
    -- 'context_limit' | 'session_end' | 'manual' | 'git_commit'

    git_sha_start   TEXT,
    -- The HEAD SHA when the session started.

    git_sha_end     TEXT,
    -- The HEAD SHA at session end. Null for open sessions.

    status          TEXT NOT NULL DEFAULT 'open',
    -- 'open'   : session is active, may receive more checkpoints
    -- 'frozen' : context limit was hit; session is complete but not committed
    -- 'closed' : session ended normally

    markdown_path   TEXT NOT NULL,
    -- Relative path from the .openCodeMemory/ directory.
    -- Example: sessions/2026-02-23_14-32_cursor_tei-migration.md

    goal            TEXT,
    -- Duplicated from the first 'goal' chunk for fast display.

    slug            TEXT
    -- Short descriptor used in the filename. 4 words max, hyphenated.
);

CREATE TABLE IF NOT EXISTS session_files (
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    -- Relative to project root.

    change_type     TEXT NOT NULL,
    -- 'created' | 'modified' | 'deleted'

    PRIMARY KEY (session_id, file_path)
);

CREATE TABLE IF NOT EXISTS session_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,

    chunk_type      TEXT NOT NULL,
    -- Valid values:
    -- 'work_completed' : appended; rendered as ✅ bullets
    -- 'work_pending'   : replaced each checkpoint; rendered as 🔲 bullets
    -- 'work_summary'   : appended; rendered under "Work Done"
    -- 'decision'       : appended; rendered under "Architecture Decisions"
    -- 'plan_file'      : appended; JSON {"path":..., "header":...}
    -- 'reference'      : appended; JSON {"url":..., "title":...}
    -- 'diff_summary'   : replaced each checkpoint; rendered under "Git Diff Summary"

    content         TEXT NOT NULL,
    created_at      INTEGER NOT NULL
    -- Unix timestamp of when this chunk was written.
);

CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    session_id    UNINDEXED,
    goal,
    todos,
    file_paths,
    decisions,
    work_summary,
    tokenize = 'porter unicode61'
);
