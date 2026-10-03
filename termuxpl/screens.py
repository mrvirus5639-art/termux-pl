"""Modal menus: playlists, metadata editor, history, settings, manual."""
from __future__ import annotations

import os
import time
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Input, Label, Markdown, Static,
                             Switch, TabbedContent, TabPane)

from . import acoustid_lookup, store
from .library import write_tags
from .models import Track, fmt_time


def clean_path(p: str) -> str:
    """Accept Windows 'Copy as path' (quoted), ~ and %VARS%."""
    p = p.strip().strip('"').strip("'").strip()
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p))) if p else ""


def _table(*cols: str, id: str | None = None) -> DataTable:
    t = DataTable(id=id, cursor_type="row", zebra_stripes=False)
    t.add_columns(*cols)
    return t


class Menu(ModalScreen):
    """Base for full-size modal menus."""
    BINDINGS = [Binding("escape", "dismiss_menu", "Close")]
    TITLE_TEXT = ""

    def action_dismiss_menu(self) -> None:
        self.dismiss(None)


# =============================================================== playlists
class PlaylistScreen(Menu):
    TITLE_TEXT = "PLAYLISTS"
    BINDINGS = Menu.BINDINGS + [
        Binding("delete", "remove", "Remove track"),
        Binding("ctrl+up", "move(-1)", "Move up"),
        Binding("ctrl+down", "move(1)", "Move down"),
    ]

    def __init__(self, library_tracks: list[Track], queue: list[Track], now: Track | None):
        super().__init__()
        self.lib = library_tracks
        self.queue = queue
        self.now = now
        self.current: str | None = None
        self.items: list[Track] = []
        self.lib_view: list[Track] = library_tracks[:500]

    def compose(self) -> ComposeResult:
        with Vertical(classes="menu"):
            yield Label("PLAYLISTS  ·  create playlists from local or downloaded tracks", classes="menu-title")
            with Horizontal(classes="row"):
                with Vertical(classes="col narrow box", id="pl-box") as v:
                    v.border_title = "YOUR PLAYLISTS"
                    t = _table("Name", id="pl-list")
                    t.show_header = False
                    yield t
                    yield Input(placeholder="new playlist name…", id="pl-name")
                    with Horizontal(classes="btns"):
                        yield Button("Create", id="pl-create")
                        yield Button("Rename", id="pl-rename")
                        yield Button("Delete", id="pl-delete")
                with Vertical(classes="col box", id="pl-tracks-box") as v:
                    v.border_title = "TRACKS"
                    yield _table("#", "Title", "Artist", "Time", id="pl-tracks")
                    with Horizontal(classes="btns"):
                        yield Button("Play", id="pl-play")
                        yield Button("Add to queue", id="pl-queue")
                        yield Button("Remove", id="pl-remove")
                    with Horizontal(classes="btns tight"):
                        yield Button("+ Now playing", id="pl-addnow")
                        yield Button("+ Whole queue", id="pl-addqueue")
                with Vertical(classes="col box", id="pl-lib-box") as v:
                    v.border_title = "LIBRARY  (enter = add)"
                    yield Input(placeholder="filter library…", id="pl-filter")
                    yield _table("Title", "Artist", id="pl-lib")
            yield Label("enter on a library track adds it · del removes · ctrl+↑/↓ reorders · esc closes",
                        classes="hint")

    def on_mount(self) -> None:
        self.reload_list()
        self.fill_lib()

    def reload_list(self, select: str | None = None) -> None:
        t = self.query_one("#pl-list", DataTable)
        t.clear()
        names = store.list_playlists()
        for n in names:
            t.add_row(Text(n), key=n)
        if names:
            target = select if select in names else (self.current if self.current in names else names[0])
            t.move_cursor(row=names.index(target))
            self.open_playlist(target)
        else:
            self.current = None
            self.items = []
            self.fill_tracks()

    def open_playlist(self, name: str) -> None:
        self.current = name
        self.items = store.load_playlist(name)
        self.query_one("#pl-tracks-box").border_title = f"TRACKS · {name} ({len(self.items)})"
        self.fill_tracks()

    def fill_tracks(self, cursor: int | None = None) -> None:
        t = self.query_one("#pl-tracks", DataTable)
        t.clear()
        for i, tr in enumerate(self.items, 1):
            t.add_row(str(i), Text(tr.display_title[:60]), Text(tr.display_artist[:30]), fmt_time(tr.duration))
        if cursor is not None and self.items:
            t.move_cursor(row=max(0, min(cursor, len(self.items) - 1)))

    def fill_lib(self) -> None:
        t = self.query_one("#pl-lib", DataTable)
        t.clear()
        for tr in self.lib_view:
            t.add_row(Text(tr.display_title[:60]), Text(tr.display_artist[:30]))

    def save(self) -> None:
        if self.current:
            store.save_playlist(self.current, self.items)
            self.query_one("#pl-tracks-box").border_title = f"TRACKS · {self.current} ({len(self.items)})"

    @on(DataTable.RowHighlighted, "#pl-list")
    def _pick(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key and ev.row_key.value != self.current:
            self.open_playlist(ev.row_key.value)

    @on(DataTable.RowSelected, "#pl-lib")
    def _add_from_lib(self, ev: DataTable.RowSelected) -> None:
        if not self.current:
            self.notify("Create a playlist first", severity="warning")
            return
        tr = self.lib_view[ev.cursor_row]
        self.items.append(tr)
        self.save()
        self.fill_tracks(cursor=len(self.items) - 1)

    @on(DataTable.RowSelected, "#pl-tracks")
    def _play_from(self, ev: DataTable.RowSelected) -> None:
        if self.items:
            self.app.play_list(self.items, ev.cursor_row)
            self.dismiss(None)

    @on(Input.Changed, "#pl-filter")
    def _filter(self, ev: Input.Changed) -> None:
        self.lib_view = self.app.library.search(ev.value, self.lib)[:500]
        self.fill_lib()

    @on(Input.Submitted, "#pl-name")
    def _name_submit(self) -> None:
        self._create()

    @on(Button.Pressed)
    def _buttons(self, ev: Button.Pressed) -> None:
        bid = ev.button.id
        tracks_tbl = self.query_one("#pl-tracks", DataTable)
        name_in = self.query_one("#pl-name", Input)
        if bid == "pl-create":
            self._create()
        elif bid == "pl-rename" and self.current:
            new = name_in.value.strip()
            if new:
                self.current = store.rename_playlist(self.current, new)
                name_in.value = ""
                self.reload_list(self.current)
        elif bid == "pl-delete" and self.current:
            store.delete_playlist(self.current)
            self.current = None
            self.reload_list()
        elif bid == "pl-play" and self.items:
            self.app.play_list(self.items, 0)
            self.dismiss(None)
        elif bid == "pl-queue" and self.items:
            self.app.enqueue(self.items)
        elif bid == "pl-addnow":
            if self.current and self.now:
                self.items.append(self.now)
                self.save()
                self.fill_tracks(cursor=len(self.items) - 1)
            elif not self.now:
                self.notify("Nothing is playing", severity="warning")
        elif bid == "pl-addqueue" and self.current and self.queue:
            self.items.extend(self.queue)
            self.save()
            self.fill_tracks(cursor=len(self.items) - 1)
        elif bid == "pl-remove":
            self.action_remove()

    def _create(self) -> None:
        name_in = self.query_one("#pl-name", Input)
        name = name_in.value.strip()
        if not name:
            self.notify("Type a playlist name first", severity="warning")
            return
        if name in store.list_playlists():
            self.notify("A playlist with that name exists", severity="warning")
            return
        safe = store.save_playlist(name, [])
        name_in.value = ""
        self.reload_list(safe)
        self.notify(f"Created playlist '{safe}'")

    def action_remove(self) -> None:
        t = self.query_one("#pl-tracks", DataTable)
        if self.items and t.cursor_row is not None and 0 <= t.cursor_row < len(self.items):
            row = t.cursor_row
            del self.items[row]
            self.save()
            self.fill_tracks(cursor=row)

    def action_move(self, d: int) -> None:
        t = self.query_one("#pl-tracks", DataTable)
        i = t.cursor_row
        j = i + d
        if self.items and 0 <= i < len(self.items) and 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self.save()
            self.fill_tracks(cursor=j)


# =========================================================== metadata editor
FIELDS = [("title", "Title"), ("artist", "Artist"), ("album", "Album"),
          ("year", "Year"), ("genre", "Genre"), ("track", "Track #")]


class MetadataScreen(Menu):
    def __init__(self, track: Track, api_key: str):
        super().__init__()
        self.track = track
        self.api_key = api_key
        self.candidates: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="menu"):
            yield Label("METADATA EDITOR", classes="menu-title")
            yield Static(self.track.source, classes="path")
            with Horizontal(classes="row"):
                with VerticalScroll(classes="col box", id="md-form") as v:
                    v.border_title = "TAGS"
                    for key, label in FIELDS:
                        with Horizontal(classes="field"):
                            yield Label(f"{label:<8}", classes="flabel")
                            yield Input(value=str(getattr(self.track, key, "") or ""), id=f"md-{key}")
                    with Horizontal(classes="btns"):
                        yield Button("Save tags", id="md-save", variant="primary")
                        yield Button("Fetch from AcoustID", id="md-fetch")
                        yield Button("Close", id="md-close")
                with Vertical(classes="col box", id="md-cands") as v:
                    v.border_title = "ACOUSTID MATCHES  (enter = apply)"
                    yield _table("Score", "Title", "Artist", "Album", "Year", id="md-table")
                    yield Static("", id="md-status", classes="hint")

    @on(Button.Pressed, "#md-close")
    def _close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#md-save")
    def _save(self) -> None:
        fields = {k: self.query_one(f"#md-{k}", Input).value for k, _ in FIELDS}
        try:
            write_tags(self.track.source, fields)
        except Exception as exc:
            self.notify(f"Could not save: {exc}", severity="error")
            return
        self.notify("Tags saved")
        self.dismiss(fields)

    @on(Button.Pressed, "#md-fetch")
    def _fetch(self) -> None:
        self.query_one("#md-status", Static).update("Fingerprinting and looking up…")
        self.lookup()

    @work(thread=True, exclusive=True)
    def lookup(self) -> None:
        try:
            cands = acoustid_lookup.lookup(self.track.source, self.api_key)
            self.app.call_from_thread(self._show, cands, "")
        except Exception as exc:
            self.app.call_from_thread(self._show, [], str(exc))

    def _show(self, cands: list[dict], err: str) -> None:
        self.candidates = cands
        t = self.query_one("#md-table", DataTable)
        t.clear()
        for c in cands:
            t.add_row(f"{c['score']:.2f}", Text(c["title"][:40]), Text(c["artist"][:30]), Text(c["album"][:30]), c["year"])
        status = self.query_one("#md-status", Static)
        if err:
            status.update(Text(err, style="red"))
        elif not cands:
            status.update("No matches found")
        else:
            status.update(f"{len(cands)} match(es) — select one to fill the form, then Save")
            t.focus()

    @on(DataTable.RowSelected, "#md-table")
    def _apply(self, ev: DataTable.RowSelected) -> None:
        c = self.candidates[ev.cursor_row]
        for k in ("title", "artist", "album", "year"):
            if c.get(k):
                self.query_one(f"#md-{k}", Input).value = c[k]
        self.query_one("#md-status", Static).update("Applied — press Save tags to write the file")


# ================================================================= history
class HistoryScreen(Menu):
    def __init__(self, history: store.History):
        super().__init__()
        self.history = history
        self.recent: list[tuple[float, Track]] = []
        self.top: list[tuple[int, Track]] = []
        self.period: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="menu"):
            yield Label("LISTENING HISTORY", classes="menu-title")
            with TabbedContent(initial="tab-top"):
                with TabPane("Top tracks", id="tab-top"):
                    with Horizontal(classes="btns"):
                        yield Button("All time", id="h-all")
                        yield Button("30 days", id="h-30")
                        yield Button("7 days", id="h-7")
                    yield _table("Plays", "Title", "Artist", "Type", id="h-top")
                with TabPane("Recently played", id="tab-recent"):
                    yield _table("When", "Title", "Artist", "Type", id="h-recent")
            with Horizontal(classes="btns"):
                yield Button("Add selected to queue", id="h-add")
                yield Button("Add top 10 to queue", id="h-top10", variant="primary")
                yield Button("Add top 25 to queue", id="h-top25")
                yield Button("Clear history", id="h-clear", variant="error")
                yield Button("Close", id="h-close")
            yield Label("enter plays · esc closes", classes="hint")

    def on_mount(self) -> None:
        self.fill()

    def fill(self) -> None:
        self.top = self.history.top(100, self.period)
        self.recent = self.history.recent(300)
        t = self.query_one("#h-top", DataTable)
        t.clear()
        for c, tr in self.top:
            t.add_row(str(c), Text(tr.display_title[:60]), Text(tr.display_artist[:30]), tr.kind)
        r = self.query_one("#h-recent", DataTable)
        r.clear()
        for ts, tr in self.recent:
            r.add_row(time.strftime("%d %b %H:%M", time.localtime(ts)), Text(tr.display_title[:60]),
                      Text(tr.display_artist[:30]), tr.kind)

    def _active(self) -> tuple[DataTable, list[Track]]:
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-recent":
            return self.query_one("#h-recent", DataTable), [t for _, t in self.recent]
        return self.query_one("#h-top", DataTable), [t for _, t in self.top]

    @on(DataTable.RowSelected)
    def _play(self, ev: DataTable.RowSelected) -> None:
        _, tracks = self._active()
        if tracks:
            self.app.play_list(tracks, ev.cursor_row)
            self.dismiss(None)

    @on(Button.Pressed)
    def _buttons(self, ev: Button.Pressed) -> None:
        bid = ev.button.id
        if bid in ("h-all", "h-30", "h-7"):
            self.period = {"h-all": None, "h-30": 30, "h-7": 7}[bid]
            self.fill()
        elif bid == "h-add":
            t, tracks = self._active()
            if tracks and t.cursor_row is not None and t.cursor_row < len(tracks):
                self.app.enqueue([tracks[t.cursor_row]])
        elif bid in ("h-top10", "h-top25"):
            n = 10 if bid == "h-top10" else 25
            tracks = [t for _, t in self.top[:n]]
            if tracks:
                self.app.enqueue(tracks)
            else:
                self.notify("No history yet", severity="warning")
        elif bid == "h-clear":
            self.history.clear()
            self.fill()
            self.notify("History cleared")
        elif bid == "h-close":
            self.dismiss(None)


# ================================================================ settings
class SettingsScreen(Menu):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.dirs = list(cfg.music_dirs)

    def compose(self) -> ComposeResult:
        c = self.cfg
        with Vertical(classes="menu"):
            yield Label("SETTINGS", classes="menu-title")
            with Horizontal(classes="row"):
                with Vertical(classes="col box", id="s-dirs-box") as v:
                    v.border_title = "MUSIC FOLDERS"
                    yield _table("Path", "Status", id="s-dirs")
                    yield Input(placeholder=r"add a path, e.g. D:\Music  (paste 'Copy as path' is fine)",
                                id="s-newdir")
                    with Horizontal(classes="btns"):
                        yield Button("Add path", id="s-add", variant="primary")
                        yield Button("Remove selected", id="s-remove")
                with VerticalScroll(classes="col box") as v:
                    v.border_title = "PLAYBACK & SERVICES"
                    for sid, label, val in (("s-norm", "Loudness normalization", c.normalize),
                                            ("s-mono", "Mono output (off = stereo)", c.mono),
                                            ("s-lyrics", "Show lyrics", c.lyrics),
                                            ("s-art", "Album cover art (off = rotating CD)",
                                             c.art_mode == "album"),
                                            ("s-shuffle", "Shuffle mode", c.shuffle)):
                        with Horizontal(classes="field"):
                            yield Switch(value=val, id=sid)
                            yield Label(label, classes="slabel")
                    with Horizontal(classes="field"):
                        yield Label("Target LUFS ", classes="flabel")
                        yield Input(value=str(c.loudness_target), id="s-lufs")
                    with Horizontal(classes="field"):
                        yield Label("Downloads  ", classes="flabel")
                        yield Input(value=c.download_dir, id="s-dl")
                    with Horizontal(classes="field"):
                        yield Label("AcoustID key", classes="flabel")
                        yield Input(value=c.acoustid_key, password=True, id="s-key",
                                    placeholder="free key: acoustid.org/new-application")
                    with Horizontal(classes="field"):
                        yield Label("Online hits ", classes="flabel")
                        yield Input(value=str(c.online_results), id="s-hits")
            with Horizontal(classes="btns"):
                yield Button("Save & rescan", id="s-save", variant="primary")
                yield Button("Cancel", id="s-cancel")

    def on_mount(self) -> None:
        self.fill_dirs()

    def fill_dirs(self) -> None:
        t = self.query_one("#s-dirs", DataTable)
        t.clear()
        for d in self.dirs:
            t.add_row(Text(d), "ok" if os.path.isdir(d) else "missing")

    @on(Input.Submitted, "#s-newdir")
    def _submit_dir(self) -> None:
        self._add_dir()

    def _add_dir(self) -> None:
        inp = self.query_one("#s-newdir", Input)
        p = clean_path(inp.value)
        if not p:
            return
        if not os.path.isdir(p):
            self.notify(f"Folder not found: {p}", severity="error")
            return
        if any(os.path.normcase(p) == os.path.normcase(d) for d in self.dirs):
            self.notify("Already in the list", severity="warning")
            return
        self.dirs.append(p)
        inp.value = ""
        self.fill_dirs()

    @on(Button.Pressed)
    def _buttons(self, ev: Button.Pressed) -> None:
        bid = ev.button.id
        if bid == "s-add":
            self._add_dir()
        elif bid == "s-remove":
            t = self.query_one("#s-dirs", DataTable)
            if self.dirs and t.cursor_row is not None and t.cursor_row < len(self.dirs):
                del self.dirs[t.cursor_row]
                self.fill_dirs()
        elif bid == "s-cancel":
            self.dismiss(None)
        elif bid == "s-save":
            c = self.cfg
            c.music_dirs = self.dirs
            c.normalize = self.query_one("#s-norm", Switch).value
            c.mono = self.query_one("#s-mono", Switch).value
            c.lyrics = self.query_one("#s-lyrics", Switch).value
            c.art_mode = "album" if self.query_one("#s-art", Switch).value else "disc"
            c.shuffle = self.query_one("#s-shuffle", Switch).value
            try:
                c.loudness_target = max(-30.0, min(-5.0, float(self.query_one("#s-lufs", Input).value)))
            except ValueError:
                pass
            try:
                c.online_results = max(5, min(50, int(self.query_one("#s-hits", Input).value)))
            except ValueError:
                pass
            dl = clean_path(self.query_one("#s-dl", Input).value)
            if dl:
                c.download_dir = dl
            c.acoustid_key = self.query_one("#s-key", Input).value.strip()
            c.save()
            self.dismiss(True)


# ================================================================== manual
class ManualScreen(Menu):
    def compose(self) -> ComposeResult:
        path = Path(__file__).with_name("MANUAL.md")
        try:
            md = path.read_text(encoding="utf-8")
        except OSError:
            md = "# Manual\n\nMANUAL.md is missing."
        with Vertical(classes="menu"):
            yield Label("USER MANUAL  ·  esc to close", classes="menu-title")
            with VerticalScroll(id="manual-scroll"):
                yield Markdown(md)

    def on_mount(self) -> None:
        self.query_one("#manual-scroll").focus()
