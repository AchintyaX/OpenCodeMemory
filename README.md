# openCodeMemory

**Persistent session memory for AI coding assistants. Local. Private. Zero cloud.**

AI coding assistants lose all context when a session ends — what you were building, decisions made, files changed. openCodeMemory solves this by capturing session state as structured markdown files, indexing them with SQLite FTS5 for fast search, and exposing them via MCP so your assistant can pick up exactly where it left off.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    IDE (Claude Code / Cursor)                     │
│                                                                   │
│  ┌──────────────┐  MCP         ┌───────────────────────────────┐   │
│  │  AI Model    │──────────▶   │  ocm.server (FastMCP)         │   │
│  │  (checkpoint │  (stdio)     │  ocm__checkpoint              │   │
│  │   / search)  │◀──────────   │  ocm__search_sessions         │   │
│  └──────────────┘  result      │  ocm__list_sessions           │   │
│                                 │  ocm__get_session_files       │   │
│                                 └──────────────┬────────────────┘   │
└───────────────────────────────────────────────┼────────────────────┘
                                                │
              ┌─────────────────────────────────▼──────────────────┐
              │          .openCodeMemory/  (per-project)            │
              │                                                      │
              │  sessions/                                           │
              │  └── 2026-03-03_14-32_claude-code_auth-fix.md  ◀── canonical source
              │                                                      │
              │  memory.db  (SQLite)                                 │
              │  ├── sessions       (metadata)                       │
              │  ├── session_chunks (goal, decisions, work…)         │
              │  ├── session_files  (touched files list)             │
              │  └── sessions_fts   (FTS5 BM25 search index)         │
              │                                                      │
              │  active_<session_id>.jsonl  (file-edit journal)      │
              └──────────────────────────────────────────────────────┘
```

**Hook usage** — Cursor hook integration is intentionally disabled. Claude uses a prompt-submit hook to inject session context; touched files are derived at checkpoint time (journal if present, otherwise git fallback).

**Active MCP tool flow** — the AI assistant calls `ocm__checkpoint` to persist a snapshot and `ocm__search_sessions` to retrieve past context. Search returns a `markdown_path`; the assistant reads the file directly.

---

## Prerequisites

- Python 3.11+
- `[uv](https://docs.astral.sh/uv/)` (recommended) or `pip`
- Claude Code CLI (`claude`) and/or Cursor installed

---

## Installation

### 1) Install the package

```bash
# Clone the repo
git clone https://github.com/<org>/openCodeMemory
cd openCodeMemory

# Install dependencies with uv
uv sync

# Run CLI commands through uv
uv run ocm --help

# Or install editable with pip
pip install -e .
```

If you use `pip install -e .`, `ocm` and `ocm-hook` are available directly.
If you use `uv sync`, run commands as `uv run ocm ...` unless your venv is activated.

---

## Global Installation (Recommended)

Run once, works in every project. This creates the default global store at `~/.openCodeMemory/memory.db` (project-local DBs created by `ocm init` still take precedence).

```bash
ocm install
# or: uv run ocm install
```

This creates global storage and configures detected assistants:

- **Storage**: `~/.openCodeMemory/memory.db` + `~/.openCodeMemory/sessions/`
- **Claude Code**:
  - MCP at user scope (`claude mcp add --scope user ...`)
  - Hook entry in `~/.claude/settings.json` (`UserPromptSubmit`)
  - Rules injected into `~/.claude/CLAUDE.md`
- **Cursor**:
  - MCP in `~/.cursor/mcp.json`
  - Global rules injection is not supported (add rules per project)

After `ocm install`, you can use openCodeMemory immediately across projects.
If you use Cursor and want persistent assistant guidance, run `ocm init` in a project (or add rules manually there).

**DB fallback priority**: per-project `.openCodeMemory/memory.db` (if present from `ocm init`) → global `~/.openCodeMemory/memory.db`.

---

## Project Setup — Optional

> Use `ocm init` if you want an isolated per-project DB instead of the global one.

Navigate to any project directory and run:

```bash
cd /path/to/your-project
ocm init
```

`ocm init` does all of the following automatically:

1. Detects the project root (walks up to `.git`)
2. Creates `.openCodeMemory/memory.db` and `sessions/` directory
3. Adds `.openCodeMemory/` to `.gitignore`
4. Detects installed assistants (Claude Code, Cursor)
5. For each assistant: registers MCP and injects usage rules
6. For Cursor projects: writes `.cursor/rules/ocm-checkpoint.mdc`
7. Registers the project in `~/.openCodeMemory/registry.json`

---

## Claude Code Setup

`ocm init` handles this automatically. Here's what it configures:

**MCP server** registered at project scope:

```bash
claude mcp add --scope project opencodememory -- uv run python -m ocm.server
```

**Hooks** written to `.claude/settings.json`:


| Event              | Hook command                                                           | Purpose                                                                     |
| ------------------ | ---------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `UserPromptSubmit` | command hook that injects `additionalContext` with `CLAUDE_SESSION_ID` | Ensures the assistant receives session id and calls `ocm__checkpoint` first |


Notes:

- Claude setup currently does not install `PostToolUse`/`Stop` hooks via `ocm init`.
- File tracking for Claude falls back to git diff at checkpoint time when no file-edit journal exists.
- Session closure is typically done by calling `ocm__checkpoint` with `status="closed"` per injected rules.

**Rules** are appended to `CLAUDE.md` to enforce checkpoint lifecycle.

---

## Cursor Setup

`ocm init` handles this automatically. Here's what it configures:

**MCP server** registered in `.cursor/mcp.json`.

**Hooks** are not configured for Cursor.

Session metadata and file tracking are captured on `ocm__checkpoint` (with git fallback when no journal data exists).


**Rules**:

- Appends openCodeMemory guidance to `.cursorrules`
- Writes `.cursor/rules/ocm-checkpoint.mdc` for always-on checkpoint instructions in Cursor

---

## Usage

### CLI Commands

```bash
ocm install                 # One-time global setup for detected assistants
ocm init                    # Optional project-local setup + project rules
ocm list                    # List recent sessions (default: last 10)
ocm list --limit 20         # Last 20 sessions
ocm list --tool claude-code # Filter by tool
ocm search "Redis caching"  # BM25 natural language search
ocm show <session_id>       # Print full session markdown
ocm export <session_id>     # Copy markdown path to clipboard
ocm rebuild-index           # Rebuild FTS5 from session_chunks
```

### MCP Tools (called automatically by the AI assistant)


| Tool                     | When it's called                                   |
| ------------------------ | -------------------------------------------------- |
| `ocm__checkpoint`        | At ~70% context, before git commit, at task end    |
| `ocm__search_sessions`   | When user asks to continue previous work           |
| `ocm__list_sessions`     | To browse recent sessions                          |
| `ocm__get_session_files` | To get a session's file list without full markdown |


### Session Lifecycle

1. **Session identity established**
  - Claude: `UserPromptSubmit` injects `CLAUDE_SESSION_ID` into model context
  - Claude/Cursor: first `ocm__checkpoint` call can auto-create the session row
2. **File edits tracked**
  - At checkpoint time, journal entries are flushed when present
  - If no journal entries exist, checkpoint falls back to git diff from `git_sha_start`
3. **Checkpoint persisted**
  - `ocm__checkpoint` writes chunks, flushes journal when present, re-renders markdown, and rebuilds FTS
4. **Session closed**
  - Claude/Cursor: assistant can explicitly close with `ocm__checkpoint(..., status="closed")`

### Resuming a Previous Session

The AI assistant automatically searches for related sessions when starting a new task. To trigger manually:

> "Search openCodeMemory for the Redis caching work from last week"

---

## Data Storage

Storage location depends on setup mode:

- **Project mode (`ocm init`)**: data is local to the project
- **Global mode (`ocm install`)**: data is shared in `~/.openCodeMemory/`

Project mode:

```
<project>/.openCodeMemory/
├── memory.db          # SQLite: metadata + FTS5 index (rebuild-only cache)
├── sessions/*.md      # Markdown files — canonical source of truth
└── active_<id>.jsonl  # Temporary file-edit journals (flushed on checkpoint)
```

Global mode:

~/.openCodeMemory/
├── memory.db          # Global SQLite DB used when project DB is absent
├── sessions/*.md      # Global markdown sessions
└── registry.json      # Paths to project DBs (used by global search)

```

**Markdown is the source of truth.** If `memory.db` is deleted or corrupted, run `ocm rebuild-index` to restore the search index from the markdown files.

---

## Contributing

- Bug reports and feature requests via GitHub Issues
- PRs welcome: fork → branch → test → PR
- Run `uv run pytest` before submitting
- **No LLM calls** from within the tool — all intelligence comes from the calling assistant
- **No external network calls** — 100% local, all data stays in `.openCodeMemory/`
- **No ORM** — use stdlib `sqlite3` directly
- Follow existing code style; discuss new dependencies in an issue before adding them
```

