from __future__ import annotations

import json
from pathlib import Path

CURSOR_MCP_CONFIG = {
    "mcpServers": {
        "opencodememory": {
            "command": "uv",
            "args": ["run", "python", "-m", "ocm.server"],
        }
    }
}

CURSOR_HOOKS_CONFIG = {
    "version": 1,
    "hooks": {
        "sessionStart": [
            {"command": "ocm-hook session-start --tool cursor"}
        ],
        "afterFileEdit": [
            {"command": "ocm-hook file-edited --tool cursor"}
        ],
        "sessionEnd": [
            {"command": "ocm-hook session-end --tool cursor"}
        ],
    },
}

RULE_SNIPPET_PATH = Path(__file__).parent.parent.parent / "rules" / "cursorrules.snippet"


def is_installed() -> bool:
    """Check if Cursor is available on the system."""
    return (
        _command_exists("cursor")
        or (Path.home() / ".cursor").exists()
    )


def configure_mcp(project_root: Path) -> tuple[bool, str]:
    """Create or update .cursor/mcp.json."""
    cursor_dir = project_root / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"

    existing: dict = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    servers = existing.setdefault("mcpServers", {})
    servers["opencodememory"] = CURSOR_MCP_CONFIG["mcpServers"]["opencodememory"]

    mcp_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return True, f"MCP configuration written to {mcp_path}"


def configure_hooks(project_root: Path) -> tuple[bool, str]:
    """Create or update .cursor/hooks.json."""
    cursor_dir = project_root / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    hooks_path = cursor_dir / "hooks.json"

    existing: dict = {}
    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    existing.setdefault("version", 1)
    hooks = existing.setdefault("hooks", {})

    for event, new_hooks in CURSOR_HOOKS_CONFIG["hooks"].items():
        if event not in hooks:
            hooks[event] = new_hooks
        else:
            existing_cmds = {
                h.get("command") for h in hooks[event] if isinstance(h, dict)
            }
            for h in new_hooks:
                if h.get("command") not in existing_cmds:
                    hooks[event].append(h)

    hooks_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return True, f"Hook configuration written to {hooks_path}"


def inject_rules(project_root: Path) -> tuple[bool, str]:
    """Append the openCodeMemory rule block to .cursorrules."""
    cursorrules = project_root / ".cursorrules"

    if RULE_SNIPPET_PATH.exists():
        snippet = RULE_SNIPPET_PATH.read_text(encoding="utf-8")
    else:
        snippet = "## openCodeMemory\nUse ocm__checkpoint and ocm__search_sessions.\n"

    if cursorrules.exists():
        existing = cursorrules.read_text(encoding="utf-8")
        if "openCodeMemory" in existing:
            return True, ".cursorrules already contains openCodeMemory rules (skipped)"
        cursorrules.write_text(existing.rstrip() + "\n\n" + snippet, encoding="utf-8")
    else:
        cursorrules.write_text(snippet, encoding="utf-8")

    return True, f"openCodeMemory rules written to {cursorrules}"


CURSOR_MCP_CONFIG_GLOBAL = {
    "mcpServers": {
        "opencodememory": {
            "command": "uv",
            "args": ["run", "python", "-m", "ocm.server"],
        }
    }
}


def configure_mcp_global() -> tuple[bool, str]:
    """Create or update ~/.cursor/mcp.json (no OCM_PROJECT_DIR — uses global DB)."""
    cursor_dir = Path.home() / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_path = cursor_dir / "mcp.json"

    existing: dict = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    servers = existing.setdefault("mcpServers", {})
    servers["opencodememory"] = CURSOR_MCP_CONFIG_GLOBAL["mcpServers"]["opencodememory"]

    mcp_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return True, f"MCP configuration written to {mcp_path}"


def configure_hooks_global() -> tuple[bool, str]:
    """Create or update ~/.cursor/hooks.json."""
    cursor_dir = Path.home() / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    hooks_path = cursor_dir / "hooks.json"

    existing: dict = {}
    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text())
        except json.JSONDecodeError:
            existing = {}

    existing.setdefault("version", 1)
    hooks = existing.setdefault("hooks", {})

    for event, new_hooks in CURSOR_HOOKS_CONFIG["hooks"].items():
        if event not in hooks:
            hooks[event] = new_hooks
        else:
            existing_cmds = {
                h.get("command") for h in hooks[event] if isinstance(h, dict)
            }
            for h in new_hooks:
                if h.get("command") not in existing_cmds:
                    hooks[event].append(h)

    hooks_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return True, f"Hook configuration written to {hooks_path}"


def inject_rules_global() -> tuple[bool, str]:
    """No-op: Cursor has no well-defined global rules file."""
    return (
        True,
        "Rules injection not supported globally for Cursor "
        "(add manually to .cursorrules in each project if needed)",
    )


def _command_exists(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
