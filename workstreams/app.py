"""Workstream Home: an interactive Textual TUI for one workstream.

Follows the UX spec (claude.ai artifact "Workstream Home UX", draft 2):

- Home shows only linked things, as one list with section labels, and keeps
  itself up to date. The top line answers "does anything need me?".
- Suggestions is a separate screen (s): an inbox of what Claude found. It is
  the only place Claude searches (f).
"""

import asyncio
import datetime
import os
import time

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, ListItem, ListView, Static, TextArea

from . import core, find

HERDR_SECONDS = 3
GITHUB_SECONDS = 60
SCAN_SECONDS = 30
UNDO_SECONDS = 5

# Catppuccin Macchiato palette with a peach accent
ACCENT, TEXT, SUB, DIM = "#f5a97f", "#cad3f5", "#8f93a2", "#6e738d"
GREEN, RED, YELLOW, BLUE, MAUVE = "#a6da95", "#ed8796", "#eed49f", "#8aadf4", "#c6a0f6"
TONE = {"ok": GREEN, "bad": RED, "wait": YELLOW, "dim": SUB, "info": BLUE}
AGENT_TONE = {"working": BLUE, "blocked": YELLOW, "done": GREEN, "idle": DIM}
DOT = "●"
SOURCES = list(find.SOURCES)
SUGGEST_TABS = [("PRs", ("pr",)), ("Linear", ("linear", "linear_project")),
                ("Slack", ("slack_channel", "slack_thread"))]

CSS = f"""
Screen {{ background: #1c2030; color: {TEXT}; }}
.name {{ padding: 1 2 0 2; text-style: bold; height: auto; }}
.topline {{ height: 1; padding: 0 2; }}
.topline > .left {{ width: 1fr; }}
.topline > .right {{ width: auto; color: {DIM}; }}
#start {{ padding: 1 2; height: auto; }}
#s-tabs {{ padding: 1 2 0 2; height: auto; }}
Nav {{ background: #1c2030; height: 1fr; padding: 1 1 0 1; scrollbar-size-vertical: 1; }}
Nav > ListItem {{ background: #1c2030; color: {TEXT}; padding: 0 1; border-left: tall #1c2030; height: auto; }}
Nav > ListItem:disabled {{ opacity: 1; text-opacity: 1; color: {TEXT}; }}
Nav > ListItem:hover {{ background: #24273a; }}
Nav > ListItem.-highlight {{ background: #1c2030; color: {TEXT}; text-style: none; }}
Nav:focus > ListItem.-highlight {{ background: #2e3348; color: {TEXT}; text-style: none; border-left: tall {ACCENT}; }}
.bottom {{ dock: bottom; height: auto; padding: 0 2; }}
.bottom > Static {{ height: auto; }}
ModalScreen {{ align: center middle; background: #0f1117 60%; }}
#dialog {{ width: 90; height: auto; max-height: 90%; padding: 1 2; background: #24273a; border: round {ACCENT}; }}
#dialog .dialog-title {{ color: {ACCENT}; text-style: bold; height: 2; }}
#dialog .hint {{ color: {SUB}; padding: 1 0 0 0; height: auto; }}
#dialog TextArea {{ height: 20; }}
"""


def fmt_time(value):
    if not value:
        return None
    dt = value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(value)
    return dt.strftime("%H:%M")


def ago(iso):
    """"5m ago", "3h ago", "2d ago" for a snapshot time."""
    if not iso:
        return ""
    seconds = (datetime.datetime.now() - datetime.datetime.fromisoformat(iso)).total_seconds()
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def keys_text(pairs):
    parts = []
    for key, desc in pairs:
        parts += [(key, f"bold {ACCENT}"), (f" {desc}   ", SUB)]
    return Text.assemble(*parts)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def indent_text(text, n):
    """Indent every line of a Text by n spaces (keeps the hierarchy readable)."""
    if not n:
        return text
    text = text if isinstance(text, Text) else Text(str(text))
    return Text("\n").join(Text(" " * n) + line for line in text.split("\n", allow_blank=True))


class Row(ListItem):
    def __init__(self, text, kind=None, key=None, url=None, pane_id=None, token=None, indent=0):
        text = indent_text(text, indent)
        super().__init__(Label(text), disabled=kind is None)
        self.kind, self.key, self.url, self.pane_id, self.token = kind, key, url, pane_id, token
        self.sig = (self.ident, text.plain if isinstance(text, Text) else str(text))

    @property
    def ident(self):
        return (self.kind, self.key or self.pane_id or str(self.url))


def label_row(title, style=f"bold {ACCENT}"):
    return Row(Text(title, style=style))


def spacer():
    return Row(Text(""))


class Nav(ListView):
    """A list that moves with j/k and skips label rows."""

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    async def show(self, rows):
        """Replace rows only when they changed, keeping the selection."""
        sig = [r.sig for r in rows]
        if getattr(self, "_sig", None) == sig:
            return
        self._sig = sig
        current = self.highlighted_child.ident if self.highlighted_child else None
        await self.clear()
        await self.extend(rows)
        idents = [r.ident if r.kind else None for r in rows]
        if current in idents:
            self.index = idents.index(current)
        else:
            self.index = next((i for i, r in enumerate(rows) if r.kind), None)


# ─── pop-ups ────────────────────────────────────────────────────────────────


class AddScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("Add a link", classes="dialog-title")
            yield Input(placeholder="Paste a GitHub PR, Linear issue or project, Slack channel or thread")
            yield Static(Text("Also works: #channel-name, ABC-123", style=SUB), id="kind", classes="hint")

    @on(Input.Changed)
    def show_kind(self, event):
        value = event.value.strip()
        kind = self.query_one("#kind", Static)
        if not value:
            kind.update(Text("Also works: #channel-name, ABC-123", style=SUB))
        elif parsed := core.classify(value):
            kind.update(Text.assemble((f"✓ {core.KINDS[parsed[0]][1]}", f"bold {GREEN}"),
                                      ("   enter adds it · esc cancels", SUB)))
        else:
            kind.update(Text("Not a link I recognize yet", style=YELLOW))

    @on(Input.Submitted)
    def submit(self, event):
        if core.classify(event.value.strip()):
            self.dismiss(event.value.strip())


class BriefScreen(ModalScreen):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        with Vertical(id="dialog"):
            yield Label("Brief: what this workstream is about", classes="dialog-title")
            yield TextArea(self.text, soft_wrap=True)
            yield Label("Ctrl+S saves and finds suggestions with it · Esc cancels", classes="hint")

    def action_save(self):
        self.dismiss(self.query_one(TextArea).text)


# ─── suggestions screen ─────────────────────────────────────────────────────


class SuggestScreen(Screen):
    BINDINGS = [
        Binding("y", "app.accept", "Link"),
        Binding("n", "app.dismiss_suggestion", "Dismiss"),
        Binding("f", "app.find", "Find more"),
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", show=False),
        Binding("left", "app.suggest_tab(-1)", "Previous tab", show=False),
        Binding("right", "app.suggest_tab(1)", "Next tab", show=False),
        Binding("h", "app.suggest_tab(-1)", "Previous tab", show=False),
        Binding("l", "app.suggest_tab(1)", "Next tab", show=False),
    ]

    def compose(self):
        yield Static(classes="name", id="s-name")
        with Horizontal(classes="topline"):
            yield Static(classes="left", id="s-progress")
            yield Static(classes="right", id="s-count")
        yield Static(id="s-tabs")
        yield Nav(id="sugg")
        with Vertical(classes="bottom"):
            yield Static(id="s-status")
            yield Static(keys_text([("← →", "switch tab"), ("y", "link"), ("n", "dismiss"),
                                    ("enter", "open to check"), ("f", "find more"), ("esc", "back to home")]))

    def on_mount(self):
        self.query_one("#sugg").focus()
        self.app.call_later(self.app.render_all)

    @on(ListView.Selected)
    def open_row(self, event):
        if event.item.url:
            core.run(["open", event.item.url])
        event.stop()


# ─── the app (Home is its default screen) ───────────────────────────────────


class HomeApp(App):
    CSS = CSS
    BINDINGS = [
        Binding("s", "suggestions", "Suggestions"),
        Binding("a", "add", "Add link"),
        Binding("b", "brief", "Brief"),
        Binding("x", "remove", "Remove"),
        Binding("u", "undo", "Undo"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, ws, workspace_id, home_pane):
        super().__init__()
        self.ws, self.workspace_id, self.home_pane = ws, workspace_id, home_pane
        self.viewer = None
        self.prs = {}
        self.panes, self.tabs = [], {}
        self.github_at = None
        self.github_error = None
        self.github_busy = False
        self.github_pending = False  # a refresh was asked for while one ran
        self.fetched_refs = set()  # PRs included in the last GitHub fetch
        self.last_scan = None
        self.find_running = False
        self.find_started = None
        self.find_sources = {}
        self.find_error = None
        self.undo = None  # (kind, key, entry, expires)
        self.suggest_tab = None  # which Suggestions tab is showing
        self.reported = None
        self.message = ""
        self.message_until = 0.0
        self.render_lock = asyncio.Lock()

    # layout (the default screen is Home)

    def compose(self) -> ComposeResult:
        yield Static(self.ws.name, classes="name")
        with Horizontal(classes="topline"):
            yield Static(classes="left", id="pulse")
            yield Static(classes="right", id="updated")
        yield Static(id="start")
        yield Nav(id="home")
        with Vertical(classes="bottom"):
            yield Static(id="waiting")
            yield Static(id="status")
            yield Static(keys_text([("enter", "open"), ("s", "suggestions"), ("a", "add link"),
                                    ("b", "brief"), ("x", "remove"), ("q", "quit")]))

    def on_mount(self):
        self.home = self.screen
        if self.home_pane:
            self.ws.update(lambda d: d.update(home_pane=self.home_pane))
        self.home.query_one("#home").focus()
        self.set_interval(HERDR_SECONDS, self.herdr_tick)
        self.set_interval(GITHUB_SECONDS, self.github_tick)
        self.set_interval(1, self.render_status)
        self.herdr_tick()
        self.github_tick()
        self.call_later(self.render_all)

    def on_resize(self, event):
        self.call_later(self.render_all)  # title width follows the window

    def on_home(self):
        return self.screen is self.home

    # background work

    @work(thread=True, group="herdr")
    def herdr_tick(self):
        panes = core.workspace_panes(self.workspace_id)
        tabs = core.workspace_tabs(self.workspace_id)
        self.panes, self.tabs = panes, tabs
        now = time.monotonic()
        if self.last_scan is None or now - self.last_scan > SCAN_SECONDS:
            self.last_scan = now
            if core.scan_pane_output(self.ws, panes, self.home_pane, tabs):
                self.github_tick()  # fetch titles for the new suggestions
        self.call_from_thread(self.render_all)

    @work(thread=True, group="github")
    def github_tick(self):
        if self.github_busy:
            self.github_pending = True
            return
        self.github_busy = True
        try:
            panes = self.panes or core.workspace_panes(self.workspace_id)  # first run
            lookups = core.branch_lookups(panes, self.home_pane)
            data = self.ws.load()
            refs = sorted(data["prs"])
            untitled = sorted(s["key"] for s in data["suggestions"].values()
                              if s["kind"] == "pr" and not s.get("title"))
            viewer, prs, found = core.fetch_github(refs + untitled, lookups)
            added = False
            for pane_id, owner, repo, number in found:  # a pane's branch: link it
                kind, key, entry = core.classify(f"https://github.com/{owner}/{repo}/pull/{number}")
                added |= self.ws.link(kind, key, entry, "pane branch", pane_id=pane_id)
            for ref in untitled:
                if pr := prs.get(ref):
                    self.ws.update(lambda d, r=ref, p=pr: d["suggestions"].get(f"pr:{r}", {}).update(
                        title=p["title"], status=p["state"].lower()))
            if added:
                viewer, prs, _ = core.fetch_github(sorted(self.ws.load()["prs"]), [])
            self.viewer, self.prs = viewer, prs
            self.fetched_refs = set(refs) | set(self.ws.load()["prs"]) if added else set(refs)
            self.github_at = datetime.datetime.now()
            self.github_error = None
        except Exception as e:  # keep the page alive on network trouble
            self.github_error = str(e) or repr(e)
        finally:
            self.github_busy = False
        self.call_from_thread(self.render_all)
        if self.github_pending:
            self.github_pending = False
            self.github_tick()

    @work(thread=True, group="find", exclusive=True)
    def run_find(self):
        def on_event(kind, payload):
            self.call_from_thread(self.find_event, kind, payload)

        find.run_find(self.ws, self.viewer, on_event)

    async def find_event(self, kind, payload):
        if kind == "source":
            name, state, count = payload
            self.find_sources[name] = (state, count)
        elif kind in ("suggest", "update"):
            await self.render_all()
        elif kind == "error":
            self.find_error = payload
        elif kind == "done":
            self.find_running = False
            await self.render_all()
        self.render_status()

    # rendering

    def flash(self, text, seconds=3):
        self.message, self.message_until = text, time.monotonic() + seconds
        self.render_status()

    def progress_text(self):
        elapsed = int(time.monotonic() - self.find_started)
        parts = [("Finding suggestions…", f"bold {ACCENT}"), (f"  {elapsed}s    ", SUB)]
        for name in SOURCES:
            state, count = self.find_sources.get(name, ("waiting", 0))
            if state == "done":
                parts.append((f"{name} ✓ {count}   ", GREEN))
            else:
                parts.append((f"{name} … {count or ''}".rstrip() + "   ", YELLOW))
        return Text.assemble(*parts)

    def render_status(self):
        msg = Text(self.message, style=TEXT) if self.message and time.monotonic() < self.message_until else Text("")
        if self.find_error and not self.find_running and not msg.plain:
            msg = Text(f"Finding suggestions had a problem: {self.find_error}", style=RED)
        self.home.query_one("#status", Static).update(msg)
        if isinstance(self.screen, SuggestScreen):
            self.screen.query_one("#s-status", Static).update(msg)
            self.render_suggest_progress()
        count = len(self.ws.load()["suggestions"])
        waiting = self.home.query_one("#waiting", Static)
        if self.find_running:
            waiting.update(self.progress_text())
        elif count:
            waiting.update(Text.assemble((f"{plural(count, 'suggestion')} waiting", f"bold {YELLOW}"),
                                         ("  press ", DIM), ("s", f"bold {ACCENT}"), (" to review", DIM)))
        else:
            waiting.update("")

    def render_suggest_progress(self):
        data = self.ws.load()
        if self.find_running:
            line = self.progress_text()
        elif data.get("last_find"):
            line = Text(f"found {fmt_time(data['last_find'])} from GitHub, Linear and Slack, using your brief", style=DIM)
        else:
            line = Text("Press f to find suggestions using your brief", style=DIM)
        self.screen.query_one("#s-progress", Static).update(line)

    async def render_all(self):
        async with self.render_lock:  # refreshes from several threads must not interleave
            await self._render_home()
            if isinstance(self.screen, SuggestScreen):
                await self._render_suggestions()
        self.render_status()

    @property
    def title_width(self):
        """Room for PR titles: the window width minus the number and status columns."""
        return max(30, min(90, self.size.width - 44))

    def pr_row(self, ref, pr, entry, done=False):
        """One PR under its repo heading: dot, #number, title, then fixed status
        columns (CI, review, draft) so they always sit in the same place. A second
        line appears only when the PR needs you."""
        if pr is None:
            state = "loading…" if ref not in self.fetched_refs else "not found or no access"
            text = Text.assemble((f"{DOT} ", DIM), (f"#{ref.split('#')[1]}".ljust(8), f"bold {TEXT}"), (state, DIM))
            return Row(text, "pr", ref, entry["url"], entry.get("last_pane"), indent=4)
        width = getattr(self, "_title_col", self.title_width)
        title = pr["title"]
        if len(title) > width:
            title = title[: width - 1] + "…"
        parts = [None, (f"#{pr['number']}".ljust(8), DIM if done else f"bold {TEXT}"),
                 (title.ljust(width + 2), DIM if done else TEXT)]
        if pr["state"] == "MERGED":
            parts[0] = (f"{DOT} ", MAUVE)
            parts.append(("merged", DIM))
        elif pr["state"] == "CLOSED":
            parts[0] = (f"{DOT} ", DIM)
            parts.append(("closed", DIM))
        else:
            ci = core.ci_state(pr)
            ci_col = {"SUCCESS": ("✓ CI", GREEN), "FAILURE": ("✗ CI", RED), "ERROR": ("✗ CI", RED),
                      "PENDING": ("◌ CI", YELLOW), "EXPECTED": ("◌ CI", YELLOW)}.get(ci, ("", DIM))
            review_col = {"APPROVED": ("✓ approved", GREEN), "CHANGES_REQUESTED": ("✗ changes", RED),
                          "REVIEW_REQUIRED": ("◌ review", YELLOW)}.get(pr["reviewDecision"], ("", DIM))
            parts += [(ci_col[0].ljust(6), ci_col[1]), (review_col[0].ljust(12), review_col[1])]
            extras = []
            if pr["isDraft"]:
                extras.append(("draft", DIM))
            if pr["mergeable"] == "CONFLICTING":
                extras.append(("conflict", RED))
            for i, extra in enumerate(extras):
                parts += [(" · " if i else "", DIM), extra]
            reason = core.needs_you_reason(pr, self.viewer)
            bad = ci_col[1] == RED or review_col[1] == RED or pr["mergeable"] == "CONFLICTING"
            parts[0] = (f"{DOT} ", RED if bad or (reason and reason != "ready to merge")
                        else GREEN if review_col[1] == GREEN and not pr["isDraft"] else YELLOW)
            if reason:
                color = GREEN if reason == "ready to merge" else RED
                parts += [("\n" + " " * 10, ""), (f"needs you: {reason}", f"bold {color}")]
        return Row(Text.assemble(*parts), "pr", ref, entry["url"], entry.get("last_pane"), indent=4)

    async def _render_home(self):
        home = self.home
        data = self.ws.load()
        rows = []

        def section(title, items):
            if items:
                if rows:
                    rows.append(spacer())
                rows.append(label_row(title))
                rows.extend(items)

        # Each thing appears once. Whatever needs you is marked on its own row
        # in red; the top line gives the count.
        needs = 0

        # Pull requests, grouped by repo: repos with open PRs first; within a
        # repo, open PRs then merged/closed (dimmed).
        by_repo = {}
        for ref in data["prs"]:
            by_repo.setdefault(ref.split("#")[0].split("/", 1)[1], []).append(ref)

        def is_open(ref):
            pr = self.prs.get(ref)
            return pr is None or pr["state"] == "OPEN"

        prs, open_count = [], 0
        longest = max((len(self.prs[r]["title"]) for r in data["prs"] if r in self.prs), default=30)
        self._title_col = min(self.title_width, longest)  # status columns sit right after the titles
        for repo in sorted(by_repo, key=lambda r: (not any(is_open(x) for x in by_repo[r]), r)):
            refs = sorted(by_repo[repo], key=lambda x: (not is_open(x), -int(x.split("#")[1])))
            if prs:
                prs.append(spacer())
            prs.append(label_row("  " + repo, "bold #b7bdf8"))
            for ref in refs:
                pr = self.prs.get(ref)
                open_count += is_open(ref)
                if pr and core.needs_you_reason(pr, self.viewer):
                    needs += 1
                prs.append(self.pr_row(ref, pr, data["prs"][ref], done=not is_open(ref)))
        section("PULL REQUESTS", prs)

        # Linear
        linear = []
        for key, e in sorted(data["linear_projects"].items()):
            second = " · ".join(p for p in ("project", ago(e.get("as_of"))) if p)
            linear.append(Row(Text.assemble((f"{DOT} ", BLUE), (e.get("title") or key, f"bold {TEXT}"),
                                            (f"\n  {second}", SUB)), "linear_project", key, e.get("url"), indent=2))
        for key, e in sorted(data["linear"].items()):
            parts = [(f"{DOT} ", BLUE), (e.get("title") or key, f"bold {TEXT}"),
                     (f"\n  {key}" if e.get("title") else "\n  details on next find", SUB if e.get("title") else DIM)]
            if e.get("status"):
                parts += [(" · ", DIM), (e["status"], BLUE)]
            if e.get("as_of"):
                parts.append((f" · {ago(e['as_of'])}", DIM))
            linear.append(Row(Text.assemble(*parts), "linear", key, e.get("url"), indent=2))
        section("LINEAR", linear)

        # Slack
        slack = []
        for key, e in sorted(data["slack_channels"].items()):
            slack.append(Row(Text.assemble((f"{DOT} ", MAUVE), (e.get("name") or e.get("title") or f"#{key}", f"bold {TEXT}"),
                                           ("\n  channel", SUB)), "slack_channel", key, e.get("url"), indent=2))
        for key, e in sorted(data["slack_threads"].items()):
            second = " · ".join(p for p in ("thread", ago(e.get("as_of"))) if p)
            parts = [(f"{DOT} ", MAUVE),
                     (e.get("title") or "summary on next find", f"bold {TEXT}" if e.get("title") else DIM),
                     (f"\n  {second}", SUB)]
            slack.append(Row(Text.assemble(*parts), "slack_thread", key, e.get("url"), indent=2))
        section("SLACK", slack)

        # Agents
        agents = []
        for pane in self.panes:
            status = pane.get("agent_status")
            if pane["pane_id"] == self.home_pane or status in (None, "unknown"):
                continue
            reason = core.AGENT_NEEDS_YOU.get(status)
            needs += bool(reason)
            agents.append(Row(Text.assemble(
                (f"{DOT} ", AGENT_TONE.get(status, DIM)), (self.tabs.get(pane["tab_id"], pane["tab_id"]), f"bold {TEXT}"),
                ("\n  ", ""), (f"needs you: {reason}" if reason else status,
                                f"bold {YELLOW}" if reason else AGENT_TONE.get(status, DIM)),
                (" · " + os.path.basename(pane.get("foreground_cwd") or ""), SUB)),
                "agent", pane_id=pane["pane_id"], indent=2))
        section("AGENTS", agents)

        # Top line
        if needs:
            pulse = Text(f"{DOT} {needs} need{'s' if needs == 1 else ''} you", style=f"bold {RED}")
        elif self.github_at is None and self.github_error:
            pulse = Text("Can't reach GitHub right now · retrying every minute", style=YELLOW)
        elif self.github_at is None:
            pulse = Text("Loading…", style=DIM)
        else:
            pulse = Text("✓ Nothing needs you", style=GREEN)
        home.query_one("#pulse", Static).update(pulse)
        updated = f"updated {fmt_time(self.github_at)}" if self.github_at else ""
        if self.github_error:
            updated = f"GitHub error: {self.github_error[:60]}"
        home.query_one("#updated", Static).update(Text(updated, style=RED if self.github_error else DIM))

        # First time: three steps instead of an empty list
        empty = not rows
        start = home.query_one("#start", Static)
        start.display = empty
        home.query_one("#home").display = not empty
        if empty:
            start.update(Text.assemble(
                ("Nothing linked yet.\n\n", DIM),
                ("  b  ", f"bold {ACCENT}"), ("Write the brief: what this workstream is about\n", TEXT),
                ("  s  ", f"bold {ACCENT}"), ("Find suggestions from GitHub, Linear and Slack\n", TEXT),
                ("  a  ", f"bold {ACCENT}"), ("Paste a link you already have\n", TEXT)))
        await home.query_one("#home", Nav).show(rows)
        self.report(open_count, needs, len(data["suggestions"]))

    async def _render_suggestions(self):
        screen = self.screen
        data = self.ws.load()
        screen.query_one("#s-name", Static).update(Text.assemble(("Suggestions", "bold"), (f"  · {self.ws.name}", DIM)))
        items = sorted(data["suggestions"].items(), key=lambda kv: kv[1].get("found", ""))
        screen.query_one("#s-count", Static).update(
            Text(f"{len(items)} to review", style=f"bold {YELLOW}") if items else Text(""))
        counts = {name: sum(1 for _, s in items if s["kind"] in kinds) for name, kinds in SUGGEST_TABS}
        names = [name for name, _ in SUGGEST_TABS]
        sticky = getattr(self, "_sticky_tab", False)  # you picked this tab; stay on it
        if self.suggest_tab not in names or (not sticky and not counts[self.suggest_tab] and any(counts.values())):
            # land on the first tab with something in it
            self.suggest_tab = next((n for n in names if counts[n]), names[0])
        tabs = []
        for name in names:
            active = name == self.suggest_tab
            label = f" {name} {counts[name]} "
            tabs.append((label, f"bold #1c2030 on {ACCENT}" if active else (SUB if counts[name] else DIM)))
            tabs.append(("  ", ""))
        screen.query_one("#s-tabs", Static).update(Text.assemble(*tabs))

        kinds = dict(SUGGEST_TABS)[self.suggest_tab]
        group = [(token, s) for token, s in items if s["kind"] in kinds]
        rows = []
        if kinds == ("pr",):
            rows = self.suggested_pr_rows(group)
        else:
            for token, s in group:
                if s["kind"] == "linear":
                    where = s["key"] + (f" · {s['status']}" if s.get("status") else "")
                elif s["kind"] == "linear_project":
                    where = "project"
                elif s["kind"] == "slack_channel":
                    where = "channel"
                else:
                    where = "thread"
                detail = " · ".join(p for p in (where, f"why: {s['reason']}" if s.get("reason") else "") if p)
                rows.append(Row(Text.assemble((s.get("title") or s["key"], f"bold {TEXT}"), ("\n" + detail, DIM)),
                                "suggestion", s["key"], s.get("url"), token=token, indent=2))
        if not rows:
            msg = ("More appear here as they're found." if self.find_running
                   else f"Nothing to review in {self.suggest_tab}." if any(counts.values())
                   else "Nothing to review. Press f to find suggestions using your brief.")
            rows = [Row(Text(msg, style=DIM))]
        await screen.query_one("#sugg", Nav).show(rows)
        self.render_suggest_progress()

    def suggested_pr_rows(self, group):
        """Suggested PRs the same way Home shows PRs: repo headings, then
        #number and title, with the reason on the line below."""
        by_repo = {}
        for token, s in group:
            by_repo.setdefault(s["key"].split("#")[0].split("/", 1)[1], []).append((token, s))
        width = max(30, min(90, self.size.width - 30))
        rows = []
        for repo in sorted(by_repo):
            rows.append(label_row("  " + repo, "bold #b7bdf8"))
            for token, s in sorted(by_repo[repo], key=lambda ts: -int(ts[1]["key"].split("#")[1])):
                title = s.get("title") or ""
                if len(title) > width:
                    title = title[: width - 1] + "…"
                state = (s.get("status") or "").lower()
                parts = [(f"#{s['key'].split('#')[1]}".ljust(8), f"bold {TEXT}"), (title, TEXT)]
                if state in ("merged", "closed"):
                    parts.append((f"  {state}", DIM))
                if s.get("reason"):
                    parts += [("\n" + " " * 8, ""), (f"why: {s['reason']}", DIM)]
                rows.append(Row(Text.assemble(*parts), "suggestion", s["key"], s.get("url"), token=token, indent=4))
            rows.append(spacer())
        return rows[:-1]

    def report(self, open_prs, needs, suggestions):
        if self.github_at is None or (open_prs, needs, suggestions) == self.reported:
            return
        if core.report_summary(self.workspace_id, open_prs, needs, suggestions):
            self.reported = (open_prs, needs, suggestions)

    # actions

    def selected(self):
        widget = self.focused
        if isinstance(widget, ListView) and widget.highlighted_child is not None:
            row = widget.highlighted_child
            return row if row.kind else None
        return None

    def pane_tab(self, pane_id):
        for pane in self.panes:
            if pane["pane_id"] == pane_id:
                return pane["tab_id"]
        return None

    @on(ListView.Selected, "#home")
    def open_row(self, event):
        row = event.item
        tab = self.pane_tab(row.pane_id) if row.pane_id else None
        if tab:
            core.herdr("tab", "focus", tab)
        elif row.url:
            core.run(["open", row.url])
        else:
            self.flash("No link for this item yet. Finding suggestions (s, then f) looks it up.")

    def action_suggestions(self):
        if not self.on_home():
            return
        self.push_screen(SuggestScreen())
        data = self.ws.load()
        if not data["suggestions"] and not data.get("last_find") and not self.find_running:
            self.action_find()  # first time: start finding on its own

    def action_add(self):
        if not self.on_home():
            return

        def done(value):
            if not value:
                return
            kind, key, entry = core.classify(value)
            added = self.ws.link(kind, key, entry, "added by hand", force=True)
            self.flash(f"Added {core.KINDS[kind][1]}: {key}" if added else f"Already on Home: {key}")
            if kind == "pr":
                self.github_tick()
            self.call_later(self.render_all)

        self.push_screen(AddScreen(), done)

    def action_brief(self):
        if not self.on_home():
            return

        def done(text):
            if text is None:
                return
            self.ws.save_brief(text)
            self.push_screen(SuggestScreen())
            self.action_find()

        self.push_screen(BriefScreen(self.ws.brief()), done)

    def action_find(self):
        if self.find_running:
            self.flash("Already finding suggestions.")
            return
        self.find_running = True
        self.find_started = time.monotonic()
        self.find_sources = {}
        self.find_error = None
        self.render_status()
        self.call_later(self.render_all)
        self.run_find()

    def action_suggest_tab(self, step):
        names = [name for name, _ in SUGGEST_TABS]
        current = names.index(self.suggest_tab) if self.suggest_tab in names else 0
        self.suggest_tab = names[(current + step) % len(names)]
        self._sticky_tab = True
        self.call_later(self.render_all)

    def action_accept(self):
        row = self.selected()
        if row and row.kind == "suggestion":
            self.ws.accept(row.token)
            self.flash(f"Linked: {row.key}. It's on Home now.")
            if row.token.startswith("pr:"):
                self.github_tick()
            self.call_later(self.render_all)

    def action_dismiss_suggestion(self):
        row = self.selected()
        if row and row.kind == "suggestion":
            self.ws.dismiss(row.token)
            self.flash(f"Dismissed: {row.key}. It won't come back.")
            self.call_later(self.render_all)

    def action_remove(self):
        if not self.on_home():
            return
        row = self.selected()
        if not row or row.kind not in core.KINDS:
            return
        entry = self.ws.load()[core.KINDS[row.kind][0]].get(row.key, {})
        self.ws.unlink(row.kind, row.key)
        self.prs.pop(row.key, None)
        self.undo = (row.kind, row.key, entry, time.monotonic() + UNDO_SECONDS)
        self.flash(f"Removed {row.key} · press u to undo", UNDO_SECONDS)
        self.call_later(self.render_all)

    def action_undo(self):
        if not self.undo or time.monotonic() > self.undo[3]:
            return
        kind, key, entry, _ = self.undo
        self.undo = None
        self.ws.link(kind, key, entry, entry.get("source", "restored"), force=True)
        self.flash(f"Restored {key}")
        if kind == "pr":
            self.github_tick()
        self.call_later(self.render_all)


def run_app(ws, workspace_id, home_pane):
    HomeApp(ws, workspace_id, home_pane).run()
