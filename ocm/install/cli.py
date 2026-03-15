from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import click

from ocm.hooks.git import get_project_name
from ocm.storage.db import Database
from ocm.storage.markdown_renderer import render_session


@click.group()
def main() -> None:
    """openCodeMemory — persistent session memory for AI coding assistants."""


@main.command("init")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def cmd_init(yes: bool) -> None:
    """One-time setup: create storage, configure IDE hooks and MCP server."""
    # 1. Detect project root
    project_root = _find_project_root(Path.cwd())
    click.echo(f"Project root: {project_root}")

    # 2. Create storage
    ocm_dir = project_root / ".openCodeMemory"
    ocm_dir.mkdir(exist_ok=True)
    (ocm_dir / "sessions").mkdir(exist_ok=True)
    db_path = ocm_dir / "memory.db"
    db = Database.init(db_path)
    db.close()
    _report("Created .openCodeMemory/memory.db", True)

    # 3. Update .gitignore
    _report(*_update_gitignore(project_root))

    # 4. Detect installed assistants
    from ocm.install import claude_code, cursor

    assistants = []
    if claude_code.is_installed():
        assistants.append(("Claude Code", claude_code))
    if cursor.is_installed():
        assistants.append(("Cursor", cursor))

    if not assistants:
        click.echo("  No supported AI assistants detected (claude, cursor).")
        click.echo("  Install Claude Code or Cursor, then re-run ocm init.")

    # 5-7. Configure each assistant
    for name, module in assistants:
        click.echo(f"\nConfiguring {name}:")
        _report(*module.configure_mcp(project_root))
        _report(*module.configure_hooks(project_root))
        _report(*module.inject_rules(project_root))

    # 8. Update global registry
    _report(*_update_registry(project_root, db_path))

    click.echo("\n✓ openCodeMemory initialized successfully.")


@main.command("install")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
def cmd_install(yes: bool) -> None:
    """One-time global setup: central DB + global IDE hooks and MCP server."""
    # 1. Create ~/.openCodeMemory/ + memory.db
    ocm_dir = Path.home() / ".openCodeMemory"
    ocm_dir.mkdir(exist_ok=True)
    (ocm_dir / "sessions").mkdir(exist_ok=True)
    db_path = ocm_dir / "memory.db"
    db = Database.init(db_path)
    db.close()
    _report(True, f"Created {db_path}")

    # 2. Configure each installed assistant globally
    from ocm.install import claude_code, cursor

    assistants = []
    if claude_code.is_installed():
        assistants.append(("Claude Code", claude_code))
    if cursor.is_installed():
        assistants.append(("Cursor", cursor))

    if not assistants:
        click.echo("  No supported AI assistants detected.")
        return

    for name, module in assistants:
        click.echo(f"\nConfiguring {name} (global):")
        _report(*module.configure_mcp_global())
        _report(*module.configure_hooks_global())
        _report(*module.inject_rules_global())

    click.echo("\n✓ openCodeMemory installed globally.")
    click.echo("  Sessions from all projects → ~/.openCodeMemory/memory.db")
    click.echo("  No need to run 'ocm init' per project.")


@main.command("list")
@click.option("--limit", "-n", default=10, help="Number of sessions to show")
@click.option("--tool", default=None, help="Filter by tool (claude-code, cursor)")
def cmd_list(limit: int, tool: str | None) -> None:
    """List recent sessions."""
    try:
        db = Database.for_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    from ocm.tools.session import ocm__list_sessions
    ocm__list_sessions.__globals__["_db"] = db
    sessions = ocm__list_sessions(limit=limit, tool_filter=tool)

    if not sessions:
        click.echo("No sessions found.")
        return

    click.echo(f"{'ID':<45} {'Date':<12} {'Tool':<12} {'Status':<8} {'Goal'}")
    click.echo("-" * 100)
    for s in sessions:
        goal = (s["goal"] or "")[:40]
        if len(s["goal"] or "") > 40:
            goal += "..."
        sid = s["session_id"][:40]
        click.echo(f"{sid:<45} {s['date']:<12} {s['tool']:<12} {s['status']:<8} {goal}")


@main.command("search")
@click.argument("query")
@click.option("--scope", default="project", type=click.Choice(["project", "global"]))
@click.option("--tool", default=None, help="Filter by tool")
def cmd_search(query: str, scope: str, tool: str | None) -> None:
    """Search sessions with a natural language query."""
    try:
        db = Database.for_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    from ocm.search.fts import search
    results = search(query, db, limit=5, tool_filter=tool)

    if not results:
        click.echo("No sessions found matching your query.")
        return

    for r in results:
        click.echo(f"\n[{r.rank}] score={r.score:.3f} | {r.date} | {r.tool} | {r.session_id}")
        click.echo(f"    Goal: {r.goal}")
        if r.top_files:
            click.echo(f"    Files: {', '.join(r.top_files[:3])}")
        click.echo(f"    Path: {r.markdown_path}")


@main.command("show")
@click.argument("session_id")
def cmd_show(session_id: str) -> None:
    """Print the full markdown for a session."""
    try:
        db = Database.for_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    session = db.execute(
        "SELECT markdown_path FROM sessions WHERE id = ?", [session_id]
    ).fetchone()
    if session is None:
        click.echo(f"Session not found: {session_id}", err=True)
        sys.exit(1)

    md_path = db.project_root / ".openCodeMemory" / session["markdown_path"]
    if md_path.exists():
        click.echo(md_path.read_text())
    else:
        # Re-render on demand
        render_session(session_id, db)
        click.echo(md_path.read_text())


@main.command("export")
@click.argument("session_id")
def cmd_export(session_id: str) -> None:
    """Copy the markdown file path to the clipboard."""
    try:
        db = Database.for_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    session = db.execute(
        "SELECT markdown_path FROM sessions WHERE id = ?", [session_id]
    ).fetchone()
    if session is None:
        click.echo(f"Session not found: {session_id}", err=True)
        sys.exit(1)

    abs_path = str(db.project_root / ".openCodeMemory" / session["markdown_path"])

    try:
        import subprocess
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=abs_path.encode(), check=True)
            click.echo(f"Copied to clipboard: {abs_path}")
        elif sys.platform.startswith("linux"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=abs_path.encode(), check=True)
            click.echo(f"Copied to clipboard: {abs_path}")
        else:
            click.echo(abs_path)
    except Exception:
        click.echo(abs_path)


@main.command("rebuild-index")
def cmd_rebuild_index() -> None:
    """Drop and rebuild the FTS5 search index from session_chunks."""
    try:
        db = Database.for_project()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("Rebuilding FTS5 index...")

    # Clear existing FTS
    db.execute("DELETE FROM sessions_fts")

    sessions = db.execute("SELECT id FROM sessions").fetchall()
    count = 0
    for row in sessions:
        sid = row[0]
        try:
            from ocm.tools.checkpoint import _rebuild_fts
            _rebuild_fts(sid, db)
            count += 1
        except Exception as e:
            click.echo(f"  Warning: failed to index {sid}: {e}", err=True)

    db.commit()
    click.echo(f"✓ Rebuilt FTS index for {count} sessions.")


# --- Helpers ---

def _find_project_root(cwd: Path) -> Path:
    """Walk up from cwd to find a .git directory."""
    current = cwd.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return cwd.resolve()
        current = parent


def _update_gitignore(project_root: Path) -> tuple[bool, str]:
    gitignore = project_root / ".gitignore"
    entry = ".openCodeMemory/"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if entry in content:
            return True, f".gitignore already contains {entry} (skipped)"
        gitignore.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    else:
        gitignore.write_text(f"{entry}\n", encoding="utf-8")

    return True, f"Added {entry} to .gitignore"


def _update_registry(project_root: Path, db_path: Path) -> tuple[bool, str]:
    """Add this project to ~/.openCodeMemory/registry.json."""
    registry_dir = Path.home() / ".openCodeMemory"
    registry_dir.mkdir(exist_ok=True)
    registry_path = registry_dir / "registry.json"

    registry: list = []
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text())
        except json.JSONDecodeError:
            registry = []

    project_name = get_project_name(project_root)

    # Check if already registered
    existing_paths = {entry.get("project_root") for entry in registry}
    if str(project_root) not in existing_paths:
        registry.append({
            "project": project_name,
            "project_root": str(project_root),
            "db_path": str(db_path),
            "registered_at": datetime.now().isoformat(),
        })
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        return True, f"Registered in ~/.openCodeMemory/registry.json"

    return True, "Already in global registry (skipped)"


def _report(success: bool, message: str) -> None:
    icon = "✓" if success else "✗"
    click.echo(f"  {icon} {message}")
