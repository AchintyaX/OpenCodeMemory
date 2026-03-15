#!/bin/bash
# Fires on Stop. Reads session context from stdin JSON.
exec uv run --directory "$(dirname "$0")/../.." python -m ocm.hooks.handler session-end --tool claude-code
