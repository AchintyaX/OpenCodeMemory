from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path

from ocm.install._resources import _safe_write_json

HOST = "127.0.0.1"
MCP_PATH = "/mcp"

_SERVER_JSON = "server.json"
_SERVER_PID = "server.pid"
_SERVER_LOG = "server.log"


@dataclass
class ServerConfig:
    scope: str             # "project" | "global"
    ocm_dir: Path          # <project>/.openCodeMemory  or  ~/.openCodeMemory
    project_root: Path | None
    db_path: Path
    host: str
    port: int
    url: str
    pid_path: Path         # <ocm_dir>/server.pid
    log_path: Path         # <ocm_dir>/server.log


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_or_create(ocm_dir: Path, scope: str, project_root: Path | None) -> ServerConfig:
    """Idempotent: read existing server.json or pick a free port and write one."""
    cfg_path = ocm_dir / _SERVER_JSON

    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            port = int(data["port"])
            url = data.get("url") or f"http://{HOST}:{port}{MCP_PATH}"
            return ServerConfig(
                scope=data.get("scope", scope),
                ocm_dir=ocm_dir,
                project_root=Path(data["project_root"]) if data.get("project_root") else project_root,
                db_path=ocm_dir / "memory.db",
                host=HOST,
                port=port,
                url=url,
                pid_path=ocm_dir / _SERVER_PID,
                log_path=ocm_dir / _SERVER_LOG,
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            pass  # corrupt — fall through to pick a fresh port

    port = _pick_free_port()
    url = f"http://{HOST}:{port}{MCP_PATH}"
    data = {
        "scope": scope,
        "host": HOST,
        "port": port,
        "url": url,
        "project_root": str(project_root) if project_root else None,
        "db_path": str(ocm_dir / "memory.db"),
    }
    ocm_dir.mkdir(parents=True, exist_ok=True)
    _safe_write_json(cfg_path, data)
    return ServerConfig(
        scope=scope,
        ocm_dir=ocm_dir,
        project_root=project_root,
        db_path=ocm_dir / "memory.db",
        host=HOST,
        port=port,
        url=url,
        pid_path=ocm_dir / _SERVER_PID,
        log_path=ocm_dir / _SERVER_LOG,
    )


def project_config(project_root: Path) -> ServerConfig:
    """Load (or pick+persist) the port config for a specific project root."""
    return _load_or_create(project_root / ".openCodeMemory", "project", project_root)


def global_config() -> ServerConfig:
    """Load (or pick+persist) the port config for the global store."""
    return _load_or_create(Path.home() / ".openCodeMemory", "global", None)


def resolve_for_cwd(cwd: Path | None = None) -> ServerConfig:
    """Walk up from cwd looking for .openCodeMemory/server.json; fall back to global."""
    start = (cwd or Path.cwd()).resolve()
    current = start
    while True:
        candidate = current / ".openCodeMemory" / _SERVER_JSON
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text())
                port = int(data["port"])
                url = data.get("url") or f"http://{HOST}:{port}{MCP_PATH}"
                ocm_dir = current / ".openCodeMemory"
                project_root = Path(data["project_root"]) if data.get("project_root") else current
                return ServerConfig(
                    scope=data.get("scope", "project"),
                    ocm_dir=ocm_dir,
                    project_root=project_root,
                    db_path=ocm_dir / "memory.db",
                    host=HOST,
                    port=port,
                    url=url,
                    pid_path=ocm_dir / _SERVER_PID,
                    log_path=ocm_dir / _SERVER_LOG,
                )
            except (KeyError, ValueError, json.JSONDecodeError):
                pass  # corrupt entry — keep walking
        parent = current.parent
        if parent == current:
            break
        current = parent
    return global_config()


def all_known() -> list[ServerConfig]:
    """Return configs for all known servers: global + every registered project."""
    results: list[ServerConfig] = []

    global_dir = Path.home() / ".openCodeMemory"
    if (global_dir / _SERVER_JSON).exists():
        results.append(global_config())

    registry_path = global_dir / "registry.json"
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text())
        except Exception:
            registry = []
        for entry in registry:
            pr_str = entry.get("project_root", "")
            if not pr_str:
                continue
            pr = Path(pr_str)
            if (pr / ".openCodeMemory" / _SERVER_JSON).exists():
                try:
                    results.append(project_config(pr))
                except Exception:
                    pass

    # Deduplicate by port
    seen: set[int] = set()
    deduped: list[ServerConfig] = []
    for cfg in results:
        if cfg.port not in seen:
            seen.add(cfg.port)
            deduped.append(cfg)
    return deduped
