#!/bin/bash
# Create the project-local Python environment (.venv) and install dependencies.
# Picks the first Python 3.11+ it finds: mise's, then python3.13/3.12/3.11/python3.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

PY=""
for candidate in "$HOME/.local/share/mise/installs/python/3/bin/python3" \
                 "$(command -v python3.13 || true)" "$(command -v python3.12 || true)" \
                 "$(command -v python3.11 || true)" "$(command -v python3 || true)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    # Resolve symlinks (mise's "3" alias moves between versions), so the venv
    # keeps pointing at a real interpreter.
    PY="$("$candidate" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "setup.sh: need Python 3.11 or newer. Install one (brew install python@3.12, or mise use -g python@3.12) and re-run." >&2
  exit 1
fi

# Rebuild the venv if it's missing or its interpreter no longer runs.
if ! "$DIR/.venv/bin/python" -c '' 2>/dev/null; then
  rm -rf "$DIR/.venv"
  "$PY" -m venv "$DIR/.venv"
fi
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"
echo "ready: $DIR/.venv ($("$DIR/.venv/bin/python" --version))"
