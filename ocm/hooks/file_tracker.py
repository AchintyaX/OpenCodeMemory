from __future__ import annotations

import json
import time
from pathlib import Path


def journal_path(session_id: str, ocm_dir: Path) -> Path:
    return ocm_dir / f"active_{session_id}.jsonl"


def append_file(session_id: str, file_path: str, ocm_dir: Path, tool_name: str = "Edit") -> None:
    """Append a file-edit event to the session journal."""
    entry = {
        "path": file_path,
        "tool": tool_name,
        "ts": int(time.time()),
    }
    jpath = journal_path(session_id, ocm_dir)
    with open(jpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def flush_journal(session_id: str, ocm_dir: Path) -> list[dict]:
    """Read and delete the journal file, returning all entries."""
    jpath = journal_path(session_id, ocm_dir)
    if not jpath.exists():
        return []

    entries = []
    try:
        for line in jpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    finally:
        try:
            jpath.unlink()
        except OSError:
            pass

    return entries
