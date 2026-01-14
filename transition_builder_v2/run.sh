#!/usr/bin/env bash
# Run the Song Transition Preview App

cd "$(dirname "$0")"

# Use parent directory's venv python
VENV_PYTHON="../.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found at $VENV_PYTHON"
    echo "Please create a virtual environment in the parent directory or update this script."
    exit 1
fi

$VENV_PYTHON -m app.main
