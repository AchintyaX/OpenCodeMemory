#!/bin/bash
# Fires on sessionStart. Reads session context from stdin JSON.
exec ocm-hook session-start --tool cursor
