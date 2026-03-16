#!/bin/bash
# UserPromptSubmit: inject session_id into Claude's context (no Python/uv needed)
SESSION_ID="${CLAUDE_SESSION_ID:-}"
echo "{\"additionalContext\": \"openCodeMemory session_id: ${SESSION_ID}. Call ocm__checkpoint with this session_id as your first tool use if starting a new session.\"}"
