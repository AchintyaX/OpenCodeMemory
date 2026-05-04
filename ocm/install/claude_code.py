from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ocm.install._resources import (
    inject_text_block,
    read_rule,
    remove_text_block,
    _safe_write_json,
)


def _hook_cmd() -> str:
    return (
        'SESSION_ID="${CLAUDE_SESSION_ID:-}"; '
        'echo "{\\"additionalContext\\": \\"openCodeMemory session_id: ${SESSION_ID}. '
        'Call ocm__checkpoint with this session_id as your first tool use if starting a new session.\\"}"'
    )


def _make_hook_config(profile: str = "minimal") -> dict:
    if profile == "none":
        return {}

    hooks: dict = {
        "SessionStart": [
            {"matcher": "", "hooks": [{"type": "command", "command": "ocm-hook session-start --tool claude-code"}]},
        ],
        "UserPromptSubmit": [
            {"matcher": "", "hooks": [{"type": "command", "command": _hook_cmd()}]},
        ],
        "PreToolUse": [
            {"matcher": ".*", "hooks": [{"type": "command", "command": "ocm-hook pre-tool-use --threshold 5"}]},
        ],
        "PostToolUse": [
            {"matcher": ".*", "hooks": [{"type": "command", "command": "ocm-hook post-tool-use --tool claude-code --threshold 5"}]},
        ],
        "Stop": [
            {"matcher": "", "hooks": [{"type": "command", "command": "ocm-hook session-end --tool claude-code"}]},
        ],
    }
    if profile == "full":
        hooks["PostToolUse"].append(
            {
                "matcher": "Write|Edit|MultiEdit",
                "hooks": [{"type": "command", "command": "ocm-hook file-edited --tool claude-code"}],
            }
        )
    return hooks


def is_installed() -> bool:
    return _command_exists("claude") or (Path.home() / ".claude").exists()


def configure_mcp(project_root: Path) -> tuple[bool, str]:
    from ocm.install.server_config import project_config
    url = project_config(project_root).url
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "--transport", "http", "--scope", "project",
             "opencodememory", url],
            capture_output=True, text=True, cwd=str(project_root),
        )
        if result.returncode == 0:
            return True, f"MCP server registered with Claude Code ({url})"
        return False, f"claude mcp add failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "claude command not found"
    except Exception as e:
        return False, str(e)


def configure_hooks(project_root: Path, profile: str = "minimal") -> tuple[bool, str]:
    if profile == "none":
        return True, "Claude hooks are disabled by profile (skipped)"
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"
    return _merge_hooks_into(settings_path, profile)


def configure_mcp_global() -> tuple[bool, str]:
    from ocm.install.server_config import global_config
    url = global_config().url
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "--transport", "http", "--scope", "user",
             "opencodememory", url],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True, f"MCP server registered with Claude Code user scope ({url})"
        return False, f"claude mcp add failed: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "claude command not found"
    except Exception as e:
        return False, str(e)


def configure_hooks_global(profile: str = "minimal") -> tuple[bool, str]:
    if profile == "none":
        return True, "Claude global hooks are disabled by profile (skipped)"
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.json"
    return _merge_hooks_into(settings_path, profile)


def inject_rules(project_root: Path) -> tuple[bool, str]:
    snippet = _rule_snippet()
    return inject_text_block(project_root / "CLAUDE.md", snippet)


def inject_rules_global() -> tuple[bool, str]:
    snippet = _rule_snippet()
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(exist_ok=True)
    return inject_text_block(claude_dir / "CLAUDE.md", snippet)


def remove_hooks(project_root: Path) -> tuple[bool, str]:
    settings_path = project_root / ".claude" / "settings.json"
    return _remove_ocm_hooks_from(settings_path)


def remove_hooks_global() -> tuple[bool, str]:
    settings_path = Path.home() / ".claude" / "settings.json"
    return _remove_ocm_hooks_from(settings_path)


def remove_rules(project_root: Path) -> tuple[bool, str]:
    return remove_text_block(project_root / "CLAUDE.md")


def remove_rules_global() -> tuple[bool, str]:
    return remove_text_block(Path.home() / ".claude" / "CLAUDE.md")


# --- Internal helpers ---

def _rule_snippet() -> str:
    try:
        return read_rule("CLAUDE.md.snippet")
    except Exception:
        return (
            "## openCodeMemory\n\n"
            "Use `ocm__checkpoint` to save session state. "
            "Use `ocm__search_sessions` to find previous sessions.\n"
        )


def _merge_hooks_into(settings_path: Path, profile: str) -> tuple[bool, str]:
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            return False, (
                f"Refusing to modify malformed JSON at {settings_path}. "
                "Fix the file and re-run."
            )

    hooks = settings.setdefault("hooks", {})
    for event, new_entries in _make_hook_config(profile).items():
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

    _safe_write_json(settings_path, settings)
    return True, f"Hook configuration written to {settings_path}"


def _remove_ocm_hooks_from(settings_path: Path) -> tuple[bool, str]:
    if not settings_path.exists():
        return True, f"{settings_path} not found (skipped)"
    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return False, f"Cannot parse {settings_path}"

    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        filtered = [
            entry for entry in hooks[event]
            if isinstance(entry, dict) and not all(
                "ocm-hook" in inner.get("command", "")
                for inner in entry.get("hooks", []) if isinstance(inner, dict)
            )
        ]
        if len(filtered) != len(hooks[event]):
            changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]
            changed = True

    if not hooks:
        settings.pop("hooks", None)

    if changed:
        _safe_write_json(settings_path, settings)
    return True, f"Removed openCodeMemory hooks from {settings_path}"


def _command_exists(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
