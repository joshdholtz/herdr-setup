#!/bin/bash
# Install the workstreams tool: Python env, `ws` on PATH, the agent skill, and
# the herdr plugin. Safe to re-run (re-run it if you move this folder).
# Does not touch your herdr config, shell config, or Claude/Codex settings.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

link() {  # link <source> <target>: symlink, refusing to replace a real file or folder
  if [ -e "$2" ] && [ ! -L "$2" ]; then
    echo "install.sh: $2 exists and is not a symlink. Move it aside and re-run." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
  echo "linked: $2"
}

"$DIR/setup.sh"

link "$DIR/ws" "$HOME/.local/bin/ws"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to your PATH so agents can run \`ws\`." ;; esac

link "$DIR/skills/workstream" "$HOME/.agents/skills/workstream"
[ -d "$HOME/.claude" ] && link "$HOME/.agents/skills/workstream" "$HOME/.claude/skills/workstream"
[ -d "$HOME/.codex" ] && link "$HOME/.agents/skills/workstream" "$HOME/.codex/skills/workstream"

if ! command -v herdr >/dev/null; then
  echo "herdr not found: skipped the herdr plugin. Install herdr (https://herdr.dev), then re-run ./install.sh."
elif herdr plugin link "$DIR" >/dev/null; then
  echo "linked: herdr plugin 'workstreams'"
else
  echo "herdr plugin link failed. Is the herdr server running? Then run: herdr plugin link \"$DIR\"" >&2
fi

echo
echo "Done. Next steps are in README.md under \"Optional: my herdr setup\" and \"Let agents see the workstream\"."
