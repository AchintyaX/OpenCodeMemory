# openCodeMemory

**Persistent session memory for AI coding assistants. Local. Private. Zero cloud.**

AI coding assistants lose all context when a session ends — what you were building, decisions made, files changed. openCodeMemory solves this by capturing session state as structured markdown files, indexing them with SQLite FTS5 for fast search, and exposing them via MCP so your assistant can pick up exactly where it left off.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    IDE (Claude Code / Cursor)                  │
│                                                                 │
│  ┌──────────────┐  MCP         ┌─────────────────────────────┐  │
│  │  AI Model    │──────────▶   │  ocm.server (FastMCP)       │  │
│  │ (checkpoint  │  (stdio)     │  ocm__checkpoint            │  │
│  │  / search)   │◀──────────   │  ocm__search_sessions       │  │
│  └──────────────┘   result     │  ocm__list_sessions         │  │
│                                │  ocm__get_session_files     │  │
│                                └────────────┬────────────────┘  │
└─────────────────────────────────────────────┼───────────────────┘
                                              │
            ┌─────────────────────────────────▼──────────────────┐
            │          .openCodeMemory/ (per-project)           │
            │                                                    │
            │  sessions/                                         │
            │  └── 2026-03-03_14-32_claude-code_auth-fix.md     │
            │      ◀── canonical source                          │
            │                                                    │
            │  memory.db (SQLite)                                │
            │  ├── sessions       (metadata)                     │
            │  ├── session_chunks (goal, decisions, work…)       │
            │  ├── session_files  (touched files list)           │
            │  └── sessions_fts   (FTS5 BM25 search index)       │
            │                                                    │
            │  active_<session_id>.jsonl (file-edit journal)     │
            └────────────────────────────────────────────────────┘
```

**Hook usage** — Hooks are profile-driven. Claude defaults to a minimal hook profile; Cursor defaults to no hooks unless enabled. Both assistants can use a shared `postToolUse` semantic-checkpoint policy (threshold: 5 tool calls).

**Active MCP tool flow** — the AI assistant calls `ocm__checkpoint` to persist a snapshot and `ocm__search_sessions` to retrieve past context. Search returns a `markdown_path`; the assistant reads the file directly.

---

## Prerequisites

- Python 3.11+
- `[uv](https://docs.astral.sh/uv/)` (recommended) or `pip`
- Claude Code CLI (`claude`) and/or Cursor installed

---

## Installation

### From PyPI (recommended)

```bash
pip install ocm-session-memory
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install ocm-session-memory
```

After installing, `ocm` and `ocm-hook` are available directly on your PATH.

### From source

```bash
git clone https://github.com/AchintyaX/OpenCodeMemory
cd OpenCodeMemory
uv sync
uv run ocm --help
```

### Quick start

```bash
pip install ocm-session-memory
ocm install                    # one-time global config — works in every project
# Open Claude Code or Cursor and ask it to "save a checkpoint"
```

---

## Safety & merge behavior

openCodeMemory writes into your existing IDE configuration. It is designed to be safe to run against a setup you already use:

- **Idempotent** — rule injections are wrapped in `<!-- BEGIN openCodeMemory -->` / `<!-- END openCodeMemory -->` sentinel markers. Re-running `ocm init` finds and replaces the existing block; it never duplicates content. Hook entries are only appended when the exact command isn't already present.
- **Atomic writes** — every `settings.json`, `CLAUDE.md`, and `.cursorrules` mutation goes through a temp-file + `os.replace()`, so an interrupted install never leaves a truncated config.
- **Fail-loud on bad JSON** — if `.claude/settings.json` (or any other JSON file we touch) is malformed when we try to read it, `ocm install` aborts with a clear error message rather than silently rewriting the file. Your existing config is never discarded on a parse error.
- **Versioned `.mdc` rule** — `.cursor/rules/ocm-checkpoint.mdc` ships with an `ocm-version:` frontmatter field. Future upgrades refresh it only when the version changes; user-modified copies with a different (or missing) version are left untouched.

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
5. For each assistant: registers MCP, configures selected hook profile, and injects usage rules
6. For Cursor projects: writes `.cursor/rules/ocm-checkpoint.mdc`
7. Registers the project in `~/.openCodeMemory/registry.json`

---

## Claude Code Setup

`ocm init` handles this automatically. By default, Claude uses `--claude-hooks minimal`.

**MCP server** registered at project scope:

```bash
claude mcp add --scope project opencodememory -- uv run python -m ocm.server
```

> If you installed via `pip install ocm-session-memory` without `uv`, replace `uv run python` with `python`.

**Hooks** written to `.claude/settings.json` (profile-dependent):


| Event | Purpose |
| --- | --- |
| `SessionStart` | Initializes session row via `ocm-hook session-start` |
| `UserPromptSubmit` | Injects `CLAUDE_SESSION_ID` reminder context |
| `PreToolUse` | Blocks tool calls when semantic checkpoint is stale |
| `PostToolUse` | Increments checkpoint counter and writes machine checkpoint |
| `Stop` | Marks session closed via `ocm-hook session-end` |


Notes:

- `--claude-hooks full` additionally records file edits with `ocm-hook file-edited`.
- Semantic checkpoint enforcement is hybrid: reminder first, then gate via `PreToolUse` after threshold is exceeded.

**Rules** are appended to `CLAUDE.md` inside `<!-- BEGIN openCodeMemory -->` / `<!-- END openCodeMemory -->` markers.

---

## Cursor Setup

`ocm init` handles this automatically. Cursor hooks are opt-in (`--cursor-hooks minimal|full`).

**MCP server** registered in `.cursor/mcp.json`.

**Hooks** are profile-based:
- `none` (default): no Cursor hook setup
- `minimal`: `sessionStart`, `preToolUse`, `postToolUse`, `stop`
- `full`: `minimal` plus `afterFileEdit` journal tracking

**Rules**:

- Appends openCodeMemory guidance to `.cursorrules` (inside sentinel markers)
- Writes `.cursor/rules/ocm-checkpoint.mdc` for always-on checkpoint instructions in Cursor

---

## Usage

### CLI Commands

```bash
ocm install                 # One-time global setup for detected assistants
ocm init                    # Optional project-local setup + project rules
ocm help                    # List all ocm commands with descriptions
ocm checkpoint              # Write/update session checkpoint from CLI
ocm list                    # List recent sessions (default: last 10)
ocm list --limit 20         # Last 20 sessions
ocm list --tool claude-code # Filter by tool
ocm search "Redis caching"  # BM25 natural language search
ocm show <session_id>       # Print full session markdown
ocm export <session_id>     # Copy markdown path to clipboard
ocm rebuild-index           # Rebuild FTS5 from session_chunks
ocm uninstall               # Remove OCM hooks/rules from project config (storage preserved)
ocm uninstall --global      # Remove OCM global hooks/rules (storage preserved)
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

5. **Semantic checkpoint policy (when hook profile enabled)**
  - Counter increments at `postToolUse`
  - At 5 tool calls, hook injects reminder context
  - If still stale, `preToolUse` denies further tools until semantic checkpoint is written

### Resuming a Previous Session

The AI assistant automatically searches for related sessions when starting a new task. To trigger manually:

> "Search openCodeMemory for the Redis caching work from last week"

---

## Removing openCodeMemory

`ocm uninstall` removes openCodeMemory's hooks and rules from your IDE config without touching your session data.

**What is removed:**
- Hook entries from `.claude/settings.json` / `~/.claude/settings.json` (only entries whose command contains `ocm-hook` — other hooks are untouched)
- The `<!-- BEGIN openCodeMemory -->` ... `<!-- END openCodeMemory -->` block from `CLAUDE.md` and `.cursorrules`
- The `opencodememory` key from `.cursor/mcp.json` / `~/.cursor/mcp.json`
- `.cursor/rules/ocm-checkpoint.mdc` — only when its `ocm-version` matches the installed version; user-modified copies are left in place

**What is NOT removed:**
- `.openCodeMemory/` (sessions, DB) — delete manually if desired
- `~/.openCodeMemory/` (global sessions, DB, registry) — delete manually if desired

```bash
ocm uninstall               # project-local config (run from inside the project)
ocm uninstall --global      # global (~/.claude, ~/.cursor) config
```

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

```
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
