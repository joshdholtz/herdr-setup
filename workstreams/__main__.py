"""ws: a home page for a herdr workstream (one herdr space = one workstream).

  ws home             run the interactive home page (normally in its own tab)
  ws open             open or focus the home tab in the current space
  ws add <link>       link a GitHub PR, Linear issue/project, or Slack channel/thread
  ws rm <link>        unlink it (nothing re-adds or re-suggests it)
  ws list             print everything linked to the current space
  ws find             run the find agent once and print what it found
  ws brief            print the brief file path
  ws context          print the workstream for agents (markdown; --json, --hook)
"""

import os
import sys

from . import core


def need_ws():
    workspace_id = core.current_workspace_id()
    ws = core.workstream_for(workspace_id) if workspace_id else None
    if not ws:
        sys.exit("ws: not inside a herdr space")
    return workspace_id, ws


def cmd_home(args):
    from .app import run_app

    workspace_id = args[0] if args else core.current_workspace_id()
    ws = core.workstream_for(workspace_id) if workspace_id else None
    if not ws:
        sys.exit("ws: not inside herdr and no space id given")
    run_app(ws, workspace_id, os.environ.get("HERDR_PANE_ID"))


def cmd_open(args):
    workspace_id, ws = need_ws()
    launcher = os.environ.get("WS_LAUNCHER", "ws")
    if not core.open_home_tab(workspace_id, ws, launcher):
        sys.exit("ws: could not create the home tab")


def cmd_add(args):
    parsed = core.classify(args[0]) if args else None
    if not parsed:
        sys.exit("usage: ws add <GitHub PR | Linear issue/project | Slack channel/thread link>")
    _, ws = need_ws()
    kind, key, entry = parsed
    added = ws.link(kind, key, entry, "added by hand", force=True)
    print(f"{'Linked' if added else 'Already linked'} {core.KINDS[kind][1]} {key} to {ws.name}")


def cmd_rm(args):
    parsed = core.classify(args[0]) if args else None
    if not parsed:
        sys.exit("usage: ws rm <link>")
    _, ws = need_ws()
    kind, key, _ = parsed
    ws.unlink(kind, key)
    print(f"Removed {core.KINDS[kind][1]} {key} from {ws.name}")


def cmd_list(args):
    _, ws = need_ws()
    data = ws.load()
    print(ws.name)
    for kind, (collection, label) in core.KINDS.items():
        for key, entry in sorted(data[collection].items()):
            extra = " · ".join(v for v in (entry.get("status"), entry.get("title") or entry.get("name")) if v)
            print(f"  {label:<15} {key}" + (f"  {extra}" if extra else ""))
    for token, s in data["suggestions"].items():
        print(f"  suggested       {token}  {s.get('reason', '')}")


def cmd_find(args):
    from . import find

    _, ws = need_ws()
    viewer = (core.run(["gh", "api", "user", "--jq", ".login"]) or "").strip() or None

    def on_event(kind, text):
        print(f"[{kind}] {text}", flush=True)

    find.run_find(ws, viewer, on_event)


def cmd_brief(args):
    _, ws = need_ws()
    if not os.path.exists(ws.brief_path):
        ws.save_brief(ws.brief())
    print(ws.brief_path)


def context_markdown(ws, data, gh_timeout=60):
    """The workstream as a short markdown page an agent can act on."""
    lines = [f"# Workstream: {ws.name}", "",
             f'This herdr space is the "{ws.name}" workstream. The user linked the items below to it.',
             "GitHub, Linear and Slack are the source of truth; this is the list of what belongs.",
             "Refresh with `ws context`. Link something new with `ws add <link>`.", ""]
    brief = "\n".join(l for l in ws.brief().splitlines() if not l.startswith("# ")).strip()
    template = core.BRIEF_TEMPLATE.format(name=ws.name).split("\n", 2)[2].strip()
    if brief and brief != template:
        lines += ["## Brief", "", brief, ""]

    refs = sorted(data["prs"])
    prs, viewer, note = {}, None, ""
    if refs:
        try:
            viewer, prs, _ = core.fetch_github(refs, [], timeout=gh_timeout)
        except Exception as e:
            note = f" (live status unavailable: {e})"
    lines += [f"## Pull requests{note}", ""]
    for ref in refs:
        pr = prs.get(ref)
        url = data["prs"][ref]["url"]
        if not pr:
            lines.append(f"- {ref}: {url}")
            continue
        state = pr["state"].lower()
        badges = ", ".join(t for t, _ in core.pr_badges(pr) if t not in ("merged", "closed"))
        reason = core.needs_you_reason(pr, viewer)
        line = f"- [{state}] {ref}: {pr['title']}" + (f" ({badges})" if badges else "")
        if reason:
            line += f". NEEDS YOU: {reason}"
        lines.append(line + f". {url}")
    if not refs:
        lines.append("- none linked")
    lines.append("")

    linear = [(k, e) for k, e in sorted(data["linear_projects"].items())] + sorted(data["linear"].items())
    if linear:
        lines += ["## Linear", ""]
        for key, e in linear:
            bits = [key] + [x for x in (e.get("status"), e.get("title")) if x]
            label = f"{bits[0]}: {' · '.join(bits[1:])}" if len(bits) > 1 else key
            lines.append(f"- {label}" + (f". {e['url']}" if e.get("url") else ""))
        lines.append("")

    slack = [("channel", e) for _, e in sorted(data["slack_channels"].items())] + \
            [("thread", e) for _, e in sorted(data["slack_threads"].items())]
    if slack:
        lines += ["## Slack", ""]
        for kind, e in slack:
            label = e.get("name") or e.get("title") or kind
            lines.append(f"- {kind}: {label}" + (f". {e['url']}" if e.get("url") else ""))
        lines.append("")

    if data["suggestions"]:
        lines += [f"({len(data['suggestions'])} unreviewed suggestions are not listed; "
                  "they are not confirmed as part of this workstream.)"]
    return "\n".join(lines).rstrip() + "\n"


def cmd_context(args):
    """Print the workstream for agents. --hook: Claude/Codex SessionStart output,
    silent when not in a herdr space that has a workstream."""
    import json as _json

    hook = "--hook" in args
    # Only the pane's own space: falling back to the focused space could hand an
    # agent outside herdr some other workstream.
    workspace_id = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace_id:
        if hook:
            return
        sys.exit("ws: not inside a herdr pane (HERDR_WORKSPACE_ID is not set)")
    ws = core.workstream_for(workspace_id)
    if not ws or not os.path.isdir(ws.dir):
        if hook:
            return
        sys.exit("ws: this herdr space has no workstream yet")
    data = ws.load()
    if "--json" in args:
        print(_json.dumps({"workstream": ws.name, "brief": ws.brief(), **{
            k: data[k] for k in ("prs", "linear", "linear_projects", "slack_channels", "slack_threads")}}, indent=2))
        return
    text = context_markdown(ws, data, gh_timeout=8 if hook else 60)
    if hook:
        print(_json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    else:
        print(text, end="")


COMMANDS = {
    "home": cmd_home, "open": cmd_open, "add": cmd_add, "rm": cmd_rm,
    "list": cmd_list, "find": cmd_find, "brief": cmd_brief, "context": cmd_context,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) < 2 else 2)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
