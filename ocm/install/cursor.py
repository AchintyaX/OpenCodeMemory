from __future__ import annotations

import json
import re
from pathlib import Path

from ocm.install._resources import (
    inject_text_block,
    read_rule,
    remove_text_block,
    _safe_write_json,
    _safe_write_text,
)

_CURSOR_MCP_ENTRY = {
    "command": "uv",
    "args": ["run", "python", "-m", "ocm.server"],
}


def is_installed() -> bool:
    return _command_exists("cursor") or (Path.home() / ".cursor").exists()


def configure_mcp(project_root: Path) -> tuple[bool, str]:
    return _write_mcp(project_root / ".cursor" / "mcp.json")


def configure_mcp_global() -> tuple[bool, str]:
    cursor_dir = Path.home() / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    return _write_mcp(cursor_dir / "mcp.json")


def configure_hooks(project_root: Path, profile: str = "none") -> tuple[bool, str]:
    if profile == "none":
        return True, "Cursor hooks are not used (skipped)"
    cursor_dir = project_root / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    return _merge_cursor_hooks(cursor_dir / "hooks.json", profile, tool="cursor")


def configure_hooks_global(profile: str = "none") -> tuple[bool, str]:
    if profile == "none":
        return True, "Cursor hooks are not used globally (skipped)"
    cursor_dir = Path.home() / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    return _merge_cursor_hooks(cursor_dir / "hooks.json", profile, tool="cursor")


def inject_rules(project_root: Path) -> tuple[bool, str]:
    snippet = _rule_snippet()
    return inject_text_block(project_root / ".cursorrules", snippet)


def inject_mdc_rule(project_root: Path) -> tuple[bool, str]:
    rules_dir = project_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    mdc_path = rules_dir / "ocm-checkpoint.mdc"

    shipped = read_rule("ocm-checkpoint.mdc")
    shipped_version = _parse_mdc_version(shipped)

    if mdc_path.exists():
        existing_version = _parse_mdc_version(mdc_path.read_text(encoding="utf-8"))
        if existing_version == shipped_version:
            return True, ".cursor/rules/ocm-checkpoint.mdc is up to date (skipped)"
        _safe_write_text(mdc_path, shipped)
        return True, f"Updated .cursor/rules/ocm-checkpoint.mdc to version {shipped_version}"

    _safe_write_text(mdc_path, shipped)
    return True, f"Cursor rule written to {mdc_path}"


def inject_rules_global() -> tuple[bool, str]:
    return (
        True,
        "Rules injection not supported globally for Cursor "
        "(add manually to .cursorrules in each project if needed)",
    )


def remove_hooks(project_root: Path) -> tuple[bool, str]:
    return _remove_cursor_hooks(project_root / ".cursor" / "hooks.json")


def remove_hooks_global() -> tuple[bool, str]:
    return _remove_cursor_hooks(Path.home() / ".cursor" / "hooks.json")


def remove_rules(project_root: Path) -> tuple[bool, str]:
    return remove_text_block(project_root / ".cursorrules")


def remove_mdc_rule(project_root: Path) -> tuple[bool, str]:
    mdc_path = project_root / ".cursor" / "rules" / "ocm-checkpoint.mdc"
    if not mdc_path.exists():
        return True, ".cursor/rules/ocm-checkpoint.mdc not found (skipped)"
    shipped_version = _parse_mdc_version(read_rule("ocm-checkpoint.mdc"))
    existing_version = _parse_mdc_version(mdc_path.read_text(encoding="utf-8"))
    if existing_version != shipped_version:
        return True, ".cursor/rules/ocm-checkpoint.mdc version mismatch — not removing (may be user-modified)"
    mdc_path.unlink()
    return True, f"Removed {mdc_path}"


# --- Internal helpers ---

def _rule_snippet() -> str:
    try:
        return read_rule("cursorrules.snippet")
    except Exception:
        return "## openCodeMemory\nUse ocm__checkpoint and ocm__search_sessions.\n"


def _parse_mdc_version(content: str) -> str:
    m = re.search(r"^ocm-version:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _write_mcp(mcp_path: Path) -> tuple[bool, str]:
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
        except json.JSONDecodeError:
            return False, (
                f"Refusing to modify malformed JSON at {mcp_path}. "
                "Fix the file and re-run."
            )
    existing.setdefault("mcpServers", {})["opencodememory"] = _CURSOR_MCP_ENTRY
    _safe_write_json(mcp_path, existing)
    return True, f"MCP configuration written to {mcp_path}"


def _make_hook_config(profile: str, tool: str) -> dict:
    if profile == "none":
        return {}
    hooks: dict = {
        "sessionStart": [{"command": f"ocm-hook session-start --tool {tool}"}],
        "preToolUse": [{"command": "ocm-hook pre-tool-use --threshold 5"}],
        "postToolUse": [{"command": f"ocm-hook post-tool-use --tool {tool} --threshold 5"}],
        "stop": [{"command": f"ocm-hook session-end --tool {tool}"}],
    }
    if profile == "full":
        hooks["afterFileEdit"] = [{"command": f"ocm-hook file-edited --tool {tool}"}]
    return {"version": 1, "hooks": hooks}


def _merge_cursor_hooks(hooks_path: Path, profile: str, tool: str) -> tuple[bool, str]:
    existing: dict = {}
    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text())
        except json.JSONDecodeError:
            return False, (
                f"Refusing to modify malformed JSON at {hooks_path}. "
                "Fix the file and re-run."
            )
    result = dict(existing)
    result["version"] = 1
    hooks = result.setdefault("hooks", {})
    new_cfg = _make_hook_config(profile, tool)
    for event, entries in new_cfg.get("hooks", {}).items():
        if event not in hooks:
            hooks[event] = list(entries)
            continue
        existing_pairs = {(e.get("command"), e.get("matcher")) for e in hooks[event] if isinstance(e, dict)}
        for entry in entries:
            key = (entry.get("command"), entry.get("matcher"))
            if key not in existing_pairs:
                hooks[event].append(entry)
    _safe_write_json(hooks_path, result)
    return True, f"Cursor hooks configuration written to {hooks_path}"


def _remove_cursor_hooks(hooks_path: Path) -> tuple[bool, str]:
    if not hooks_path.exists():
        return True, f"{hooks_path} not found (skipped)"
    try:
        data = json.loads(hooks_path.read_text())
    except json.JSONDecodeError:
        return False, f"Cannot parse {hooks_path}"
    hooks = data.get("hooks", {})
    changed = False
    for event in list(hooks.keys()):
        filtered = [e for e in hooks[event] if "ocm-hook" not in e.get("command", "")]
        if len(filtered) != len(hooks[event]):
            changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]
    if changed:
        _safe_write_json(hooks_path, data)
    return True, f"Removed openCodeMemory hooks from {hooks_path}"


def _command_exists(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None
