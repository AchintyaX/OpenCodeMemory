#!/bin/bash
# Fires on afterFileEdit. Reads file context from stdin JSON.
exec ocm-hook file-edited --tool cursor
