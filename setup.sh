#!/bin/bash
# Create the project-local Python environment and install dependencies.
# Uses mise's Python 3 when available, otherwise python3 on PATH (3.11+).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$HOME/.local/share/mise/installs/python/3/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
"$PY" -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"
echo "ready: $DIR/.venv"
