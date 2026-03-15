#!/bin/bash
# Fires on UserPromptSubmit. Reads session context from stdin JSON.
exec uv run --directory "$(dirname "$0")/../.." python -m ocm.hooks.handler session-start --tool claude-code
