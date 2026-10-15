# herdr-setup

My [herdr](https://herdr.dev) setup: a config, a few plugins, and **workstreams**, a home page per herdr space that gathers everything for one piece of work (PRs across repos, Linear issues, Slack threads, agents) and tells you what needs you.

## The idea

One herdr space = one workstream. Each space gets a `home` tab:

- **Home** lists only what you linked to the workstream: your PRs grouped by repo with live CI and review status, Linear issues, Slack channels and threads, and the agents running in the space. The top line says whether anything needs you.
- **Suggestions** is an inbox. Claude searches GitHub, Linear and Slack using a short brief you write, and you link (`y`) or dismiss (`n`) what it finds. It only suggests your own open PRs.
- **Agents see it too.** `ws context` prints the workstream as markdown, and a SessionStart hook hands it to Claude Code or Codex when they start in that space.

It never manages work. GitHub, Linear and Slack stay the source of truth; a workstream is a list of links plus a brief.

## What's here

| Path | What |
|---|---|
| `ws`, `workstreams/` | The workstream tool (Python + [Textual](https://textual.textualize.io)) |
| `herdr-plugin.toml` | herdr plugin: "Workstream: open home page" |
| `skills/workstream/` | Agent skill explaining `ws` to Claude Code / Codex |
| `herdr/config.toml` | My herdr config: Catppuccin Macchiato with a peach accent, tmux-style keys, sidebar layout |
| `herdr/automatic-rename.config.sh` | Settings for herdr-automatic-rename |
| `shell/zshrc.snippet` | Pane background color + tab rename hook |

## Install

Needs herdr 0.9+, `gh` logged in, Python 3.11+, and Claude Code with the Slack and Linear connectors for finding suggestions.

```bash
git clone https://github.com/joshdholtz/herdr-setup ~/Developer/herdr-setup
cd ~/Developer/herdr-setup
./install.sh
```

That sets up `ws`, the skill and the plugin. Then, optionally:

```bash
cp herdr/config.toml ~/.config/herdr/config.toml          # my herdr config
cat shell/zshrc.snippet >> ~/.zshrc                        # pane color + rename hook
herdr plugin install qu8n/herdr-automatic-rename --yes     # tmux-style tab names
herdr plugin install hhdebb/herdr-radar --yes              # readable agents sidebar
herdr plugin install thanhdat77/herdr-navigator --yes      # ctrl+b t to jump anywhere
herdr server reload-config
```

### Settings

Optional `~/.config/herdr-workstreams/settings.toml`:

```toml
org = "YourGitHubOrg"          # default org to search for your PRs
linear_workspace = "yourteam"  # turns bare IDs like ABC-123 into links
slack_workspace = "yourteam"
```

### Let agents see the workstream

Add to `~/.claude/settings.json` (and the same entry to `~/.codex/hooks.json` for Codex):

```json
"SessionStart": [
  { "matcher": "^(startup|clear|compact)$",
    "hooks": [{ "type": "command", "command": "~/.local/bin/ws context --hook", "timeout": 15 }] }
]
```

It prints nothing outside a herdr space that has a workstream. Codex asks you to approve new hooks once.

## Use

Open a space's home page with `ctrl+b` then `alt+h`.

**Home:** `enter` jump to the item's pane (or open its link) · `s` suggestions · `a` paste a link (PR, Linear, Slack, `#channel`, `ABC-123`) · `b` edit the brief · `x` remove, `u` undo · `j`/`k` move · `q` quit

**Suggestions:** `←`/`→` switch PRs, Linear, Slack · `y` link · `n` dismiss · `enter` open to check · `f` find more · `esc` back

**CLI:** `ws home | open | add <link> | rm <link> | list | find | brief | context`

Data lives in `~/.config/herdr-workstreams/<space-name>/` (`links.json`, `brief.md`). Renaming a space starts a new workstream folder; rename the folder to match.

## How finding works

`f` runs three read-only `claude -p` agents at once, one each for GitHub, Linear and Slack, with your brief. Slack is used as context: PRs and Linear issues posted there become suggestions. Every PR suggestion is checked against GitHub and kept only if it's yours and open. A run takes about 25 to 80 seconds. It runs Claude without `ANTHROPIC_API_KEY` so your claude.ai login and its connectors are used.

## Credits

Built on [herdr](https://herdr.dev), with plugins by [qu8n](https://github.com/qu8n/herdr-automatic-rename), [hhdebb](https://github.com/hhdebb/herdr-radar) and [thanhdat77](https://github.com/thanhdat77/herdr-navigator).
