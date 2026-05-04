from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from ocm.hooks.git import get_project_name
from ocm.storage.db import Database
from ocm.storage.markdown_renderer import render_session


@click.group()
def main() -> None:
    """openCodeMemory — persistent session memory for AI coding assistants."""


@main.command("init")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option(
    "--cursor-hooks",
    default="none",
    type=click.Choice(["none", "minimal", "full"]),
    help="Cursor hook profile to configure.",
)
@click.option(
    "--claude-hooks",
    default="minimal",
    type=click.Choice(["none", "minimal", "full"]),
    help="Claude Code hook profile to configure.",
)
def cmd_init(yes: bool, cursor_hooks: str, claude_hooks: str) -> None:
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
        hook_profile = claude_hooks if name == "Claude Code" else cursor_hooks
        _report(*module.configure_hooks(project_root, profile=hook_profile))
        _report(*module.inject_rules(project_root))
        if hasattr(module, "inject_mdc_rule"):
            _report(*module.inject_mdc_rule(project_root))

    # 8. Update global registry
    _report(*_update_registry(project_root, db_path))

    click.echo("\n✓ openCodeMemory initialized successfully.")
    click.echo("\nNext: start the MCP server (runs in the background):")
    click.echo("    ocm serve")
    click.echo("Use `ocm status` to check the server, `ocm stop` to stop it.")


@main.command("install")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option(
    "--cursor-hooks",
    default="none",
    type=click.Choice(["none", "minimal", "full"]),
    help="Cursor global hook profile to configure.",
)
@click.option(
    "--claude-hooks",
    default="minimal",
    type=click.Choice(["none", "minimal", "full"]),
    help="Claude Code global hook profile to configure.",
)
def cmd_install(yes: bool, cursor_hooks: str, claude_hooks: str) -> None:
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
        hook_profile = claude_hooks if name == "Claude Code" else cursor_hooks
        _report(*module.configure_hooks_global(profile=hook_profile))
        _report(*module.inject_rules_global())

    click.echo("\n✓ openCodeMemory installed globally.")
    click.echo("  Sessions from all projects → ~/.openCodeMemory/memory.db")
    click.echo("  No need to run 'ocm init' per project.")
    click.echo("\nNext: start the global MCP server (runs in the background):")
    click.echo("    ocm serve --global")
    click.echo("Use `ocm status --global` to check the server, `ocm stop --global` to stop it.")


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


@main.command("checkpoint")
@click.option("--session-id", default=None, help="Session id (required unless --from-stdin).")
@click.option("--from-stdin", is_flag=True, help="Read hook-style JSON payload from stdin.")
@click.option("--tool", default=None, help="Tool name (cursor|claude-code|antigravity).")
@click.option("--slug", default=None, help="Session slug.")
@click.option("--goal", default=None, help="Session goal.")
@click.option("--status", default=None, help="Session status.")
@click.option("--completed", "work_completed", multiple=True, help="Work completed item (repeat).")
@click.option("--pending", "work_pending", multiple=True, help="Work pending item (repeat).")
@click.option("--summary", "work_summary", multiple=True, help="Work summary item (repeat).")
@click.option("--decision", "decisions", multiple=True, help="Architecture decision item (repeat).")
@click.option("--plan-file", "plan_files", multiple=True, help='Plan file JSON, e.g. {"path":"...","header":"## ..."}')
@click.option("--reference", "references", multiple=True, help='Reference JSON, e.g. {"url":"...","title":"..."}')
def cmd_checkpoint(
    session_id: str | None,
    from_stdin: bool,
    tool: str | None,
    slug: str | None,
    goal: str | None,
    status: str | None,
    work_completed: tuple[str, ...],
    work_pending: tuple[str, ...],
    work_summary: tuple[str, ...],
    decisions: tuple[str, ...],
    plan_files: tuple[str, ...],
    references: tuple[str, ...],
) -> None:
    """Write or update a session checkpoint from CLI."""
    payload: dict[str, Any] = {}
    if from_stdin:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            click.echo(f"Error: invalid JSON from stdin: {e}", err=True)
            sys.exit(1)

    resolved_session_id = (
        session_id
        or payload.get("session_id")
        or payload.get("conversation_id")
    )
    if not resolved_session_id:
        click.echo("Error: --session-id is required (or provide session_id/conversation_id via --from-stdin).", err=True)
        sys.exit(1)

    resolved_tool = (
        tool
        or payload.get("tool")
        or ("cursor" if payload.get("conversation_id") else "claude-code")
    )

    from ocm.tools.checkpoint import ocm__checkpoint

    checkpoint_payload = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else {}

    result = ocm__checkpoint(
        session_id=resolved_session_id,
        tool=resolved_tool,
        slug=slug or payload.get("slug") or checkpoint_payload.get("slug"),
        goal=goal or payload.get("goal") or checkpoint_payload.get("goal"),
        status=status or payload.get("status") or checkpoint_payload.get("status"),
        work_completed=_merged_list(work_completed, payload.get("work_completed"), checkpoint_payload.get("work_completed")),
        work_pending=_merged_list(work_pending, payload.get("work_pending"), checkpoint_payload.get("work_pending")),
        work_summary=_merged_list(work_summary, payload.get("work_summary"), checkpoint_payload.get("work_summary")),
        decisions=_merged_list(decisions, payload.get("decisions"), checkpoint_payload.get("decisions")),
        plan_files=_merged_json_list(plan_files, payload.get("plan_files"), checkpoint_payload.get("plan_files")),
        references=_merged_json_list(references, payload.get("references"), checkpoint_payload.get("references")),
    )

    click.echo(json.dumps(result, indent=2))


@main.command("serve")
@click.option("--foreground", "-f", is_flag=True,
              help="Stay attached to the terminal instead of daemonizing (for debugging).")
@click.option("--global", "global_", is_flag=True,
              help="Target the global server instead of the project server for cwd.")
def cmd_serve(foreground: bool, global_: bool) -> None:
    """Start the openCodeMemory MCP server (HTTP, streamable-http, background by default)."""
    from ocm.install import server_config as sc

    cfg = sc.global_config() if global_ else sc.resolve_for_cwd(Path.cwd())

    # Self-test: open the DB and verify search works before binding the port.
    n = _self_test(cfg.db_path)
    click.echo(f"Self-test passed ({n} session(s) in DB).")

    # Refuse to start a duplicate.
    existing = _read_pid(cfg.pid_path)
    if existing and _pid_alive(existing):
        click.echo(f"Server already running: PID {existing}  {cfg.url}")
        click.echo(f"  Stop: ocm stop{'  --global' if global_ else ''}   or: kill {existing}")
        return

    if foreground:
        _write_pid(cfg.pid_path, os.getpid())
        click.echo(f"openCodeMemory MCP server  PID {os.getpid()}  {cfg.url}  [foreground]")
        try:
            from ocm.server import run_http
            run_http(cfg.host, cfg.port, cfg.project_root)
        finally:
            _clear_pid(cfg.pid_path)
        return

    # Background: re-exec with --foreground, piping output to server.log.
    with open(cfg.log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-c", "from ocm.install.cli import main; main()",
             "serve", "--foreground"],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            # Set cwd so resolve_for_cwd finds the same server.json in the child.
            cwd=str(cfg.project_root) if cfg.project_root else str(Path.home()),
        )

    # Wait up to 5 s for the port to accept connections.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            import socket as _socket
            with _socket.create_connection(("127.0.0.1", cfg.port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)
    else:
        click.echo("Server did not start within 5 seconds.", err=True)
        click.echo(f"  Logs: {cfg.log_path}", err=True)
        proc.terminate()
        sys.exit(1)

    _write_pid(cfg.pid_path, proc.pid)
    click.echo("openCodeMemory MCP server started")
    click.echo(f"  PID:  {proc.pid}")
    click.echo(f"  URL:  {cfg.url}")
    click.echo(f"  Logs: {cfg.log_path}")
    click.echo(f"  Stop: ocm stop{'  --global' if global_ else ''}   or: kill {proc.pid}")


@main.command("status")
@click.option("--global", "global_", is_flag=True, help="Show the global server.")
@click.option("--all", "all_", is_flag=True, help="Show all known servers.")
def cmd_status(global_: bool, all_: bool) -> None:
    """Show the running status of openCodeMemory MCP server(s)."""
    from ocm.install import server_config as sc

    if all_:
        configs = sc.all_known()
        if not configs:
            click.echo("No openCodeMemory servers configured yet.")
            return
    elif global_:
        configs = [sc.global_config()]
    else:
        configs = [sc.resolve_for_cwd(Path.cwd())]

    for cfg in configs:
        pid = _read_pid(cfg.pid_path)
        alive = bool(pid and _pid_alive(pid))
        status_str = f"running (PID {pid})" if alive else "stopped"
        click.echo(f"[{cfg.scope}]  {cfg.url}  {status_str}")
        if alive:
            click.echo(f"   project:  {cfg.project_root or '(global)'}")
            click.echo(f"   db:       {cfg.db_path}")
            click.echo(f"   logs:     {cfg.log_path}")
            click.echo(f"   kill:     kill {pid}")


@main.command("stop")
@click.option("--global", "global_", is_flag=True, help="Stop the global server.")
def cmd_stop(global_: bool) -> None:
    """Stop the openCodeMemory MCP server for the current project (or global)."""
    from ocm.install import server_config as sc

    cfg = sc.global_config() if global_ else sc.resolve_for_cwd(Path.cwd())
    pid = _read_pid(cfg.pid_path)
    if not pid or not _pid_alive(pid):
        click.echo(f"No server running for [{cfg.scope}].")
        _clear_pid(cfg.pid_path)
        return
    os.kill(pid, signal.SIGTERM)
    _clear_pid(cfg.pid_path)
    click.echo(f"Stopped [{cfg.scope}] server (PID {pid}).")


@main.command("uninstall")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts")
@click.option("--global", "global_", is_flag=True, help="Remove global installation instead of project-local.")
def cmd_uninstall(yes: bool, global_: bool) -> None:
    """Remove openCodeMemory hooks and rules from IDE configuration."""
    from ocm.install import claude_code, cursor

    if global_:
        click.echo("Removing openCodeMemory global configuration:")
        _report(*claude_code.remove_hooks_global())
        _report(*claude_code.remove_rules_global())
        _report(*cursor.remove_hooks_global())
        click.echo("\n✓ Global openCodeMemory configuration removed.")
        click.echo("  Storage (~/.openCodeMemory/) was NOT deleted. Remove it manually if desired.")
        return

    project_root = _find_project_root(Path.cwd())
    click.echo(f"Removing openCodeMemory from project: {project_root}")

    for name, module in [("Claude Code", claude_code), ("Cursor", cursor)]:
        click.echo(f"\n{name}:")
        _report(*module.remove_hooks(project_root))
        _report(*module.remove_rules(project_root))
        if hasattr(module, "remove_mdc_rule"):
            _report(*module.remove_mdc_rule(project_root))

    click.echo("\n✓ openCodeMemory removed from project configuration.")
    click.echo("  Session data (.openCodeMemory/) was NOT deleted. Remove it manually if desired.")


@main.command("help")
@click.argument("command_name", required=False)
@click.pass_context
def cmd_help(ctx: click.Context, command_name: str | None) -> None:
    """Show command list or command-specific help."""
    if command_name:
        cmd = main.commands.get(command_name)
        if cmd is None:
            click.echo(f"Unknown command: {command_name}", err=True)
            sys.exit(2)
        click.echo(cmd.get_help(ctx))
        return

    click.echo("openCodeMemory CLI commands:\n")
    for name in sorted(main.commands):
        cmd = main.commands[name]
        short = (cmd.help or "").strip().splitlines()[0] if cmd.help else ""
        click.echo(f"  {name:<14} {short}")
    click.echo("\nUse `ocm <command> --help` for detailed usage.")


# --- Helpers ---

def _merged_list(*values: Any) -> list[str] | None:
    merged: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, tuple):
            merged.extend(str(v) for v in value if str(v).strip())
            continue
        if isinstance(value, list):
            merged.extend(str(v) for v in value if str(v).strip())
            continue
    return merged or None


def _merged_json_list(*values: Any) -> list[dict] | None:
    merged: list[dict] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, tuple):
            for raw in value:
                raw = str(raw).strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise click.ClickException(f"Invalid JSON list item: {e}") from e
                if isinstance(parsed, dict):
                    merged.append(parsed)
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    merged.append(item)
    return merged or None

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


def _self_test(db_path: Path) -> int:
    """Open db_path, run a list + search query, return session count. Raises on error."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"No database found at {db_path}. "
            "Run `ocm init` (project) or `ocm install` (global) first."
        )
    from ocm.storage.db import Database
    from ocm.search.fts import search as _fts_search
    db = Database._connect(db_path)
    try:
        n = db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        _fts_search("checkpoint", db, limit=1)
        return n
    finally:
        db.close()


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid))


def _clear_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _report(success: bool, message: str) -> None:
    icon = "✓" if success else "✗"
    click.echo(f"  {icon} {message}")
