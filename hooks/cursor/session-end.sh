#!/bin/bash
# Fires on sessionEnd. Reads session context from stdin JSON.
exec ocm-hook session-end --tool cursor
