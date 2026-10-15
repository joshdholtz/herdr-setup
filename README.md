# herdr-setup

My [herdr](https://herdr.dev) setup: a config, a few plugins, and **workstreams**, a home page per herdr space that gathers everything for one piece of work (your PRs across repos, Linear issues, Slack threads, agents) and tells you what needs you.

## The idea

One herdr space = one workstream. Each space gets a `home` tab:

- **Home** lists what you linked to the workstream, plus PRs for branches checked out in the space's panes (linked automatically): your PRs grouped by repo with live CI and review status, Linear issues, Slack channels and threads, and the agents running in the space. The top line says whether anything needs you.
- **Suggestions** is an inbox. Claude searches GitHub, Linear and Slack using a short brief you write, and you link (`y`) or dismiss (`n`) what it finds. It only suggests your own open PRs. PR links that show up in a pane's output also land here.
- **Agents see it too.** `ws context` prints the workstream as markdown, and a SessionStart hook hands it to Claude Code or Codex when they start in that space.

It never manages work. GitHub, Linear and Slack stay the source of truth; a workstream is a list of links plus a brief.

## What's here

| Path | What |
|---|---|
| `ws`, `workstreams/` | The workstream tool (Python + [Textual](https://textual.textualize.io)) |
| `herdr-plugin.toml` | herdr plugin: "Workstream: open home page" |
| `skills/workstream/` | Agent skill explaining `ws` to Claude Code and Codex |
| `herdr/config.toml` | My herdr config: Catppuccin Macchiato with a peach accent, tmux-style keys, sidebar layout |
| `herdr/automatic-rename.config.sh` | Settings for the herdr-automatic-rename plugin |
| `shell/zshrc.snippet` | Pane background color + tab rename hook |

## Requirements

- **Core:** macOS or Linux, [herdr](https://herdr.dev) 0.9+ (`herdr --version`), `git`, [`gh`](https://cli.github.com) logged in as the account that authors your PRs (`gh auth status`), Python 3.11+, and `~/.local/bin` on your `PATH`.
- **For Suggestions:** the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) logged in with a claude.ai account, with the claude.ai **Slack** and **Linear** connectors enabled. Locally configured Slack or Linear MCP servers are not used. GitHub search works without either connector.
- **For my herdr config and plugins (optional):** Node 18+ (herdr-radar), Rust/cargo (herdr-navigator builds from source), `lazygit` and `btop` (popup keys), and a terminal that can show herdr-radar's icon font (Ghostty or Kitty set it up automatically).

## Install

1. Install herdr first, then:

   ```bash
   git clone https://github.com/joshdholtz/herdr-setup ~/Developer/herdr-setup
   cd ~/Developer/herdr-setup
   ./install.sh
   ```

   This builds `.venv`, links `ws` into `~/.local/bin`, links the skill into `~/.agents/skills` (and `~/.claude/skills`, `~/.codex/skills` when those exist), and registers the herdr plugin. It changes nothing else. Re-run it if you move the folder.

2. Optional settings, in `~/.config/herdr-workstreams/settings.toml` (see [Settings](#settings)).

3. Optional: my herdr setup and agent hooks, below.

### Optional: my herdr setup

Plugins first, because the config binds keys to them:

```bash
herdr plugin install qu8n/herdr-automatic-rename --yes     # tmux-style tab names
herdr plugin install hhdebb/herdr-radar --yes              # readable agents sidebar
herdr plugin install thanhdat77/herdr-navigator --yes      # ctrl+b then t: jump anywhere

mkdir -p ~/.config/herdr-automatic-rename
cp herdr/automatic-rename.config.sh ~/.config/herdr-automatic-rename/config.sh

# Replaces your herdr config; this keeps a backup first.
[ -f ~/.config/herdr/config.toml ] && cp ~/.config/herdr/config.toml ~/.config/herdr/config.toml.bak
cp herdr/config.toml ~/.config/herdr/config.toml
herdr server reload-config

# Adds the pane color and rename hook to ~/.zshrc once (safe to re-run).
grep -q 'herdr-setup: add to ~/.zshrc' ~/.zshrc 2>/dev/null || cat shell/zshrc.snippet >> ~/.zshrc
```

The config already contains herdr-radar's sidebar and theme tables, so a radar notice that a block was skipped is expected.

### Let agents see the workstream

**Claude Code:** in `~/.claude/settings.json`, add this object to the `hooks.SessionStart` array (create `"hooks": {"SessionStart": []}` if it's missing; keep any entries already there):

```json
{ "matcher": "^(startup|clear|compact)$",
  "hooks": [{ "type": "command", "command": "$HOME/.local/bin/ws context --hook", "timeout": 15 }] }
```

**Codex:** make sure `~/.codex/config.toml` has `hooks = true` under `[features]`. Then add the same object without `"matcher"` to the `hooks.SessionStart` array in `~/.codex/hooks.json`, whose top level is `{"hooks": {"SessionStart": [ ... ]}}`. Codex asks you to approve a new hook the next time it starts; only you can approve it.

The hook prints nothing outside a herdr space that has a workstream, and gives up on live PR status after 8 seconds rather than delaying startup.

## Use

1. **Create a space** in herdr and name it after the work (for example "Checkout redesign"). The workstream is named after the space label.
2. **Open its home page:** `ctrl+b` then `alt+h` (with my config), or the "Workstream: open home page" plugin action, or run `ws open` in a pane of that space. The `home` tab becomes the first tab.
3. **Write the brief** with `b`: a few lines on what this workstream is about. Saving it (`ctrl+s`) starts finding suggestions.
4. **Review suggestions** with `s`. Opening Suggestions the first time also starts a search. Each search uses Claude usage and takes about 25 to 80 seconds.

**Home keys:** `enter` jump to the item's pane, or open its link · `s` suggestions · `a` paste a link (PR, Linear issue or project, Slack channel or thread, `#channel`, `ABC-123`) · `b` edit the brief · `x` remove (`u` undoes within 5 seconds) · `j`/`k` move · `q` quit

**Suggestions keys:** `←`/`→` or `h`/`l` switch between PRs, Linear and Slack · `y` link · `n` dismiss (it won't come back) · `enter` open to check · `f` find more · `esc` back

**CLI:** run `ws` with no arguments for help. Commands: `home`, `open`, `add <link>`, `rm <link>`, `list`, `find`, `brief`, `context` (`--json`, `--hook`).

**Data** lives in `~/.config/herdr-workstreams/<slug>/`, where `<slug>` is the space label lowercased with other characters turned into `-` ("Checkout redesign" becomes `checkout-redesign/`). It holds `links.json`, `brief.md`, and `find-*.log` from the last search. Renaming a space starts a new folder; rename the old folder to the new slug to keep it. Set `WS_DATA_DIR` to move the whole thing.

## Settings

Everything works without settings. Optional `~/.config/herdr-workstreams/settings.toml`:

```toml
org = "YourGitHubOrg"          # used when a brief has no "Org:" line
linear_workspace = "yourteam"  # turns bare IDs like ABC-123 into links
slack_workspace = "yourteam"   # rewrites app.slack.com/client/... links to yourteam.slack.com
```

- Without `org` (and no `Org:` line in the brief), PR search covers all your PRs in every org.
- Without `linear_workspace`, a bare `ABC-123` is kept but has no link. Pasting the full Linear URL always works.
- Without `slack_workspace`, `app.slack.com/client/...` links are kept as they are.
- Environment variables `WS_ORG`, `WS_LINEAR_WORKSPACE` and `WS_SLACK_WORKSPACE` override the file.

## How finding works

`f` runs three read-only `claude -p` agents at once, one each for GitHub, Linear and Slack, with your brief. Slack is used as context: PRs and Linear issues posted there become suggestions. Every PR suggestion is checked against GitHub and kept only if it's yours and open.

- It removes `ANTHROPIC_API_KEY` from the agents' environment so `claude` uses your claude.ai login and its connectors. If you only have an API key, set `WS_FIND_KEEP_API_KEY=1` (GitHub search still works; Slack and Linear won't).
- If [rtk](https://github.com/rtk-ai/rtk) is installed, GitHub searches go through `rtk proxy gh` so rtk's hook doesn't compress the results.

## Credits

Built on [herdr](https://herdr.dev), with plugins by [qu8n](https://github.com/qu8n/herdr-automatic-rename), [hhdebb](https://github.com/hhdebb/herdr-radar) and [thanhdat77](https://github.com/thanhdat77/herdr-navigator).
