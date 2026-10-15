#!/bin/bash
# Launcher: runs the workstreams package with the project's own Python
# (.venv, built from mise's Python 3 by setup.sh). Never the system or Xcode one.
DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "ws: run $DIR/setup.sh first" >&2
  exit 1
fi
export WS_LAUNCHER="$DIR/ws"
PYTHONPATH="$DIR" exec "$PY" -m workstreams "$@"
