#!/usr/bin/env bash
# Run the Song Transition Preview App

cd "$(dirname "$0")"

uv run --extra tui python -m app.main
