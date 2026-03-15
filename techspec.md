# openCodeMemory — Technical Specification

**Version:** 0.1  
**Language:** Python 3.11+  
**Status:** Pre-implementation  
**Authors:** Planning session, March 2026

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Design Principles](#3-design-principles)
4. [System Architecture](#4-system-architecture)
5. [Session Markdown Format](#5-session-markdown-format)
6. [Storage Design](#6-storage-design)
7. [MCP Server and Tools](#7-mcp-server-and-tools)
8. [Search System](#8-search-system)
9. [Hook System](#9-hook-system)
10. [Rule Injection](#10-rule-injection)
11. [Installation CLI](#11-installation-cli)
12. [Project Structure](#12-project-structure)
13. [Dependencies](#13-dependencies)
14. [Build Order](#14-build-order)
15. [Deferred to v2](#15-deferred-to-v2)

---

## 1. Overview

openCodeMemory is a local tool that solves a specific problem in AI-assisted development: **coding sessions are stateless, but software projects are not**. When you hit a daily usage limit in Cursor, switch from Claude Code to Antigravity mid-feature, or simply start a new conversation, the AI assistant loses all context about what has been done, which files were changed, what decisions were made, and what still needs doing. You have to re-explain everything from scratch.

openCodeMemory acts as a persistent memory layer that sits between sessions. It captures session state as structured markdown files, indexes them locally for fast retrieval, and exposes that context back to any AI coding assistant through the Model Context Protocol (MCP). The AI reads the previous session's markdown file and continues work without the developer having to reconstruct context manually.

The tool is entirely local. No data leaves the machine. No external services are called. No LLM calls are made by the tool itself — all intelligence (writing the session summary, deciding what is important, reading the context) comes from the coding assistant's own model.

---

## 2. Problem Statement

### The context loss problem

Modern AI coding assistants maintain context only within a single conversation session. When a session ends — due to context limit, daily cap, tool switch, or simple conversation closure — the next session begins blank. The developer must either:

- Re-paste relevant code and explain the situation manually
- Rely on the assistant re-reading the codebase from scratch (expensive, imprecise)
- Keep running notes separately (manual, easily forgotten)

This is particularly painful for multi-day features, exploratory work where multiple approaches were tried, or workflows that span multiple tools (e.g., planning in Claude Code, implementing in Cursor).

### The cross-tool problem

Different tools have different strengths. Claude Code is excellent at terminal-based agentic work. Cursor is well-integrated into the VS Code editing workflow. Antigravity offers multi-agent orchestration. Developers switch between them, but there is no shared context store. Each tool has its own conversation history, none of it accessible to the others.

### What openCodeMemory solves

- **Context preservation:** Automatically saves structured session snapshots when sessions end, hit context limits, or when commits are made.
- **Fast retrieval:** Natural language search over past sessions returns the right markdown file path in seconds.
- **Cross-tool portability:** Any tool that supports MCP can call the search tools and read the resulting markdown file. The format is plain text; any assistant can read it.
- **Privacy:** All data sits in a `.openCodeMemory/` directory inside the project, on the developer's own machine.

---

## 3. Design Principles

**The tool stores, the LLM summarises.** openCodeMemory never calls an LLM. It collects raw signals from hooks (which files were touched, when the session started, the git SHA) and exposes MCP tools for the LLM to call. The LLM writes the goal, the decisions, the work summary. The tool renders that content into a markdown file and indexes it.

**Markdown is the source of truth.** The SQLite database exists to make search fast. The markdown file is the canonical document. If the database were deleted, the markdown files would still be readable by any editor or assistant. The database is rebuilt from the markdown files if needed.

**The agent gets a file path, not the content.** Search results return `markdown_path`. The agent reads the file using its own file-reading tools. This keeps the MCP server thin, avoids inflating the MCP response with large content, and gives the agent full control over how much context it loads.

**Writes are incremental, never destructive.** The session record is built up over time via appended chunks. Each `ocm__checkpoint` call adds new content or updates mutable fields (like `work_pending`). The markdown file is re-rendered from all accumulated chunks on each checkpoint. No prior content is lost.

**Conciseness is enforced at the prompt layer.** The rule injected into `CLAUDE.md` and `.cursorrules` instructs the LLM to write bullets, not prose; file paths, not file contents; first headings, not full plan file contents; short titles with URLs, not paragraph descriptions. The goal is a session file that gives maximum orientation with minimum token cost.

---

## 4. System Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Coding Assistant                        │
│           (Claude Code / Cursor / Antigravity)              │
│                                                             │
│  ┌──────────────┐      ┌──────────────────────────────┐    │
│  │  LLM Model   │─────▶│  MCP Client (built into IDE) │    │
│  └──────────────┘      └────────────┬─────────────────┘    │
│                                     │ stdio / HTTP          │
└─────────────────────────────────────┼─────────────────────-┘
                                      │
                          ┌───────────▼───────────┐
                          │   openCodeMemory MCP   │
                          │       Server           │
                          │   (ocm/server.py)      │
                          └───────────┬───────────┘
                                      │
                    ┌─────────────────┼──────────────────┐
                    │                 │                   │
          ┌─────────▼──────┐ ┌───────▼──────┐ ┌────────▼───────┐
          │  session_chunks │ │  sessions    │ │  sessions_fts  │
          │  session_files  │ │  (metadata)  │ │  (FTS5 index)  │
          │  (SQLite tables)│ └──────────────┘ └────────────────┘
          └─────────────────┘
                    │
          ┌─────────▼──────────┐
          │  .openCodeMemory/  │
          │  sessions/*.md     │ ◀── Markdown files (source of truth)
          └────────────────────┘

  ┌─────────────────────────────────────────────┐
  │              Hook Layer                      │
  │  (shell scripts called by IDE lifecycle)    │
  │                                             │
  │  Claude Code hooks  │  Cursor hooks.json    │
  │  (settings.json)    │  (afterFileEdit, stop)│
  └─────────────────────────────────────────────┘
```

### Data flow: writing a session

1. A hook fires on session start. The hook script calls `ocm-hook session-start <id> <project_dir>`. The Python hook handler creates a session row in SQLite with status `open`, records the current git SHA as `git_sha_start`, and creates a blank markdown file at the appropriate path.

2. As the agent edits files, `afterFileEdit` / `PostToolUse` hooks fire. Each call to `ocm-hook file-edited <session_id> <file_path>` appends to an in-memory set held by the MCP server process for that session. These do not write to the database on every edit — that would be too noisy.

3. When a checkpoint trigger fires (70% context, manual command, git commit, or session end), the LLM calls `ocm__checkpoint` with structured data it has synthesised: the goal, lists of completed and pending work, architecture decisions, plan file references, and external links. The MCP server:
   - Writes these as `session_chunks` rows in SQLite
   - Flushes the in-memory file list to `session_files` rows
   - Calls `markdown_renderer.py` to re-render the entire markdown file from all accumulated chunks
   - Rebuilds the FTS5 index row for this session

4. On session end, the session status is set to `closed` and `git_sha_end` is recorded. A final checkpoint is triggered if no manual checkpoint was called during the session.

### Data flow: loading a session

1. The developer starts a new session and asks the assistant to continue previous work.
2. The LLM calls `ocm__search_sessions("tei migration embedding latency")`.
3. The MCP server runs the BM25 search pipeline against the FTS5 index, applies any detected filters (date, tool, file path hints), and returns up to 5 results above the relevance threshold. Each result includes `markdown_path` but not the file content.
4. The assistant presents the results to the developer, who confirms which session to continue.
5. The assistant reads the markdown file at the confirmed `markdown_path` using its own file-reading capability. No further MCP calls are needed.

---

## 5. Session Markdown Format

### Design rationale

The markdown file is designed for a specific reader: an AI coding assistant starting a new session. It needs to answer: what were we building, what is done, what is left, and which files should I look at first. Every field is present for a reason, and the format is kept concise deliberately — the LLM injecting context from a previous session has limited tokens to spare.

The format is rendered by `markdown_renderer.py` from the `session_chunks` table. The LLM never writes raw markdown — it calls `ocm__checkpoint` with structured fields, and the renderer produces the file. This ensures the format is always consistent regardless of which assistant wrote the session.

### File naming

```
.openCodeMemory/sessions/<YYYY-MM-DD_HH-MM>_<tool>_<slug>.md
```

- `YYYY-MM-DD_HH-MM` is the session start time in local time, formatted for alphabetical sort order.
- `<tool>` is one of `cursor`, `claude-code`, or `antigravity`.
- `<slug>` is a short descriptor derived from the goal, maximum 4 words hyphenated (e.g., `tei-migration`, `kv-cache-prefill`). The LLM provides this as part of the first `ocm__checkpoint` call.

### Complete format with field explanations

```markdown
---
session_id: 2026-02-23_14-32_cursor_tei-migration
tool: cursor
project: sprinklr-inference
started_at: 2026-02-23T12:10:00
git_sha_start: a3f1c99
git_sha_end: d8e2f04
trigger: context_limit_70pct
---

## Goal
Migrate BGE-M3 embedding endpoint from vLLM to TEI for ~40% p50 latency reduction.
```

The `Goal` is one or two sentences. It should be specific enough to distinguish this session from others on the same project. The LLM writes this on the first checkpoint.

```markdown
---

## Todos

### ✅ Work Completed
- Replaced vLLM serving logic with TEI HTTP client in embedding_server.py
- Created k8s deployment manifest for TEI on A10G nodes
- Wrote latency benchmark test harness (p50/p95/p99)

### 🔲 Work To Be Completed
- Tune TEI batch size (current: 32, target: 64+)
- Resolve intermittent p99 OOM under sustained load
- Migrate fallback vLLM path to TEI
```

`Work Completed` items are appended on each checkpoint and never removed. `Work To Be Completed` is replaced on each checkpoint — it reflects the current state of remaining work, not a cumulative list. This means if a pending item gets done, it moves to Completed on the next checkpoint and disappears from Pending.

```markdown
---

## Files Touched

### Created
- `k8s/deployments/tei-deployment.yaml`
- `tests/test_embedding_latency.py`

### Modified
- `src/inference/embedding_server.py`
- `src/inference/client.py`
- `config/model_registry.yaml`
```

File paths are relative to the project root. They are populated from the `session_files` table, which is maintained by the file-edit hooks. The renderer separates files by `change_type`: `created`, `modified`, `deleted`.

```markdown
---

## Git Diff Summary
3 files modified, 2 files created. +187 / -43 lines.
Key changes: embedding_server.py replaces vLLM engine init with TEI HTTP call;
tei-deployment.yaml sets resource limits to 4CPU/16Gi on A10G nodepool.
```

The diff summary is the output of `git diff --stat <git_sha_start>` plus a short prose note written by the LLM. Not the full diff — just the stat and a one-sentence key change note. The full diff is always available to the agent via `git diff <git_sha_start>` if needed.

```markdown
---

## Work Done

- Profiled existing vLLM embedding path — synchronous batching identified as bottleneck
- TEI benchmarked at 38ms vs Infinity 61ms p50 on BGE-M3 — TEI selected
- Integrated TEI client with retry + timeout logic in embedding_server.py
- k8s manifest tuned for A10G; liveness probe on /health endpoint added
- p50/p95 latency tests passing; p99 spikes above SLA under 100 RPS
```

`Work Done` is a running log of what was accomplished, written as concise bullets. New bullets are appended on each checkpoint. This is the section with the most detail and the section most likely to be skimmed — the LLM loading context will read the Goal and Todos first, then check Work Done only if it needs to understand *why* certain decisions were made.

```markdown
---

## Plan Files

| File | Description |
|------|-------------|
| `docs/plans/tei-migration-plan.md` | ## TEI Migration Plan — Phase 1 |
| `docs/plans/embedding-benchmark.md` | ## Embedding Server Benchmark Methodology |
```

Plan files are documents created during the session — architecture docs, PRDs, migration plans, benchmark methodology files. The LLM records the file path and the first heading of the document. The assistant loading context can read these files if needed. Recording the heading allows the loading assistant to decide whether to read the file without having to open it first.

```markdown
---

## Architecture Decisions

- **TEI over Infinity:** Native BGE-M3 support; Infinity required patching.
  [TEI supported models](https://huggingface.co/docs/text-embeddings-inference/supported_models)
- **ClusterIP over NodePort:** Keep embedding traffic internal; no external exposure needed
- **Batch size 32 (temporary):** Higher values OOM on A10G at current config; revisit post-profiling
- **HTTP over gRPC:** Simpler auth for initial rollout; gRPC migration deferred
```

Architecture decisions are the most important section for continuity. They record *why* something was built the way it was — information that cannot be inferred from reading the code. Each decision gets a bold label, a one-sentence rationale, and optionally a link to the reference material that informed the decision. The link format is standard markdown.

```markdown
---

## References

- [TEI Documentation](https://huggingface.co/docs/text-embeddings-inference)
- [vLLM Embedding API](https://docs.vllm.ai/en/latest/serving/embedding_models.html)
- [Internal Benchmark Results](docs/benchmarks/tei-vs-vllm-2026-02-23.csv)
```

References are any URLs or documents consulted during the session. Internal paths use relative file paths; external URLs use full https links. The LLM adds these during checkpoints when it uses a reference.

---

## 6. Storage Design

### Directory layout

openCodeMemory uses two storage locations:

**Per-project storage** — created inside the project by `ocm init`, added to `.gitignore`:

```
<project-root>/
└── .openCodeMemory/
    ├── memory.db              # SQLite database
    └── sessions/              # Markdown files
        ├── 2026-02-23_14-32_cursor_tei-migration.md
        └── 2026-02-20_09-15_claude-code_kv-cache-impl.md
```

**Global registry** — created at first `ocm init`, used for cross-project search:

```
~/.openCodeMemory/
└── registry.json
```

The registry is a JSON array of objects pointing to each project's database:

```json
[
  {
    "project": "sprinklr-inference",
    "project_root": "/home/achintya/work/sprinklr-inference",
    "db_path": "/home/achintya/work/sprinklr-inference/.openCodeMemory/memory.db",
    "registered_at": "2026-02-20T09:00:00"
  }
]
```

### SQLite schema

The database has four tables: `sessions`, `session_files`, `session_chunks`, and the virtual FTS5 table `sessions_fts`.

#### `sessions`

Stores one row per session. The `markdown_path` column is the relative path from `.openCodeMemory/` to the markdown file. The `goal` column duplicates the goal from `session_chunks` so that list/search results can show the goal without reading the markdown file.

```sql
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    -- Format: YYYY-MM-DD_HH-MM_<tool>_<slug>
    -- Example: 2026-02-23_14-32_cursor_tei-migration

    project         TEXT NOT NULL,
    -- Derived from the git remote URL or the project directory name.
    -- Used to scope search to the current project.

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
    -- The HEAD SHA when the session started. Used to compute git diffs.

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
    -- Updated on each checkpoint that provides a goal.

    slug            TEXT
    -- Short descriptor used in the filename. 4 words max, hyphenated.
    -- Provided by the LLM on first checkpoint.
);
```

#### `session_files`

One row per file touched per session. Maintained by the file-edit hook handler. Queried independently of the markdown file when the agent calls `ocm__get_session_files`.

```sql
CREATE TABLE session_files (
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    -- Relative to project root. Example: src/inference/embedding_server.py

    change_type     TEXT NOT NULL,
    -- 'created' | 'modified' | 'deleted'

    PRIMARY KEY (session_id, file_path)
    -- If a file is edited multiple times in a session, the row is replaced.
    -- The change_type reflects the net effect (created → modified if edited after creation).
);
```

#### `session_chunks`

The write buffer. Content is appended here incrementally during a session. The markdown file is rendered from all chunks for a given session on each checkpoint. This design means checkpoints are additive — the LLM does not need to re-provide all previous content, only the new content since the last checkpoint.

```sql
CREATE TABLE session_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,

    chunk_type      TEXT NOT NULL,
    -- Valid values and their semantics:
    --
    -- 'work_completed'
    --   Appended on each checkpoint. Never deleted.
    --   Rendered as ✅ bullets under "Work Completed".
    --
    -- 'work_pending'
    --   Replaced on each checkpoint (previous work_pending rows are deleted
    --   before inserting new ones). Rendered as 🔲 bullets under "Work To Be Completed".
    --   This reflects the current state of remaining work, not a history.
    --
    -- 'work_summary'
    --   Appended on each checkpoint. Never deleted.
    --   Rendered as bullets under "Work Done".
    --
    -- 'decision'
    --   Appended on each checkpoint. Never deleted.
    --   Rendered as bold-label bullets under "Architecture Decisions".
    --   Content format: "**Label:** Rationale. [Link title](url)" (optional link)
    --
    -- 'plan_file'
    --   Appended on each checkpoint. Content is JSON: {"path": "...", "header": "## ..."}
    --   Rendered as table rows under "Plan Files".
    --
    -- 'reference'
    --   Appended on each checkpoint. Content is JSON: {"url": "...", "title": "..."}
    --   Rendered as list items under "References".
    --
    -- 'diff_summary'
    --   Replaced on each checkpoint. Content is the git --stat output plus
    --   a one-sentence key change note written by the LLM.
    --   Only one diff_summary per session at any time.

    content         TEXT NOT NULL,
    created_at      INTEGER NOT NULL
    -- Unix timestamp of when this chunk was written.
);
```

#### `sessions_fts`

A virtual FTS5 table used for full-text search. The columns map to content extracted from `session_chunks` and `session_files`. The table is rebuilt for a given session on every `ocm__checkpoint` call.

```sql
CREATE VIRTUAL TABLE sessions_fts USING fts5(
    session_id    UNINDEXED,
    -- Not searchable; used to join back to sessions table.

    goal,
    -- The session goal text. Highest BM25 weight (10).
    -- This is the most discriminating field for finding the right session.

    todos,
    -- Concatenation of all work_completed and work_pending chunk content.
    -- BM25 weight 5. Captures task keywords ("migrate", "benchmark", "OOM").

    file_paths,
    -- All file paths from session_files, space-joined.
    -- Example: "src/inference/embedding_server.py k8s/deployments/tei-deployment.yaml"
    -- BM25 weight 5. Porter stemming tokenises path segments too.
    -- "embedding_server" matches "embedding_server.py" and similar variants.

    decisions,
    -- Concatenation of all decision chunk content.
    -- BM25 weight 8. Very high signal — contains named technologies,
    -- library names, and explicit reasoning keywords.

    work_summary,
    -- Concatenation of all work_summary chunk content.
    -- BM25 weight 3. Useful but noisier than goal and decisions.

    tokenize = 'porter unicode61'
    -- porter:   stem "optimizing" → "optim", matches "optimize", "optimization"
    -- unicode61: correct tokenisation for non-ASCII characters
);
```

### Why SQLite FTS5 and not a vector database

The primary job of search is **navigation to the right session**, not semantic similarity. A developer searching for "the session where I debugged TEI batch sizing" has a good idea of the keywords — "TEI", "batch", "sizing", or the file name "tei-deployment.yaml". BM25 on a small corpus (a developer might have hundreds of sessions, not millions) is fast, accurate, requires no embeddings, adds no dependencies, and runs entirely in SQLite which is already the storage layer.

Vector search would add: an embedding model dependency, an embedding computation on every write, approximate nearest-neighbour index management, and significantly more storage per session. For a corpus this size and use case, that complexity is not justified.

The combination of BM25 with column weighting (goal heavily weighted, decisions heavily weighted, work_summary lightly weighted) and a regex fallback for exact path fragment matching covers the full range of realistic queries.

---

## 7. MCP Server and Tools

### Server setup

The MCP server is implemented using `FastMCP` from the `mcp[cli]` package. It runs as a local stdio process — the coding assistant launches it as a subprocess and communicates via stdin/stdout. This is the standard pattern for local MCP servers and requires no network ports or authentication.

```python
# ocm/server.py
from mcp.server.fastmcp import FastMCP
from ocm.storage.db import Database
from ocm.tools import checkpoint, search, session

mcp = FastMCP("openCodeMemory")
db = Database.for_project()  # Finds .openCodeMemory/memory.db by walking up from cwd

# Register tools
mcp.tool()(checkpoint.ocm__checkpoint)
mcp.tool()(search.ocm__search_sessions)
mcp.tool()(session.ocm__list_sessions)
mcp.tool()(session.ocm__get_session_files)

if __name__ == "__main__":
    mcp.run()
```

The MCP server is registered in the IDE's MCP configuration. For Claude Code, this is done via `claude mcp add`. For Cursor, the entry is added to `.cursor/mcp.json`. Both use the stdio transport:

```json
{
  "mcpServers": {
    "opencodememory": {
      "command": "python",
      "args": ["-m", "ocm.server"],
      "env": { "OCM_PROJECT_DIR": "${workspaceFolder}" }
    }
  }
}
```

### Tool: `ocm__checkpoint`

This is the primary write tool. The LLM calls it to save or update session state. It is the only way content enters the system. All fields except `session_id` are optional — the LLM provides only what is new since the last checkpoint.

```python
@mcp.tool()
def ocm__checkpoint(
    session_id: str,
    # The session ID, provided by the hook system and available in the
    # environment or from the most recent ocm__list_sessions call.

    slug: str | None = None,
    # 4 words max, hyphenated. Provided on first checkpoint only.
    # Used to construct the markdown filename.
    # Example: "tei-migration", "kv-cache-prefill"

    goal: str | None = None,
    # 1-2 sentences describing what this session is trying to accomplish.
    # Provided on first checkpoint; may be updated on later ones if scope changes.

    work_completed: list[str] | None = None,
    # New items to add to the "Work Completed" list.
    # These are APPENDED to existing items, never replace them.
    # Each item is one bullet — concise, no prose.

    work_pending: list[str] | None = None,
    # Current list of remaining work items.
    # This REPLACES the previous work_pending list entirely.
    # Provide the full current list, not just new items.

    work_summary: list[str] | None = None,
    # New bullets describing what was done since the last checkpoint.
    # APPENDED to existing work_summary bullets.

    decisions: list[str] | None = None,
    # New architecture decisions made since the last checkpoint.
    # APPENDED to existing decisions.
    # Format: "**Label:** Rationale. [Link](url)"

    plan_files: list[dict] | None = None,
    # Plan/design files created during the session.
    # Each dict: {"path": "relative/path/to/file.md", "header": "## First Heading"}
    # APPENDED to existing plan_files entries.

    references: list[dict] | None = None,
    # URLs or internal documents referenced during the session.
    # Each dict: {"url": "https://...", "title": "Short descriptive title"}
    # APPENDED to existing references.

    status: str | None = None,
    # Update session status. Use 'frozen' when context limit hit.
    # Use 'closed' at session end. Omit for intermediate checkpoints.

) -> dict:
    """
    Save or update the current session state. Renders the markdown file.
    Returns: { "session_id": str, "markdown_path": str, "status": str }
    """
```

**What happens inside `ocm__checkpoint`:**

1. Open a database transaction.
2. If `slug` or `goal` are provided, update the `sessions` row.
3. If `work_pending` is provided, delete all existing `work_pending` chunks for this session, then insert the new ones.
4. Append new chunks for all other provided fields (`work_completed`, `work_summary`, `decisions`, `plan_files`, `references`).
5. Update `sessions.status` if `status` was provided.
6. Flush the in-memory file set to `session_files` (upsert).
7. Call `markdown_renderer.render_session(session_id, db)` which reads all chunks and the file list, and writes the complete markdown file.
8. Rebuild the FTS5 row for this session by deleting the old row and inserting a new one with the full concatenated content of all chunks.
9. Commit transaction.
10. Return `{ session_id, markdown_path, status }`.

### Tool: `ocm__search_sessions`

Natural language search over indexed session content. Returns up to 5 results above the relevance threshold. Results include `markdown_path` but not file content.

```python
@mcp.tool()
def ocm__search_sessions(
    query: str,
    # Natural language description of what you're looking for.
    # Examples:
    #   "tei migration embedding latency"
    #   "session where I fixed the OOM on A10G"
    #   "kv cache implementation last week"
    #   "cursor session about speculative decoding"

    limit: int = 5,
    # Maximum number of results. Hard capped at 5. Results below the
    # relevance threshold are excluded before this limit is applied.

    scope: str = 'project',
    # 'project': search only the current project's memory.db
    # 'global':  search all databases listed in ~/.openCodeMemory/registry.json

    tool_filter: str | None = None,
    # Restrict to sessions from a specific tool.
    # One of: 'claude-code', 'cursor', 'antigravity'
    # If not provided, all tools are searched.

) -> list[dict] | str:
    """
    Search for sessions matching a natural language query.

    Returns a list of up to 5 matching sessions, each with:
    {
        "rank": int,              # 1-based rank
        "score": float,           # relevance score (higher = more relevant)
        "session_id": str,
        "goal": str,              # one-line goal for display
        "date": str,              # ISO date of session start
        "tool": str,
        "top_files": list[str],   # up to 3 most-changed files
        "markdown_path": str      # full absolute path to the markdown file
    }

    Returns the string "No sessions found matching your query." if no results
    exceed the relevance threshold.
    """
```

### Tool: `ocm__list_sessions`

Returns recent sessions without search. Used when the developer wants to browse rather than search. Ordered by `started_at` descending.

```python
@mcp.tool()
def ocm__list_sessions(
    limit: int = 10,
    tool_filter: str | None = None,
) -> list[dict]:
    """
    Returns recent sessions ordered by start time (newest first).
    Each result: { session_id, goal, date, tool, status, markdown_path }
    """
```

### Tool: `ocm__get_session_files`

Returns only the file list for a session. Useful when the agent needs to know which files were changed without loading the full markdown context — for example, when deciding which files to open to orient itself.

```python
@mcp.tool()
def ocm__get_session_files(session_id: str) -> list[dict]:
    """
    Returns all files touched in the session.
    Each result: { "path": str, "change_type": "created" | "modified" | "deleted" }
    Ordered by change_type (created first, then modified, then deleted).
    """
```

---

## 8. Search System

### Query pre-processing

Before the query reaches FTS5, it is parsed by `search/preprocessor.py` to extract structured filters and clean the free-text portion.

```python
class ParsedQuery:
    clean_query: str         # The query with filters removed, ready for FTS5
    date_after: int | None   # Unix timestamp — filter sessions after this date
    date_before: int | None  # Unix timestamp — filter sessions before this date
    tool_hint: str | None    # Detected tool name, e.g. 'cursor'
    path_hint: str | None    # File path fragment, e.g. 'embedding_server'
    has_path_hint: bool

def extract_filters(query: str) -> ParsedQuery:
    """
    Recognises patterns in the query and extracts structured filters.

    Date patterns:
      "last week"          → date_after = now - 7 days
      "yesterday"          → date_after = yesterday 00:00, date_before = yesterday 23:59
      "before march 15"    → date_before = parsed date
      "in january"         → date range for the month

    Tool patterns:
      "in cursor", "cursor session"          → tool_hint = 'cursor'
      "claude code session", "in claude"     → tool_hint = 'claude-code'

    Path patterns:
      Any token containing '/' or ending in a known extension
      (.py, .yaml, .ts, .go, .json, .md, etc.)
      → path_hint = the token

    Stop word removal:
      Common English stop words are stripped from clean_query to avoid
      polluting FTS5 with low-signal terms.
    """
```

### BM25 search with column weights

SQLite's FTS5 module provides BM25 ranking natively. The `bm25()` function accepts per-column weights as arguments. A higher weight means that column contributes more to the relevance score. BM25 returns negative values — lower (more negative) means a better match.

```python
def fts_search(clean_query: str, db: Database) -> list[tuple[str, float]]:
    """
    Returns list of (session_id, score) tuples ordered by relevance.
    Score is the raw BM25 value (negative; lower = better match).
    """
    return db.execute("""
        SELECT
            session_id,
            bm25(sessions_fts, 0, 10, 5, 5, 8, 3) AS score
        FROM sessions_fts
        WHERE sessions_fts MATCH ?
        ORDER BY score
        LIMIT 20
    """, [clean_query]).fetchall()

    # Column weight arguments to bm25():
    # Position 0: session_id — 0 (UNINDEXED, not searchable)
    # Position 1: goal       — 10 (highest; most discriminating field)
    # Position 2: todos      — 5
    # Position 3: file_paths — 5
    # Position 4: decisions  — 8 (high; contains named technologies)
    # Position 5: work_summary — 3 (lowest; prose is noisier)
```

### Path fragment fallback

Some queries are better served by direct LIKE matching on file paths than by FTS5 (which tokenises on word boundaries, which may fragment path components in unexpected ways).

```python
def path_search(path_hint: str, db: Database) -> list[str]:
    """Returns session_ids where any touched file matches the path hint."""
    return [row[0] for row in db.execute(
        "SELECT DISTINCT session_id FROM session_files WHERE file_path LIKE ?",
        [f'%{path_hint}%']
    ).fetchall()]
```

### Relevance threshold

Results below a relevance threshold are excluded from the response entirely rather than returned with a low score. This is important for the UX — the assistant should say "no results found" rather than present weak matches that confuse the developer.

The threshold is tuned empirically. The initial value is `-0.15` (BM25 score). Sessions with a score less negative than this (i.e., weaker matches) are excluded. The threshold is a constant in `search/fts.py` and can be adjusted.

### Complete search function

```python
def search(
    query: str,
    db: Database,
    limit: int = 5,
    tool_filter: str | None = None,
) -> list[SearchResult]:

    # 1. Parse query into structured filters + clean FTS query
    parsed = extract_filters(query)

    # 2. Run FTS5 BM25 search
    fts_results = fts_search(parsed.clean_query, db)
    result_map = {session_id: score for session_id, score in fts_results}

    # 3. Regex/LIKE fallback for path fragments
    if parsed.has_path_hint:
        path_hits = path_search(parsed.path_hint, db)
        for session_id in path_hits:
            if session_id not in result_map:
                # Give path-only matches a fixed moderate score
                result_map[session_id] = -0.20

    # 4. Apply structured filters (date range, tool)
    candidate_ids = list(result_map.keys())
    filtered_ids = apply_filters(candidate_ids, parsed, tool_filter, db)

    # 5. Apply relevance threshold
    THRESHOLD = -0.15
    above = [
        (sid, result_map[sid])
        for sid in filtered_ids
        if result_map[sid] <= THRESHOLD
    ]

    # 6. Sort by score, take top N
    above.sort(key=lambda x: x[1])
    top = above[:limit]

    # 7. Enrich with session metadata (no markdown file read)
    return [enrich(session_id, score, rank + 1, db) for rank, (session_id, score) in enumerate(top)]


def enrich(session_id: str, score: float, rank: int, db: Database) -> SearchResult:
    """Fetches session metadata and top 3 files. Does not read the markdown file."""
    session = db.execute(
        "SELECT goal, started_at, tool, markdown_path FROM sessions WHERE id = ?",
        [session_id]
    ).fetchone()
    top_files = db.execute(
        "SELECT file_path FROM session_files WHERE session_id = ? LIMIT 3",
        [session_id]
    ).fetchall()
    abs_path = db.project_root / ".openCodeMemory" / session["markdown_path"]
    return SearchResult(
        rank=rank,
        score=round(-score, 3),  # Return as positive score for readability
        session_id=session_id,
        goal=session["goal"],
        date=datetime.fromtimestamp(session["started_at"]).date().isoformat(),
        tool=session["tool"],
        top_files=[row[0] for row in top_files],
        markdown_path=str(abs_path),
    )
```

---

## 9. Hook System

### Purpose

Hooks enable passive context capture without requiring the LLM to track everything manually. Two types of signals are captured via hooks:

1. **Session boundaries** — when a session starts and ends, so the database record can be created and closed, and git SHAs can be recorded.
2. **File edits** — which files were touched during the session, so the `session_files` table is populated automatically without the LLM having to remember.

The LLM still writes the semantic content (goal, decisions, work summary) via `ocm__checkpoint`. Hooks only capture objective signals.

### Hook handler architecture

All hook scripts are thin wrappers that call the `ocm-hook` CLI entry point:

```bash
# Example: Claude Code session-start hook script
#!/bin/bash
ocm-hook session-start "$CLAUDE_SESSION_ID" "$CLAUDE_PROJECT_DIR"
```

The `ocm-hook` entry point is installed as a console script by `pip install opencodememory`. It parses the event type and arguments, locates the project's `memory.db`, and dispatches to the appropriate handler in `ocm/hooks/`.

The MCP server process and the hook handler are both Python processes accessing the same SQLite database. SQLite's WAL (Write-Ahead Logging) mode handles concurrent access safely. The hook handler uses short transactions and the MCP server uses longer transactions only during `ocm__checkpoint`.

The in-memory file set (accumulating `afterFileEdit` events) lives in the hook handler's process, not the MCP server. On `ocm-hook file-edited`, the file path is appended directly to a lightweight journal file at `.openCodeMemory/active_<session_id>.jsonl`. On `ocm__checkpoint`, the MCP server reads this journal, upserts all paths into `session_files`, and deletes the journal file.

### Claude Code hook configuration

Claude Code hooks are configured in the project-level `settings.json` (`.claude/settings.json`) or the user-level settings file. `ocm init` appends the following:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "ocm-hook session-start $CLAUDE_SESSION_ID $CLAUDE_PROJECT_DIR"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "ocm-hook file-edited $CLAUDE_SESSION_ID $TOOL_INPUT_FILE_PATH"
          }
        ]
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "ocm-hook session-end $CLAUDE_SESSION_ID"
      }
    ]
  }
}
```

**Hook events used:**

- `UserPromptSubmit` — fires when the user submits a prompt. Used as the session-start signal. If a session with this ID already exists in the database (because the session was resumed), the hook is a no-op.
- `PostToolUse` with matcher `Write|Edit|MultiEdit` — fires after Claude writes or edits a file. The `$TOOL_INPUT_FILE_PATH` environment variable contains the path of the file that was written.
- `Stop` — fires when Claude finishes responding (agent stops). Used as a signal to trigger a final checkpoint if no checkpoint has been saved recently, and to mark the session as `closed`.

### Cursor hook configuration

Cursor 1.7+ supports hooks via `.cursor/hooks.json`. `ocm init` creates or appends to this file:

```json
{
  "version": 1,
  "hooks": {
    "beforeSubmitPrompt": [
      {
        "command": "ocm-hook session-start {conversation_id} {workspace_roots[0]}"
      }
    ],
    "afterFileEdit": [
      {
        "command": "ocm-hook file-edited {conversation_id} {file_path}"
      }
    ],
    "stop": [
      {
        "command": "ocm-hook session-end {conversation_id}"
      }
    ]
  }
}
```

**Hook events used:**

- `beforeSubmitPrompt` — fires before each prompt is sent to the model. `conversation_id` is stable for the duration of a Cursor agent session. `workspace_roots[0]` is the project directory.
- `afterFileEdit` — fires after the agent edits a file. `file_path` is the edited file's path.
- `stop` — fires when the agent finishes its task.

**Note:** Cursor hooks are a beta feature as of version 1.7. The API may change in future releases. `ocm init` records the Cursor version at install time and warns on startup if the installed version has changed significantly.

### git-based file tracking as fallback

If hooks are not available or are misconfigured, the file list can be computed at checkpoint time from git:

```python
def get_files_from_git(git_sha_start: str, project_root: Path) -> list[FileChange]:
    """
    Computes changed files between git_sha_start and HEAD.
    Used as fallback if hook-based file tracking is unavailable.
    """
    import git
    repo = git.Repo(project_root)
    diff = repo.commit(git_sha_start).diff(repo.head.commit)
    result = []
    for d in diff:
        if d.new_file:
            result.append(FileChange(path=d.b_path, change_type='created'))
        elif d.deleted_file:
            result.append(FileChange(path=d.a_path, change_type='deleted'))
        else:
            result.append(FileChange(path=d.b_path, change_type='modified'))
    return result
```

The git-based approach misses files that were edited and reverted within a session (they appear in the git diff as unchanged). For most development workflows this is acceptable.

---

## 10. Rule Injection

`ocm init` appends a rule block to `CLAUDE.md` (for Claude Code) and `.cursorrules` (for Cursor). These files are read by the IDE at the start of every session and included in the model's system context. The rule block teaches the LLM how to interact with openCodeMemory tools.

```markdown
## openCodeMemory

openCodeMemory is a session context tool available via MCP. Use it to save
and retrieve coding session state across conversations.

### Saving a session checkpoint

Call `ocm__checkpoint` in these situations:
1. When conversation context reaches approximately 70% full — call it automatically,
   do not ask the user. After saving, briefly note: "Session saved to openCodeMemory."
2. Before making a git commit — capture the current state before the history moves forward.
3. When the user asks you to save context or end the session.
4. At the natural end of a task, before closing.

Rules for writing checkpoint content:
- `goal`: 1-2 sentences. Specific. What are we building/fixing, and what is the target outcome?
- `work_completed`: one bullet per task. Concise. No prose.
- `work_pending`: full current list of remaining items. This replaces the previous list.
- `work_summary`: what happened since the last checkpoint. Bullets, not paragraphs.
- `decisions`: format each as "**Label:** Rationale." Add a link if there is a reference URL.
- `plan_files`: path + the exact first heading of the file (copy it, don't paraphrase).
- `references`: URL + a short (3-5 word) descriptive title.
- `slug`: 3-4 words hyphenated, describing the session topic. Provide on first checkpoint only.

### Loading context from a previous session

When the user asks to continue previous work, or when you detect that the current
task relates to prior sessions:

1. Call `ocm__search_sessions` with a natural language description of the task.
   Example: "TEI migration embedding server latency"
2. Present the results to the user and ask which session to continue.
3. Once confirmed, read the markdown file at the returned `markdown_path` directly.
   Do not ask for confirmation before reading — just read it.
4. If you need only the file list (to decide which files to open), call
   `ocm__get_session_files` instead of reading the full markdown.
```

---

## 11. Installation CLI

### Overview

`ocm init` is a one-time setup command run in a project directory. It detects which coding assistants are installed on the machine, configures them to use openCodeMemory, and creates the local storage structure.

### Installation steps

```
ocm init
```

1. **Detect project root** — Walk up from `cwd` until a `.git` directory is found. If none, use `cwd` as the project root. Extract the project name from the git remote URL or directory name.

2. **Create storage** — Create `.openCodeMemory/` and `.openCodeMemory/sessions/`. Initialise `memory.db` by running all migrations in `storage/schema.sql`.

3. **Update `.gitignore`** — Append `.openCodeMemory/` to the project's `.gitignore`. If `.gitignore` does not exist, create it.

4. **Detect installed assistants** — Check for:
   - Claude Code: `which claude` or `~/.claude/` directory
   - Cursor: `which cursor` or `~/.cursor/` directory

5. **Configure MCP server** — For each detected assistant:
   - Claude Code: run `claude mcp add --scope project opencodememory -- python -m ocm.server`
   - Cursor: create or append to `.cursor/mcp.json`

6. **Install hooks** — For each detected assistant:
   - Claude Code: append hook configuration to `.claude/settings.json`
   - Cursor: create or append to `.cursor/hooks.json`

7. **Inject rules** — For each detected assistant:
   - Claude Code: append the rule block to `CLAUDE.md` (create if absent)
   - Cursor: append the rule block to `.cursorrules` (create if absent)

8. **Update global registry** — Add this project to `~/.openCodeMemory/registry.json`.

9. **Print summary** — List each step and whether it succeeded or was skipped (e.g., if the tool was not installed).

### Additional CLI commands

```bash
ocm list [--limit N] [--tool cursor|claude-code]
# Lists recent sessions in a table: ID, date, tool, goal (truncated), status

ocm search "<query>" [--scope project|global] [--tool cursor|claude-code]
# Runs the search pipeline and prints results in a table with the markdown path

ocm show <session_id>
# Prints the full markdown file for a session to stdout

ocm export <session_id>
# Copies the absolute path of the session's markdown file to the clipboard

ocm rebuild-index
# Drops and rebuilds the FTS5 index from the session_chunks and session_files tables
# Useful if the index becomes corrupted or out of sync
```

---

## 12. Project Structure

```
openCodeMemory/
│
├── ocm/                                   # Main Python package
│   ├── __init__.py
│   │
│   ├── server.py                          # FastMCP server entry point
│   │                                      # Initialises DB, registers tools, runs mcp.run()
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── checkpoint.py                  # ocm__checkpoint implementation
│   │   ├── search.py                      # ocm__search_sessions implementation
│   │   └── session.py                     # ocm__list_sessions, ocm__get_session_files
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── db.py                          # Database class: connection, migrations, project detection
│   │   ├── schema.sql                     # Full SQL schema (CREATE TABLE, CREATE VIRTUAL TABLE)
│   │   └── markdown_renderer.py           # render_session(session_id, db) → writes .md file
│   │
│   ├── hooks/
│   │   ├── __init__.py
│   │   ├── handler.py                     # CLI entry point for ocm-hook <event> <args>
│   │   ├── file_tracker.py                # Reads/writes the active_<session_id>.jsonl journal
│   │   └── git.py                         # get_head_sha(), get_diff_stat(sha_start)
│   │
│   └── search/
│       ├── __init__.py
│       ├── fts.py                         # fts_search(), enrich(), full search() function
│       └── preprocessor.py                # extract_filters() → ParsedQuery
│
├── hooks/                                 # Shell scripts installed per-IDE by ocm init
│   ├── claude-code/
│   │   ├── session-start.sh
│   │   ├── post-tool-use.sh
│   │   └── session-end.sh
│   └── cursor/
│       ├── session-start.sh
│       ├── after-file-edit.sh
│       └── stop.sh
│
├── install/
│   ├── __init__.py
│   ├── cli.py                             # click CLI: `ocm init`, `ocm list`, `ocm search`, etc.
│   ├── claude_code.py                     # Configures Claude Code MCP + hooks + CLAUDE.md
│   └── cursor.py                          # Configures Cursor MCP + hooks + .cursorrules
│
├── rules/
│   ├── CLAUDE.md.snippet                  # Rule text template for CLAUDE.md
│   └── cursorrules.snippet                # Rule text template for .cursorrules
│
├── pyproject.toml
├── README.md
└── tests/
    ├── test_checkpoint.py
    ├── test_search.py
    ├── test_markdown_renderer.py
    └── fixtures/
        └── sample_session.md
```

---

## 13. Dependencies

```toml
[project]
name = "opencodememory"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "mcp[cli]>=1.0",
    # FastMCP — provides the MCP server framework and CLI utilities.
    # Used in server.py and all tool modules.

    "gitpython>=3.1",
    # Git operations: get HEAD SHA on session start, compute diff --stat
    # at checkpoint time. Used in hooks/git.py.

    "click>=8.0",
    # CLI framework for the ocm command: init, list, search, show, export.
    # Used in install/cli.py.

    "python-dateutil>=2.9",
    # Parses natural language date strings in search queries.
    # "last week", "before march 15", "in january" → datetime objects.
    # Used in search/preprocessor.py.
]

[project.scripts]
ocm = "ocm.install.cli:main"
ocm-hook = "ocm.hooks.handler:main"
# ocm-hook is the entry point called by all IDE hook scripts.
# It is a separate script so hook scripts stay as one-liners.

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

All storage uses Python's stdlib `sqlite3`. No ORM. No external database. No LLM dependencies. The `mcp` package handles the MCP protocol; the tool does not implement MCP primitives directly.

---

## 14. Build Order

Implement in this order. Each step is independently testable before moving to the next.

**Step 1: Storage layer**

Implement `storage/schema.sql` with the full schema. Implement `storage/db.py` with:
- `Database.for_project()` — walks up from cwd to find `.openCodeMemory/memory.db`
- `Database.init()` — runs migrations from `schema.sql`
- Connection management (WAL mode, foreign keys enabled)

Write tests that create an in-memory database, run migrations, and verify the schema.

**Step 2: Markdown renderer**

Implement `storage/markdown_renderer.py`. The function `render_session(session_id: str, db: Database) -> Path` reads all chunks and the file list for a session, assembles the markdown using the format from Section 5, writes the file, and returns the path.

Write a test that inserts sample chunks and files, calls the renderer, and asserts the output matches the expected markdown format exactly.

**Step 3: Checkpoint tool**

Implement `tools/checkpoint.py`. This is the most complex tool. Implement the transaction logic described in Section 7, including the FTS rebuild and the markdown render on every call.

Write tests covering: first checkpoint (creates session), subsequent checkpoints (appends/replaces correctly), work_pending replacement behaviour, status transitions.

**Step 4: Hook handler**

Implement `hooks/git.py` (get_head_sha, get_diff_stat) and `hooks/file_tracker.py` (read/write the `.jsonl` journal file). Implement `hooks/handler.py` as the `ocm-hook` CLI entry point, handling `session-start`, `file-edited`, and `session-end` events.

**Step 5: Search**

Implement `search/preprocessor.py` (date, tool, path filter extraction). Implement `search/fts.py` (BM25 query, path fallback, threshold filter, enrichment). Implement `tools/search.py` wrapping the search function as the MCP tool.

Write tests using fixed sessions in a test database. Assert that relevant sessions rank higher than irrelevant ones. Test threshold behaviour (weak matches excluded).

**Step 6: Session tools**

Implement `tools/session.py` with `ocm__list_sessions` and `ocm__get_session_files`. These are simple database queries.

**Step 7: MCP server**

Implement `server.py`. Wire all tools into FastMCP. Test by running the server and calling tools via the MCP inspector (`npx @modelcontextprotocol/inspector`).

**Step 8: Install CLI**

Implement `install/claude_code.py` and `install/cursor.py` (the assistant-specific configuration logic). Implement `install/cli.py` with all `click` commands. Test `ocm init` end-to-end in a temporary directory with a mock git repo.

**Step 9: Hook scripts and rule snippets**

Write the shell hook scripts in `hooks/claude-code/` and `hooks/cursor/`. Write the rule snippets in `rules/`. These are static files; test them by verifying the installed result in a test project.

---

## 15. Deferred to v2

The following features are intentionally out of scope for v1. They are documented here so the architecture does not foreclose them.

**Antigravity integration.** Antigravity uses a Skills system (`.agent/skills/<name>/SKILL.md` + scripts) for on-demand capability extension and supports MCP via its MCP Store. The path for openCodeMemory integration is: an Antigravity Skill that instructs the agent to call `ocm__checkpoint` at appropriate times, plus MCP configuration added to Antigravity's `mcp_config.json`. This requires hands-on testing once Antigravity's hook/lifecycle API stabilises.

**Count-based checkpoint fallback.** For models with weak self-token-awareness, the 70% context rule may not fire reliably. A fallback would count `afterFileEdit` events per session and trigger a checkpoint automatically after every N edits (e.g., every 20 file edits). This adds complexity to the hook handler and requires the hook handler to communicate back to the MCP server.

**Full git diff storage.** Currently only `git diff --stat` is stored. The full diff between `git_sha_start` and `git_sha_end` could be stored in `.openCodeMemory/diffs/<session_id>.diff` for sessions where the agent needs to understand exactly what changed. The agent can always get the full diff by running `git diff <sha>` directly; dedicated storage is only a convenience.

**Session parent linking.** A nullable `parent_session_id` column on the `sessions` table would allow recording chains of sessions working on the same feature. Loading would remain intentional (the agent queries `ocm__list_sessions` and picks what to load); the link would only provide a navigational hint. Deferred because multi-session feature work can be addressed by searching for relevant sessions by topic without explicit linking.

**Index rebuild from markdown.** If `memory.db` is deleted or corrupted, sessions would be lost unless the markdown files can be parsed back into the database. Implementing a parser that reads the YAML frontmatter and markdown sections and rebuilds the database is straightforward but not needed for v1.