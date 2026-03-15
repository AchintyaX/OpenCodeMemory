# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**openCodeMemory** is a local, privacy-first MCP server that provides persistent session memory for AI coding assistants. It captures session state (goal, work done, files changed, decisions) as structured markdown files, indexes them in SQLite for fast BM25 search, and exposes 4 MCP tools for assistants to read/write sessions.

The full design is in `techspec.md`. Read it before implementing anything.

## Development Commands

The project uses Python 3.11+ with `pyproject.toml`. Once set up:

```bash
# Install all deps including dev group
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_checkpoint.py

# Run a single test
uv run pytest tests/test_checkpoint.py::test_name

# Run the MCP server locally (stdio transport)
ocm serve

# CLI entry points
ocm init              # one-time project setup
ocm list              # list recent sessions
ocm search "<query>"  # search sessions
ocm show <session_id> # print session markdown
ocm rebuild-index     # rebuild FTS5 from chunks
```

## Architecture

### Storage Hierarchy

```
<project>/.openCodeMemory/
├── memory.db              # SQLite: metadata + FTS5 index (rebuild-only cache)
├── sessions/*.md          # Markdown files — the canonical source of truth
└── active_<id>.jsonl      # Temporary file-edit journals (flushed on checkpoint)
~/.openCodeMemory/
└── registry.json          # Paths to all known project DBs (for cross-project search)
```

**Markdown is the source of truth.** The DB exists only for fast search. If deleted, it can be rebuilt from markdown files.

### Package Layout

```
ocm/
├── server.py              # FastMCP server entry point; wires all tools
├── tools/
│   ├── checkpoint.py      # ocm__checkpoint: write/update sessions
│   ├── search.py          # ocm__search_sessions: BM25 search
│   └── session.py         # ocm__list_sessions + ocm__get_session_files
├── storage/
│   ├── db.py              # Database class, schema migrations
│   ├── schema.sql         # Full SQL schema
│   └── markdown_renderer.py  # Renders markdown from session_chunks table
├── hooks/
│   ├── handler.py         # ocm-hook CLI dispatcher
│   ├── file_tracker.py    # .jsonl journal file management
│   └── git.py             # git SHA and diff operations
└── search/
    ├── fts.py             # BM25 FTS5 query pipeline + enrichment
    └── preprocessor.py    # Extracts date/tool/path filters from natural language
```

### MCP Tools

| Tool | Direction | Purpose |
|------|-----------|---------|
| `ocm__checkpoint` | Write | Save/update session state; triggers markdown re-render and FTS rebuild |
| `ocm__search_sessions` | Read | BM25 search, returns `markdown_path` (not content) |
| `ocm__list_sessions` | Read | Recent sessions by date |
| `ocm__get_session_files` | Read | File change list for a session |

**Search returns `markdown_path`, not file content.** The calling assistant reads the file with its own tools.

### Session Lifecycle

1. **Hook fires** on session start → creates DB row with `git_sha_start`, blank markdown file
2. **File edits** accumulate in `.jsonl` journal (not written to DB on every edit)
3. **`ocm__checkpoint` called** → writes `session_chunks` rows, flushes journal to `session_files`, re-renders markdown, rebuilds FTS5 row
4. **Session end hook** → sets status `closed`, records `git_sha_end`

### `session_chunks` Table

The core write model. Chunks have a `chunk_type`:
- `work_completed`, `work_summary`, `decision`, `plan_file`, `reference`, `diff_summary` — **appended** on each checkpoint
- `work_pending` — **replaced** on each checkpoint (reflects current state)

The markdown renderer produces the full `.md` from all chunks on every checkpoint.

### FTS5 Search

Column weights in the BM25 index: `goal` (10) > `decisions` (8) > `todos`/`file_paths` (5) > `work_summary` (3). Query preprocessor extracts date ranges, tool names, and file path fragments as structured filters applied after BM25 ranking.

### Hook System

Hooks are thin shell wrappers calling `ocm-hook <event> <session_id> [args]`. They fire on:
- **Session start**: `UserPromptSubmit` (Claude Code) / `beforeSubmitPrompt` (Cursor)
- **File edit**: `PostToolUse` on Write/Edit/MultiEdit (Claude Code) / `afterFileEdit` (Cursor)
- **Session end**: `Stop` (Claude Code) / `stop` (Cursor)

## Build Order

Follow this sequence from `techspec.md §14`:

1. Storage layer (`schema.sql`, `db.py`)
2. Markdown renderer (`markdown_renderer.py`)
3. Checkpoint tool (`checkpoint.py`)
4. Hook handler (git ops + file tracker + `handler.py`)
5. Search (`preprocessor.py` + `fts.py` + `search.py` tool wrapper)
6. Session tools (`session.py`)
7. MCP server (`server.py`)
8. Install CLI (`install/`)
9. Hook shell scripts + rule injection snippets

## Key Design Constraints

- **No LLM calls** from within openCodeMemory — all intelligence comes from the calling assistant
- **No external network calls** — 100% local, all data in `.openCodeMemory/`
- **No ORM** — use stdlib `sqlite3` directly
- **Dependencies**: `mcp[cli]`, `gitpython`, `click`, `python-dateutil` — nothing else
- Session markdown format must match spec exactly (used by rule injection to teach assistants what to write)
