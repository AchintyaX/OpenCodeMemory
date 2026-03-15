from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ocm.tools import checkpoint, search, session

mcp = FastMCP("openCodeMemory")

# Register MCP tools — DB is resolved lazily via each tool's _get_db()
mcp.tool()(checkpoint.ocm__checkpoint)
mcp.tool()(search.ocm__search_sessions)
mcp.tool()(session.ocm__list_sessions)
mcp.tool()(session.ocm__get_session_files)


if __name__ == "__main__":
    mcp.run()
