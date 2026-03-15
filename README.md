# openCodeMemory

**Persistent session memory for AI coding assistants. Local. Private. Zero cloud.**

AI coding assistants lose all context when a session ends — what you were building, decisions made, files changed. openCodeMemory solves this by capturing session state as structured markdown files, indexing them with SQLite FTS5 for fast search, and exposing them via MCP so your assistant can pick up exactly where it left off.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    IDE (Claude Code / Cursor)                     │
│                                                                   │
│  ┌──────────────┐  hooks     ┌───────────────────────────────┐   │
│  │  Session /   │──────────▶│  ocm-hook CLI                 │   │
│  │  File edits  │  (stdio)   │  session-start                │   │
│  └──────────────┘            │  file-edited                  │   │
│                               │  session-end                  │   │
│  ┌──────────────┐  MCP        └──────────────┬────────────────┘   │
│  │  AI Model    │──────────▶                 │                    │
│  │  (checkpoint │  (stdio)   ┌───────────────▼────────────────┐   │
│  │   / search)  │◀──────────│  ocm.server (FastMCP)          │   │
│  └──────────────┘  result   │  ocm__checkpoint                │   │
│                               │  ocm__search_sessions          │   │
│                               │  ocm__list_sessions            │   │
│                               │  ocm__get_session_files        │   │
│                               └──────────────┬────────────────┘   │
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

**Passive hook flow** — fires automatically on IDE events (session start, file edits, session end), journaling state without interrupting your workflow.

**Active MCP tool flow** — the AI assistant calls `ocm__checkpoint` to persist a snapshot and `ocm__search_sessions` to retrieve past context. Search returns a `markdown_path`; the assistant reads the file directly.

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- Claude Code CLI (`claude`) and/or Cursor installed

---

## Installation

```bash
# Clone the repo
git clone https://github.com/<org>/openCodeMemory
cd openCodeMemory

# Install (editable, with uv)
uv sync

# Or with pip
pip install -e .
```

After install, `ocm` and `ocm-hook` are available as CLI commands.

---

## Global Installation (Recommended)

Run once, works in every project. All sessions stored in `~/.openCodeMemory/memory.db`.

```bash
ocm install
```

This registers the MCP server and hooks globally:
- **Claude Code**: MCP at `--scope user`, hooks in `~/.claude/settings.json`, rules in `~/.claude/CLAUDE.md`
- **Cursor**: MCP in `~/.cursor/mcp.json`, hooks in `~/.cursor/hooks.json`

After running `ocm install`, open any project in your IDE — no further setup needed.

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
5. For each assistant: registers the MCP server, installs hooks, injects usage rules
6. Registers the project in `~/.openCodeMemory/registry.json`

---

## Claude Code Setup

`ocm init` handles this automatically. Here's what it configures:

**MCP server** registered at project scope:

```bash
claude mcp add --scope project opencodememory -- uv run python -m ocm.server
```

**Hooks** written to `.claude/settings.json`:

| Event | Hook command | Purpose |
|-------|-------------|---------|
| `UserPromptSubmit` | `ocm-hook session-start --tool claude-code` | Create session row on conversation start |
| `PostToolUse` (Write/Edit/MultiEdit) | `ocm-hook file-edited --tool claude-code` | Log touched files to journal |
| `Stop` | `ocm-hook session-end --tool claude-code` | Mark session closed on conversation end |

**Rules** appended to `CLAUDE.md` instructing Claude when and how to call `ocm__checkpoint`.

---

## Cursor Setup

`ocm init` handles this automatically. Here's what it configures:

**MCP server** registered in `.cursor/mcp.json`.

**Hooks** written to `.cursor/hooks.json`:

| Event | Hook command | Purpose |
|-------|-------------|---------|
| `beforeSubmitPrompt` | `ocm-hook session-start --tool cursor` | Create session on prompt |
| `afterFileEdit` | `ocm-hook file-edited --tool cursor` | Log touched files |
| `stop` | `ocm-hook session-end --tool cursor` | Close session |

**Rules** appended to `.cursorrules`.

---

## Usage

### CLI Commands

```bash
ocm install                 # One-time global setup (MCP + hooks for all projects)
ocm list                    # List recent sessions (default: last 10)
ocm list --limit 20         # Last 20 sessions
ocm list --tool claude-code # Filter by tool
ocm search "Redis caching"  # BM25 natural language search
ocm show <session_id>       # Print full session markdown
ocm export <session_id>     # Copy markdown path to clipboard
ocm rebuild-index           # Rebuild FTS5 from session_chunks
```

### MCP Tools (called automatically by the AI assistant)

| Tool | When it's called |
|------|-----------------|
| `ocm__checkpoint` | At ~70% context, before git commit, at task end |
| `ocm__search_sessions` | When user asks to continue previous work |
| `ocm__list_sessions` | To browse recent sessions |
| `ocm__get_session_files` | To get a session's file list without full markdown |

### Session Lifecycle

1. IDE fires `session-start` hook → session row created, initial `.md` file written
2. Each file edit → path logged to `.jsonl` journal
3. AI calls `ocm__checkpoint` → chunks written to DB, journal flushed, markdown re-rendered, FTS rebuilt
4. IDE fires `session-end` hook → session marked `closed`

### Resuming a Previous Session

The AI assistant automatically searches for related sessions when starting a new task. To trigger manually:

> "Search openCodeMemory for the Redis caching work from last week"

---

## Data Storage

All data is local to each project:

```
<project>/.openCodeMemory/
├── memory.db          # SQLite: metadata + FTS5 index (rebuild-only cache)
├── sessions/*.md      # Markdown files — canonical source of truth
└── active_<id>.jsonl  # Temporary file-edit journals (flushed on checkpoint)

~/.openCodeMemory/
└── registry.json      # Paths to all known project DBs (for cross-project search)
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
