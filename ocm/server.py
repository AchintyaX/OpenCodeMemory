from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ocm.tools import checkpoint, search, session

mcp = FastMCP("openCodeMemory")

mcp.tool()(checkpoint.ocm__checkpoint)
mcp.tool()(search.ocm__search_sessions)
mcp.tool()(session.ocm__list_sessions)
mcp.tool()(session.ocm__get_session_files)


def run_http(host: str, port: int, project_root: Path | None) -> None:
    """Pre-bind the project DB and start the streamable-http server."""
    import os
    if project_root is not None:
        os.environ["OCM_PROJECT_DIR"] = str(project_root)

    from ocm.storage.db import Database
    db = Database.for_project()
    checkpoint._init(db)
    search._init(db)
    session._init(db)

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
