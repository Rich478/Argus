#!/bin/bash
# Quick launcher for Argus
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "No .venv found. Creating one..."
    if ! python3 -m venv "$VENV"; then
        echo "Error: Failed to create virtual environment. Is python3 installed?"
        exit 1
    fi
    if ! "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"; then
        echo "Error: Failed to install dependencies."
        rm -rf "$VENV"
        exit 1
    fi
fi

"$VENV/bin/python" "$SCRIPT_DIR/argus.py" "$@"
