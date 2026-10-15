"""The search: Claude looks through GitHub, Linear and Slack for one workstream.

Three small read-only `claude -p` agents run at the same time, one per source,
so a search takes about as long as the slowest source instead of all three in a
row. Each prints one line per finding (SUGGEST / UPDATE) as it goes. Nothing is
linked automatically: findings become suggestions you accept or dismiss.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import threading

from . import core

# If rtk (https://github.com/rtk-ai/rtk) is installed, its Claude Code hook
# compresses gh output and the agent sees almost nothing. `rtk proxy` skips that.
GH = "rtk proxy gh" if shutil.which("rtk") else "gh"
GITHUB_TOOLS = [f"Bash({GH} search:*)", f"Bash({GH} pr view:*)", f"Bash({GH} pr list:*)"]
LINEAR_TOOLS = [
    "mcp__claude_ai_Linear__list_issues",
    "mcp__claude_ai_Linear__get_issue",
    "mcp__claude_ai_Linear__list_projects",
    "mcp__claude_ai_Linear__get_project",
    "mcp__claude_ai_Linear__list_teams",
]
SLACK_TOOLS = [
    "mcp__claude_ai_Slack__slack_read_channel",
    "mcp__claude_ai_Slack__slack_read_thread",
    "mcp__claude_ai_Slack__slack_search_public_and_private",
    "mcp__claude_ai_Slack__slack_search_channels",
]
DISALLOWED_TOOLS = ["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task"]

COMMON = """\
You search ONE source for things associated with one workstream. The user (GitHub
login: {viewer}) wants everything for it in one place. Read only: never change
anything anywhere.

WORKSTREAM: {name}

BRIEF (written by the user, this is what to look for):
---
{brief}
---

ALREADY LINKED (do not suggest again):
{linked}

DISMISSED BY THE USER (never suggest):
{dismissed}

BE FAST. Batch your work: make as few tool calls as possible, issue
independent calls together in one turn, and stop as soon as you have covered
the source. Do not explain your reasoning.

OUTPUT. Print each finding on its own line the moment you have it:
SUGGEST {{"kind": "...", "url": "...", "title": "...", "reason": "max 8 words", "status": "..."}}
Suggest only things that clearly belong to this workstream. Print nothing
else except a final line DONE.
"""

SOURCES = {
    "GitHub": (GITHUB_TOOLS, """\
YOUR SOURCE: GitHub pull requests in the org named in the brief, all repos
(any org if none is named).

Step 1. the user's own open PRs are listed below. Check every
one against the brief and print a SUGGEST line for each that belongs. These
are the most likely matches, so do not skip this step. Repos named in the
brief are where most work is, not a limit: include matching PRs from any
repo (for example a related web or backend repo). PRs come in stacks ("1 of 3", "2 of 3"):
when one PR of a stack belongs, suggest every PR in that stack.
{my_prs}

Step 2. More of the user's open PRs: in ONE Bash call, run 3 to 6 keyword
searches chained with `;`, each exactly like this:
  {gh} search prs --author @me --state open {owner}"<keywords>" --limit 30 --json url,title,state,repository,updatedAt
Only the user's own OPEN PRs count. Never suggest merged or closed PRs, or PRs by other people.

kind is "pr"; status is open|merged|closed."""),
    "Linear": (LINEAR_TOOLS, """\
YOUR SOURCE: Linear. Find issues and projects matching the brief (search by
the brief's main keywords, at most 3 searches). kind is "linear" for issues
(status = the issue state, for example "In Progress") or "linear_project".
For linked Linear issues, also print their current details:
UPDATE {{"url": "...", "title": "...", "status": "..."}}"""),
    "Slack": (SLACK_TOOLS, """\
YOUR SOURCE: Slack. Linked channels: {channels}.
Slack is context: use it to find the work. Read recent messages in each
linked channel and in linked threads. Every GitHub PR or Linear issue linked
there that belongs to this workstream is a finding: suggest the PR (kind "pr")
or issue (kind "linear") itself. Only PRs authored by the user count. Also suggest threads with decisions or open
questions (kind "slack_thread", title = a short summary of the thread). Also run at most 2
searches for the brief's main keywords. If no channel is linked, suggest the
channel most clearly about this workstream (kind "slack_channel", title = its
name like "#checkout-redesign"). For linked channels and threads, also print:
UPDATE {{"url": "...", "title": "...", "status": "..."}}"""),
}


def linked_summary(data):
    lines = []
    for kind, (collection, label) in core.KINDS.items():
        for key, entry in data[collection].items():
            lines.append(f"- {label}: {entry.get('url') or key}")
    return "\n".join(lines) or "(nothing yet)"


def build_prompt(ws, viewer, source):
    data = ws.load()
    channels = [
        f"{e.get('name') or key} ({e.get('url') or 'no link'})"
        for key, e in data["slack_channels"].items()
    ]
    common = COMMON.format(
        viewer=viewer or "unknown",
        name=ws.name,
        brief=ws.brief().strip(),
        linked=linked_summary(data),
        dismissed="\n".join(f"- {d}" for d in data["dismissed"]) or "(none)",
    )
    org = brief_org(ws.brief())
    extra = {"channels": ", ".join(channels) or "(none linked)", "my_prs": "", "gh": GH,
             "owner": f"--owner {org} " if org else ""}
    if source == "GitHub":
        extra["my_prs"] = my_recent_prs(ws.brief())
    return common + "\n" + SOURCES[source][1].format(**extra)


def brief_org(brief):
    """The brief's "Org:" line, else the default org from settings, else None."""
    m = re.search(r"^Org:\s*([\w.-]+)", brief, re.MULTILINE | re.IGNORECASE)
    return m.group(1) if m else core.DEFAULT_ORG


def my_recent_prs(brief, days=90):
    """Your open PRs (in the brief's org, if any), fetched by code so they're never missed."""
    org = brief_org(brief)
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = core.run(
        ["gh", "search", "prs", "--author", "@me", *(["--owner", org] if org else []), "--state", "open",
         "--updated", f">={since}", "--limit", "400", "--json", "url,title,state"], timeout=60,
    )
    try:
        prs = json.loads(out or "[]")
    except json.JSONDecodeError:
        prs = []
    lines = [f"- {p['url']} | {p['title']} | {p['state']}" for p in prs]
    return "\n".join(lines) or "(none found)"


def handle_line(ws, line, on_event):
    """Apply one SUGGEST/UPDATE line. Returns True when it was a finding."""
    line = line.strip()
    for prefix in ("SUGGEST ", "UPDATE "):
        if not line.startswith(prefix):
            continue
        try:
            item = json.loads(line[len(prefix):])
        except json.JSONDecodeError:
            return False
        parsed = core.classify(item.get("url", ""))
        if not parsed:
            return False
        kind, key, entry = parsed
        fields = {k: item[k] for k in ("title", "status") if item.get(k)}
        if kind == "slack_channel" and item.get("title"):
            fields["name"] = item["title"]
        if prefix == "SUGGEST ":
            entry.update(fields, reason=item.get("reason", ""))
            if ws.suggest(kind, key, entry):
                on_event("suggest", f"{core.KINDS[kind][1]}: {item.get('title') or key}")
        else:
            ws.annotate(kind, key, fields)
            on_event("update", key)
        return True
    return False


def run_source(ws, viewer, source, on_event, model, env):
    """One agent for one source. Returns the number of findings."""
    tools = SOURCES[source][0]
    args = [
        "claude", "-p", build_prompt(ws, viewer, source),
        "--model", model,
        "--output-format", "stream-json", "--verbose",
        "--allowedTools", *tools,
        "--disallowedTools", *DISALLOWED_TOOLS,
    ]
    try:
        proc = subprocess.Popen(
            args, cwd=ws.dir, env=env, text=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as e:
        on_event("error", f"could not start claude: {e}")
        return 0
    found = 0
    seen = set()
    on_event("source", (source, "running", 0))
    log_path = os.path.join(ws.dir, f"find-{source.lower()}.log")  # last run, for debugging
    with open(log_path, "w") as log:
        for raw in proc.stdout:
            log.write(raw)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            texts = []
            if event.get("type") == "assistant":
                for block in event["message"].get("content", []):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
            elif event.get("type") == "result":
                texts = [event.get("result") or ""]
                if event.get("is_error"):
                    on_event("error", f"{source}: " + (event.get("result") or "failed")[:150])
            for text in texts:
                for line in text.splitlines():
                    if line in seen:  # the final result repeats earlier text
                        continue
                    seen.add(line)
                    if handle_line(ws, line, on_event):
                        found += 1
                        on_event("source", (source, "running", found))
    proc.wait()
    if proc.returncode != 0 and not found:
        on_event("error", f"{source}: " + (proc.stderr.read() or "claude exited with an error").strip()[:150])
    on_event("source", (source, "done", found))
    return found


def run_find(ws, viewer, on_event, model="sonnet"):
    """Search all sources at once. on_event(kind, text) is called as it goes:
    "activity", "suggest", "update", "done", "error"."""
    # If ANTHROPIC_API_KEY is set, claude uses it and turns the claude.ai
    # connectors (Linear, Slack) off, so it is removed here and claude uses your
    # claude.ai login. Set WS_FIND_KEEP_API_KEY=1 if you only have an API key.
    drop = {"CLAUDECODE"} | (set() if os.environ.get("WS_FIND_KEEP_API_KEY") else {"ANTHROPIC_API_KEY"})
    env = {k: v for k, v in os.environ.items() if k not in drop}
    ws.update(lambda d: d.update(last_find=core.now_iso()))
    counts = {}

    def worker(source):
        counts[source] = run_source(ws, viewer, source, on_event, model, env)

    threads = [threading.Thread(target=worker, args=(s,)) for s in SOURCES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    on_event("done", f"{sum(counts.values())} found")
