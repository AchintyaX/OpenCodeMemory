#!/bin/bash
# Fires on PostToolUse for Write|Edit|MultiEdit.
# Reads tool context (including file_path) from stdin JSON.
exec uv run --directory "$(dirname "$0")/../.." python -m ocm.hooks.handler file-edited --tool claude-code
