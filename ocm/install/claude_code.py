from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _ocm_root() -> Path:
    """Absolute path to the openCodeMemory project root (where pyproject.toml lives)."""
    return Path(__file__).parent.parent.parent.resolve()


def _hook_cmd() -> str:
    return (
        'SESSION_ID="${CLAUDE_SESSION_ID:-}"; '
        'echo "{\\"additionalContext\\": \\"openCodeMemory session_id: ${SESSION_ID}. '
        'Call ocm__checkpoint with this session_id as your first tool use if starting a new session.\\"}"'
    )


def _make_hook_config() -> dict:
    return {
        "UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": _hook_cmd()}]},
        ],
    }

RULE_SNIPPET_PATH = Path(__file__).parent.parent.parent / "rules" / "CLAUDE.md.snippet"


def is_installed() -> bool:
    """Check if Claude Code is available on the system."""
    return (
        _command_exists("claude")
        or (Path.home() / ".claude").exists()
    )


def configure_mcp(project_root: Path) -> tuple[bool, str]:
    """Register the MCP server with Claude Code."""
    try:
        result = subprocess.run(
            [
                "claude", "mcp", "add",
                "--scope", "project",
                "opencodememory",
                "--",
                "uv", "run", "python", "-m", "ocm.server",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        if result.returncode == 0:
            return True, "MCP server registered with Claude Code"
        return False, f"claude mcp add failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "claude command not found"
    except Exception as e:
        return False, str(e)


def configure_hooks(project_root: Path) -> tuple[bool, str]:
    """Append hook configuration to .claude/settings.json."""
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    hooks = settings.setdefault("hooks", {})

    # Merge each hook event
    for event, new_entries in _make_hook_config().items():
        if event not in hooks:
            hooks[event] = new_entries
        else:
            existing_cmds = {
                inner.get("command")
                for entry in hooks[event] if isinstance(entry, dict)
                for inner in entry.get("hooks", []) if isinstance(inner, dict)
            }
            for entry in new_entries:
                entry_cmds = {
                    inner.get("command")
                    for inner in entry.get("hooks", []) if isinstance(inner, dict)
                }
                if not entry_cmds.issubset(existing_cmds):
                    hooks[event].append(entry)

    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return True, f"Hook configuration written to {settings_path}"


def inject_rules(project_root: Path) -> tuple[bool, str]:
    """Append the openCodeMemory rule block to CLAUDE.md."""
    claude_md = project_root / "CLAUDE.md"

    if RULE_SNIPPET_PATH.exists():
        snippet = RULE_SNIPPET_PATH.read_text(encoding="utf-8")
    else:
        snippet = _fallback_snippet()

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "openCodeMemory" in existing:
            return True, "CLAUDE.md already contains openCodeMemory rules (skipped)"
        claude_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
    else:
        claude_md.write_text(snippet, encoding="utf-8")

    return True, f"openCodeMemory rules written to {claude_md}"


def configure_mcp_global() -> tuple[bool, str]:
    """Register the MCP server with Claude Code at user scope."""
    try:
        result = subprocess.run(
            [
                "claude", "mcp", "add",
                "--scope", "user",
                "opencodememory",
                "--",
                "uv", "run", "python", "-m", "ocm.server",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, "MCP server registered with Claude Code (user scope)"
        return False, f"claude mcp add failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "claude command not found"
    except Exception as e:
        return False, str(e)


def configure_hooks_global() -> tuple[bool, str]:
    """Write hook configuration to ~/.claude/settings.json."""
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    hooks = settings.setdefault("hooks", {})

    for event, new_entries in _make_hook_config().items():
        if event not in hooks:
            hooks[event] = new_entries
        else:
            existing_cmds = {
                inner.get("command")
                for entry in hooks[event] if isinstance(entry, dict)
                for inner in entry.get("hooks", []) if isinstance(inner, dict)
            }
            for entry in new_entries:
                entry_cmds = {
                    inner.get("command")
                    for inner in entry.get("hooks", []) if isinstance(inner, dict)
                }
                if not entry_cmds.issubset(existing_cmds):
                    hooks[event].append(entry)

    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return True, f"Hook configuration written to {settings_path}"


def inject_rules_global() -> tuple[bool, str]:
    """Append the openCodeMemory rule block to ~/.claude/CLAUDE.md."""
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)

    if RULE_SNIPPET_PATH.exists():
        snippet = RULE_SNIPPET_PATH.read_text(encoding="utf-8")
    else:
        snippet = _fallback_snippet()

    if claude_md.exists():
        existing = claude_md.read_text(encoding="utf-8")
        if "openCodeMemory" in existing:
            return True, "~/.claude/CLAUDE.md already contains openCodeMemory rules (skipped)"
        claude_md.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
    else:
        claude_md.write_text(snippet, encoding="utf-8")

    return True, f"openCodeMemory rules written to {claude_md}"


def _command_exists(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _fallback_snippet() -> str:
    return (
        "## openCodeMemory\n\n"
        "Use `ocm__checkpoint` to save session state. "
        "Use `ocm__search_sessions` to find previous sessions.\n"
    )
