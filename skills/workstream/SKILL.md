---
name: workstream
description: Read the workstream for the current herdr space (the user's linked PRs, Linear issues, Slack threads and brief) with the `ws` CLI. Use when working inside a herdr pane and you need to know which PRs, issues or Slack threads belong to the current work, when the user mentions "the workstream", "my PRs for this", or the home page, or when you open, finish or find a PR, Linear issue or Slack thread that belongs to this work and should be linked.
---

# Workstream (`ws`)

The user organizes work in herdr: **one herdr space = one workstream** (for example "Checkout redesign"). Each space has a `home` tab, a read-only home page listing everything the user linked to that workstream: their PRs across repos (with live CI and review status), Linear issues and projects, and Slack channels and threads, plus a short brief describing the work.

`ws` is the command-line tool behind that page (source: github.com/joshdholtz/herdr-setup). Inside a herdr pane it knows the space from `HERDR_WORKSPACE_ID`; no arguments needed.

## When you start in a herdr space

If a "# Workstream: …" block is already in your context (a SessionStart hook adds it), you have the current list. Otherwise run:

```bash
ws context
```

It prints markdown: the brief, each PR with state, CI, review, and a `NEEDS YOU:` note when something needs the user, then Linear and Slack with links. Run it again whenever you need fresh status (it fetches live PR status from GitHub). `ws context --json` gives the raw links.

Outside herdr, or in a space with no workstream, `ws context` exits with "no workstream yet". That is normal; carry on without it.

## Using it

- Treat the listed items as the scope of the work. Read the linked Slack threads and Linear issues (with your Slack and Linear tools) for decisions and context before changing related code.
- Items are links, not copies: GitHub, Linear and Slack are the source of truth.
- Unreviewed suggestions are not part of the workstream; ignore them unless the user asks.

## Linking something new

When you open a PR for this work, or find an issue or thread that clearly belongs, link it:

```bash
ws add <url>
```

It accepts GitHub PR links, Linear issue or project links, Slack channel or thread links, `#channel-name`, and Linear IDs like `ABC-123`. Tell the user what you linked. Only link things that clearly belong; when unsure, ask instead.

Never run `ws rm`, `ws find`, or `ws home` unless the user asks: removing changes their list, finding costs usage, and the home page is an interactive screen.
