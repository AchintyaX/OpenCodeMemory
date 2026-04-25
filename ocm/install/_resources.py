from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path

SENTINEL_START = "<!-- BEGIN openCodeMemory -->"
SENTINEL_END = "<!-- END openCodeMemory -->"


def read_rule(name: str) -> str:
    return (files("ocm.rules") / name).read_text(encoding="utf-8")


def _safe_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _safe_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def inject_text_block(target: Path, snippet: str) -> tuple[bool, str]:
    """Append or update an OCM sentinel-wrapped block in a text file."""
    wrapped = f"{SENTINEL_START}\n{snippet.strip()}\n{SENTINEL_END}"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if SENTINEL_START in existing and SENTINEL_END in existing:
            start = existing.index(SENTINEL_START)
            end = existing.index(SENTINEL_END) + len(SENTINEL_END)
            updated = existing[:start].rstrip() + "\n\n" + wrapped + "\n" + existing[end:].lstrip()
            _safe_write_text(target, updated)
            return True, f"Updated openCodeMemory rules in {target}"
        elif "openCodeMemory" in existing:
            return (
                True,
                f"{target} already has openCodeMemory content (no markers). "
                "Re-run with --force-rules to refresh.",
            )
        else:
            _safe_write_text(target, existing.rstrip() + "\n\n" + wrapped + "\n")
            return True, f"openCodeMemory rules written to {target}"
    else:
        _safe_write_text(target, wrapped + "\n")
        return True, f"openCodeMemory rules written to {target}"


def remove_text_block(target: Path) -> tuple[bool, str]:
    """Remove the OCM sentinel-wrapped block from a text file."""
    if not target.exists():
        return True, f"{target} not found (skipped)"
    existing = target.read_text(encoding="utf-8")
    if SENTINEL_START not in existing or SENTINEL_END not in existing:
        return True, f"No openCodeMemory block found in {target} (skipped)"
    start = existing.index(SENTINEL_START)
    end = existing.index(SENTINEL_END) + len(SENTINEL_END)
    cleaned = existing[:start].rstrip() + "\n" + existing[end:].lstrip()
    _safe_write_text(target, cleaned)
    return True, f"Removed openCodeMemory block from {target}"
