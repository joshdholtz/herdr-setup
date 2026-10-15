#!/bin/bash
# Install the workstreams tool: Python env, `ws` on PATH, the agent skill,
# and the herdr plugin. Safe to run again. Does not touch your herdr config.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

"$DIR/setup.sh"

mkdir -p "$HOME/.local/bin"
ln -sfn "$DIR/ws" "$HOME/.local/bin/ws"
echo "linked: ~/.local/bin/ws"

mkdir -p "$HOME/.agents/skills"
ln -sfn "$DIR/skills/workstream" "$HOME/.agents/skills/workstream"
if [ -d "$HOME/.claude" ]; then
  mkdir -p "$HOME/.claude/skills"
  ln -sfn "$HOME/.agents/skills/workstream" "$HOME/.claude/skills/workstream"
fi
echo "linked: workstream skill"

if command -v herdr >/dev/null; then
  herdr plugin link "$DIR" >/dev/null && echo "linked: herdr plugin 'workstreams'"
fi

cat <<MSG

Done. Optional next steps (see README):
  - herdr config:   cp herdr/config.toml ~/.config/herdr/config.toml
  - shell:          add shell/zshrc.snippet to ~/.zshrc
  - agent context:  add the SessionStart hook for Claude Code / Codex
MSG
