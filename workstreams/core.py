"""Storage, herdr and GitHub plumbing shared by the CLI and the TUI.

Nothing here owns work. GitHub, Linear and Slack stay the source of truth; a
workstream only stores the links that belong to it, a brief, and suggestions
from the find agent.
"""

import datetime
import json
import os
import re
import shlex
import socket
import sys
import subprocess
import threading

DATA_ROOT = os.environ.get(
    "WS_DATA_DIR", os.path.expanduser("~/.config/herdr-workstreams")
)
def _settings():
    """Optional ~/.config/herdr-workstreams/settings.toml:
        org = "YourGitHubOrg"          # default org to search for PRs
        linear_workspace = "yourteam"  # for bare Linear IDs like ABC-123
        slack_workspace = "yourteam"   # for app.slack.com/client links
    Environment variables WS_ORG, WS_LINEAR_WORKSPACE, WS_SLACK_WORKSPACE win."""
    try:
        import tomllib
        with open(os.path.join(DATA_ROOT, "settings.toml"), "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError, ImportError):
        return {}


SETTINGS = _settings()
DEFAULT_ORG = os.environ.get("WS_ORG") or SETTINGS.get("org")
LINEAR_WORKSPACE = os.environ.get("WS_LINEAR_WORKSPACE") or SETTINGS.get("linear_workspace")
SLACK_WORKSPACE = os.environ.get("WS_SLACK_WORKSPACE") or SETTINGS.get("slack_workspace")
DEFAULT_BRANCHES = {"main", "master", "develop", "HEAD", "trunk"}


# ─── small helpers ──────────────────────────────────────────────────────────


def run(args, cwd=None, timeout=20):
    """stdout on success, None on failure. Never reads the keyboard."""
    try:
        out = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def herdr(*args):
    out = run(["herdr", *args])
    if not out:
        return None
    try:
        return json.loads(out).get("result")
    except json.JSONDecodeError:
        return None


def herdr_rpc(method, params):
    """Raw socket call, for API methods the herdr CLI does not expose."""
    path = os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser(
        "~/.config/herdr/herdr.sock"
    )
    request = {"id": "ws", "method": method, "params": params}
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(path)
            s.sendall((json.dumps(request) + "\n").encode())
            buf = b""
            while chunk := s.recv(65536):
                buf += chunk
        return json.loads(buf).get("result")
    except (OSError, ValueError):
        return None


def strip_number(label):
    """herdr-automatic-rename can prefix labels with "[N] "; ignore it."""
    return re.sub(r"^\[\d+\]\s*", "", label or "")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ─── herdr: which space are we in ───────────────────────────────────────────


def current_workspace_id():
    for var in ("HERDR_WORKSPACE_ID", "HERDR_ACTIVE_WORKSPACE_ID"):
        if os.environ.get(var):
            return os.environ[var]
    result = herdr("workspace", "list") or {}
    for ws in result.get("workspaces", []):
        if ws.get("focused"):
            return ws["workspace_id"]
    return None


def workspace_label(workspace_id):
    result = herdr("workspace", "list") or {}
    for ws in result.get("workspaces", []):
        if ws["workspace_id"] == workspace_id:
            return strip_number(ws.get("label"))
    return None


def workspace_panes(workspace_id):
    result = herdr("pane", "list", "--workspace", workspace_id) or {}
    return result.get("panes", [])


def workspace_tabs(workspace_id):
    result = herdr("tab", "list", "--workspace", workspace_id) or {}
    return {t["tab_id"]: strip_number(t.get("label")) for t in result.get("tabs", [])}


# ─── links: anything you can paste ──────────────────────────────────────────

PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
PR_REF_RE = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")
LINEAR_ISSUE_RE = re.compile(r"https://linear\.app/([\w-]+)/issue/([A-Za-z][A-Za-z0-9]*-\d+)")
LINEAR_PROJECT_RE = re.compile(r"https://linear\.app/([\w-]+)/project/([\w-]+)")
LINEAR_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*-\d+)$")
SLACK_THREAD_RE = re.compile(r"https://([\w.-]+)\.slack\.com/archives/([A-Z0-9]+)/p(\d+)")
SLACK_CHANNEL_RE = re.compile(r"https://([\w.-]+)\.slack\.com/archives/([A-Z0-9]+)/?(?:[?#].*)?$")
SLACK_CLIENT_RE = re.compile(r"https://app\.slack\.com/client/([A-Z0-9]+)/([A-Z0-9]+)")
SLACK_NAME_RE = re.compile(r"^#([\w.-]+)$")

# kind -> (collection in links.json, label for people)
KINDS = {
    "pr": ("prs", "PR"),
    "linear": ("linear", "Linear issue"),
    "linear_project": ("linear_projects", "Linear project"),
    "slack_channel": ("slack_channels", "Slack channel"),
    "slack_thread": ("slack_threads", "Slack thread"),
}


def pr_ref(owner, repo, number):
    return f"{owner}/{repo}#{number}"


def parse_pr(text):
    text = text.strip()
    m = PR_URL_RE.search(text) or PR_REF_RE.match(text)
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def classify(text):
    """Turn a pasted link into (kind, key, entry), or None if unrecognized."""
    text = text.strip().strip("<>")
    if pr := parse_pr(text):
        owner, repo, number = pr
        return "pr", pr_ref(*pr), {"url": f"https://github.com/{owner}/{repo}/pull/{number}"}
    if m := LINEAR_ISSUE_RE.search(text):
        key = m.group(2).upper()
        return "linear", key, {"url": f"https://linear.app/{m.group(1)}/issue/{key}"}
    if m := LINEAR_PROJECT_RE.search(text):
        return "linear_project", m.group(2), {"url": m.group(0)}
    if m := LINEAR_ID_RE.match(text):
        key = m.group(1).upper()
        url = f"https://linear.app/{LINEAR_WORKSPACE}/issue/{key}" if LINEAR_WORKSPACE else None
        return "linear", key, {"url": url} if url else {}
    if m := SLACK_THREAD_RE.search(text):
        return "slack_thread", m.group(0), {"url": m.group(0), "channel_id": m.group(2)}
    if m := SLACK_CHANNEL_RE.match(text):
        return "slack_channel", m.group(2), {"url": f"https://{m.group(1)}.slack.com/archives/{m.group(2)}"}
    if m := SLACK_CLIENT_RE.search(text):
        url = f"https://{SLACK_WORKSPACE}.slack.com/archives/{m.group(2)}" if SLACK_WORKSPACE else m.group(0)
        return "slack_channel", m.group(2), {"url": url}
    if m := SLACK_NAME_RE.match(text):
        return "slack_channel", "#" + m.group(1), {"name": "#" + m.group(1)}
    return None


# ─── workstream storage ─────────────────────────────────────────────────────

BRIEF_TEMPLATE = """\
# {name}

Looking for: (what this workstream is about, in plain words)
Org: (GitHub org to search, all repos)
Repos: (optional: repos that matter most)
People: (optional: teammates working on it)
Notes: (anything else the find agent should know)
"""


class Workstream:
    def __init__(self, name):
        self.name = name
        self.dir = os.path.join(DATA_ROOT, slugify(name))
        self.links_path = os.path.join(self.dir, "links.json")
        self.brief_path = os.path.join(self.dir, "brief.md")
        self.lock = threading.RLock()

    # links.json

    def load(self):
        try:
            with open(self.links_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
        for collection, _ in KINDS.values():
            data.setdefault(collection, {})
        data.setdefault("suggestions", {})
        # Older versions stored dismissed PRs without a kind prefix.
        data["dismissed"] = [
            d if ":" in d.split("/")[0] else f"pr:{d}" for d in data.get("dismissed", [])
        ]
        return data

    def save(self, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.links_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.links_path)

    def update(self, fn):
        """Load, apply fn(data), save, all under the lock. Returns fn's result."""
        with self.lock:
            data = self.load()
            result = fn(data)
            self.save(data)
            return result

    def link(self, kind, key, entry, source, force=False, pane_id=None):
        """Link an item. Returns True when it was newly added."""
        collection = KINDS[kind][0]
        token = f"{kind}:{key}"

        def apply(data):
            if token in data["dismissed"]:
                if not force:
                    return False
                data["dismissed"].remove(token)
            data["suggestions"].pop(token, None)
            existing = data[collection].get(key)
            if existing is None:
                data[collection][key] = {**entry, "source": source, "added": now_iso()}
            else:
                for k, v in entry.items():
                    existing.setdefault(k, v)
            if pane_id:
                data[collection][key]["last_pane"] = pane_id
            return existing is None

        return self.update(apply)

    def unlink(self, kind, key):
        """Remove a link and remember it so nothing re-adds or re-suggests it."""
        collection = KINDS[kind][0]
        token = f"{kind}:{key}"

        def apply(data):
            data[collection].pop(key, None)
            data["suggestions"].pop(token, None)
            if token not in data["dismissed"]:
                data["dismissed"].append(token)

        self.update(apply)

    def suggest(self, kind, key, entry):
        """Add a suggestion unless it is linked or dismissed. PRs must be yours."""
        token = f"{kind}:{key}"
        if kind == "pr" and not (is_mine(key) and pr_state(key) == "open"):
            return False  # PR suggestions are your open PRs only

        def apply(data):
            if token in data["dismissed"] or key in data[KINDS[kind][0]]:
                return False
            existing = data["suggestions"].get(token)
            if existing:  # seen again: only fill in details, keep what we had
                existing.update({k: v for k, v in entry.items() if v and not existing.get(k)})
                return False
            data["suggestions"][token] = {**entry, "kind": kind, "key": key, "found": now_iso()}
            return True

        return self.update(apply)

    def annotate(self, kind, key, fields):
        """Store a snapshot (title, status, name) for a linked item."""
        collection = KINDS[kind][0]

        def apply(data):
            if key in data[collection]:
                data[collection][key].update(fields, as_of=now_iso())

        self.update(apply)

    def accept(self, token):
        data = self.load()
        s = data["suggestions"].get(token)
        if not s:
            return False
        entry = {k: v for k, v in s.items() if k not in ("kind", "key", "found", "reason")}
        return self.link(s["kind"], s["key"], entry, source="find", force=True)

    def dismiss(self, token):
        def apply(data):
            data["suggestions"].pop(token, None)
            if token not in data["dismissed"]:
                data["dismissed"].append(token)

        self.update(apply)

    # brief.md

    def brief(self):
        for path in (self.brief_path, os.path.join(self.dir, "notes.md")):
            try:
                with open(path) as f:
                    return f.read()
            except OSError:
                continue
        return BRIEF_TEMPLATE.format(name=self.name)

    def save_brief(self, text):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.brief_path, "w") as f:
            f.write(text.rstrip() + "\n")


_viewer = None
_authors = {}


def viewer_login():
    global _viewer
    if _viewer is None:
        _viewer = (run(["gh", "api", "user", "--jq", ".login"]) or "").strip() or None
    return _viewer


def _pr_info(ref):
    """(author login, "open" | "merged" | "closed") for owner/repo#123, cached per process."""
    if ref not in _authors:
        owner_repo, number = ref.split("#")
        out = run(["gh", "api", f"repos/{owner_repo}/pulls/{number}",
                   "--jq", '[.user.login, (if .merged_at then "merged" else .state end)] | join(" ")'])
        parts = (out or "").split()
        _authors[ref] = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
    return _authors[ref]


def is_mine(ref):
    """Whether you authored this PR (owner/repo#123)."""
    me = viewer_login()
    return bool(me) and _pr_info(ref)[0].lower() == me.lower()


def pr_state(ref):
    return _pr_info(ref)[1]


def workstream_for(workspace_id):
    label = workspace_label(workspace_id)
    return Workstream(label) if label else None


# ─── auto-detection from herdr panes ────────────────────────────────────────


def git_branch_and_repo(cwd):
    branch = (run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd) or "").strip()
    remote = (run(["git", "remote", "get-url", "origin"], cwd=cwd) or "").strip()
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", remote)
    if not branch or not m:
        return None
    return m.group(1), m.group(2), branch


def branch_lookups(panes, skip_pane):
    """[(pane_id, owner, repo, branch)] for panes on a non-default branch."""
    seen = {}
    for pane in panes:
        if pane["pane_id"] == skip_pane:
            continue
        cwd = pane.get("foreground_cwd") or pane.get("cwd")
        info = git_branch_and_repo(cwd) if cwd else None
        if not info or info[2] in DEFAULT_BRANCHES:
            continue
        seen.setdefault(info, pane["pane_id"])
    return [(pane_id, *info) for info, pane_id in seen.items()]


def scan_pane_output(ws, panes, skip_pane, tabs=None):
    """Suggest every PR link that shows up in a pane's recent output.

    Less certain than a pane's branch, so these become suggestions, not links."""
    found = []
    for pane in panes:
        if pane["pane_id"] == skip_pane:
            continue
        out = run(
            ["herdr", "pane", "read", pane["pane_id"],
             "--source", "recent-unwrapped", "--lines", "400"]
        ) or ""
        where = (tabs or {}).get(pane["tab_id"]) or pane["pane_id"]
        for owner, repo, number in set(PR_URL_RE.findall(out)):
            kind, key, entry = classify(f"https://github.com/{owner}/{repo}/pull/{number}")
            entry.update(reason=f"seen in pane: {where}", pane_id=pane["pane_id"])
            if ws.suggest(kind, key, entry):
                found.append(key)
    return found


# ─── GitHub: one GraphQL call for every PR and branch lookup ────────────────

PR_FIELDS = """
  number title url state isDraft mergeable reviewDecision headRefName updatedAt
  author { login }
  repository { nameWithOwner }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
"""


def open_url(url):
    """Open a link in the default browser or app (macOS `open`, else `xdg-open`)."""
    return run(["open" if sys.platform == "darwin" else "xdg-open", url]) is not None


def fetch_github(refs, lookups, timeout=60):
    """Returns (viewer_login, {ref: pr}, [(pane_id, owner, repo, number)]).

    Each pr dict gets "awaiting_my_review" (personal or team request)."""
    q = json.dumps
    parts = [
        "viewer { login }",
        # Covers team review requests too, which reviewRequests alone cannot.
        'awaiting: search(query: "is:pr is:open review-requested:@me", type: ISSUE, '
        "first: 100) { nodes { ... on PullRequest { url } } }",
    ]
    for i, ref in enumerate(refs):
        owner, repo, number = parse_pr(ref)
        parts.append(
            f"p{i}: repository(owner: {q(owner)}, name: {q(repo)}) "
            f"{{ pullRequest(number: {number}) {{ {PR_FIELDS} }} }}"
        )
    for i, (_, owner, repo, branch) in enumerate(lookups):
        parts.append(
            f"b{i}: repository(owner: {q(owner)}, name: {q(repo)}) "
            f"{{ pullRequests(headRefName: {q(branch)}, first: 1, "
            f"orderBy: {{field: UPDATED_AT, direction: DESC}}) {{ nodes {{ number }} }} }}"
        )
    query = "query { " + "\n".join(parts) + " }"
    proc = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    # Partial errors (a deleted repo, no access) still return data for the rest.
    try:
        data = json.loads(proc.stdout or "{}").get("data") or {}
    except json.JSONDecodeError:
        data = {}
    if not data:
        detail = (proc.stderr.strip() or proc.stdout.strip())[:200]
        raise RuntimeError(f"gh exited {proc.returncode}: {detail or 'no output'}")
    awaiting = {n.get("url") for n in (data.get("awaiting") or {}).get("nodes") or []}
    prs = {}
    for i, ref in enumerate(refs):
        pr = (data.get(f"p{i}") or {}).get("pullRequest")
        if pr:
            pr["awaiting_my_review"] = pr["url"] in awaiting
            prs[ref] = pr
    found = []
    for i, (pane_id, owner, repo, _) in enumerate(lookups):
        nodes = ((data.get(f"b{i}") or {}).get("pullRequests") or {}).get("nodes") or []
        if nodes:
            found.append((pane_id, owner, repo, nodes[0]["number"]))
    return data["viewer"]["login"], prs, found


def ci_state(pr):
    nodes = pr["commits"]["nodes"]
    rollup = nodes[0]["commit"]["statusCheckRollup"] if nodes else None
    return (rollup or {}).get("state")  # SUCCESS FAILURE ERROR PENDING EXPECTED


def needs_you_reason(pr, viewer):
    if pr["state"] != "OPEN":
        return None
    mine = (pr.get("author") or {}).get("login") == viewer
    if not mine:
        return "review requested" if pr.get("awaiting_my_review") else None
    ci = ci_state(pr)
    if ci in ("FAILURE", "ERROR"):
        return "CI failing"
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        return "changes requested"
    if pr["mergeable"] == "CONFLICTING":
        return "merge conflict"
    if pr["reviewDecision"] == "APPROVED" and ci == "SUCCESS" and not pr["isDraft"]:
        return "ready to merge"
    return None


def pr_badges(pr):
    """[(text, tone)] where tone is ok | bad | wait | dim."""
    if pr["state"] == "MERGED":
        return [("merged", "dim")]
    if pr["state"] == "CLOSED":
        return [("closed", "dim")]
    out = []
    if pr["isDraft"]:
        out.append(("draft", "dim"))
    ci = ci_state(pr)
    if ci == "SUCCESS":
        out.append(("CI ok", "ok"))
    elif ci in ("FAILURE", "ERROR"):
        out.append(("CI failing", "bad"))
    elif ci in ("PENDING", "EXPECTED"):
        out.append(("CI running", "wait"))
    review = {
        "APPROVED": ("approved", "ok"),
        "CHANGES_REQUESTED": ("changes requested", "bad"),
        "REVIEW_REQUIRED": ("needs review", "wait"),
    }.get(pr["reviewDecision"])
    if review:
        out.append(review)
    if pr["mergeable"] == "CONFLICTING":
        out.append(("conflict", "bad"))
    return out


AGENT_NEEDS_YOU = {"blocked": "agent waiting on you", "done": "agent finished"}


def report_summary(workspace_id, open_prs, needs, suggestions):
    """Sidebar line under each space, short enough for a 31-column sidebar:
    "1 needs you · 3 PRs · 14 suggestions" or "3 PRs · 14 suggestions".
    Tokens: $ws_needs (red) then $ws_prs (dim). herdr puts the dot between them."""
    rest = [f"{open_prs} PR{'' if open_prs == 1 else 's'}" if open_prs else "no PRs"]
    if suggestions:
        rest.append(f"{suggestions} suggestion{'' if suggestions == 1 else 's'}")
    args = ["workspace", "report-metadata", workspace_id, "--source", "workstreams",
            "--token", f"ws_prs={' · '.join(rest)}"]
    if needs:
        args += ["--token", f"ws_needs={needs} need{'s' if needs == 1 else ''} you"]
    else:
        args += ["--clear-token", "ws_needs"]
    return run(["herdr", *args]) is not None  # prints nothing on success


def open_home_tab(workspace_id, ws, launcher):
    """Focus the space's home tab, creating it (as the first tab) if needed."""
    home_pane = ws.load().get("home_pane")
    for pane in workspace_panes(workspace_id):
        if pane["pane_id"] == home_pane:
            herdr("tab", "focus", pane["tab_id"])
            return True
    created = herdr("tab", "create", "--workspace", workspace_id, "--label", "home", "--focus")
    if not created:
        return False
    herdr_rpc("tab.move", {"tab_id": created["tab"]["tab_id"], "insert_index": 0})
    herdr("pane", "run", created["root_pane"]["pane_id"], f"exec {shlex.quote(launcher)} home")
    return True
