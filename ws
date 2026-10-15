#!/bin/bash
# Launcher: runs the workstreams package with the project's own Python (.venv,
# built by setup.sh). Works through symlinks such as ~/.local/bin/ws.
SELF="$(realpath "$0" 2>/dev/null || python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$0")"
DIR="$(cd "$(dirname "$SELF")" && pwd)"
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ws: run $DIR/setup.sh first" >&2
  exit 1
fi
export WS_LAUNCHER="$DIR/ws"
PYTHONPATH="$DIR" exec "$PY" -m workstreams "$@"
