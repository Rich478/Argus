#!/bin/bash
# Quick launcher for Argus
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ -d "$VENV" ]; then
    "$VENV/bin/python" "$SCRIPT_DIR/argus.py" "$@"
else
    echo "No .venv found. Creating one..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    "$VENV/bin/python" "$SCRIPT_DIR/argus.py" "$@"
fi
