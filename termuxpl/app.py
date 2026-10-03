"""Termux PL - keyboard-driven terminal music player."""
from __future__ import annotations

import os
import random
import threading

from rich.text import Text
import re

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Label, Static

from . import lyrics as lyrics_mod
from . import online
from .config import Config
from .engine import FFMPEG, AudioEngine, compute_waveform
from .library import Library, read_cover, sort_tracks
from .models import Track, fmt_time
from .screens import (HistoryScreen, ManualScreen, MetadataScreen, PlaylistScreen,
                      SettingsScreen)
from .store import History
from .widgets import (ArtView, IdleView, InfoView, LyricsView, Separator, SpectrumView,
                      VolumeView, WaveView)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def esc(text: str) -> str:
    return _ANSI.sub("", str(text)).replace("[", "\\[")


SORT_LABEL = {"folder": "folder order", "title": "title", "artist": "artist", "duration": "duration"}
SORT_CYCLE = ["folder", "title", "artist", "duration"]


class TermuxPL(App):
    CSS_PATH = "termuxpl.tcss"
    TITLE = "Termux PL"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("b", "prev", "Prev"),
        Binding("x", "random_next", "Shuffle-next"),
        Binding("z", "toggle_shuffle", "Shuffle mode"),
        Binding("r", "cycle_repeat", "Repeat"),
        Binding("right_square_bracket", "seek(10)", "+10s"),
        Binding("left_square_bracket", "seek(-10)", "-10s"),
        Binding("full_stop", "seek(5)", "+5s", show=False),
        Binding("comma", "seek(-5)", "-5s", show=False),
        Binding("plus,equals_sign", "volume(5)", "Vol+"),
        Binding("minus,underscore", "volume(-5)", "Vol-"),
        Binding("l", "toggle_lyrics", "Lyrics"),
        Binding("c", "toggle_art", "Cover/CD"),
        Binding("N", "toggle_normalize", "Normalize"),
        Binding("m", "toggle_mono", "Stereo/Mono"),
        Binding("a", "enqueue_selected", "Queue"),
        Binding("A", "enqueue_all", "Queue all"),
        Binding("C", "clear_queue", "Clear queue"),
        Binding("slash", "focus_search", "Search"),
        Binding("o", "toggle_online", "Online/Local"),
        Binding("d", "download", "Download"),
        Binding("t", "cycle_sort", "Sort"),
        Binding("p", "playlists", "Playlists"),
        Binding("e", "edit_meta", "Metadata"),
        Binding("h", "history", "History"),
        Binding("s", "settings", "Settings"),
        Binding("question_mark,f1", "manual", "Manual"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "leave_input", "Back", show=False),
        Binding("delete", "queue_remove", "Remove", show=False),
        Binding("K", "queue_move(-1)", "Move up", show=False),
        Binding("J", "queue_move(1)", "Move down", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.engine = AudioEngine(self.cfg.sample_rate, self.cfg.volume, self.cfg.normalize,
                                  self.cfg.loudness_target, self.cfg.mono)
        self.library = Library()
        self.history = History()
        self.online_mode = False
        self.local_view: list[Track] = []
        self.online_view: list[Track] = []
        self.queue: list[Track] = []
        self.context: list[Track] = []  # list the current track was started from
        self.context_idx = -1
        self.back_stack: list[Track] = []
        self.now: Track | None = None
        self.lyrics: lyrics_mod.Lyrics | None = None
        self._wave_cancel: threading.Event | None = None
        self._play_token = 0
        self._loading = False
        self._last_error = None
        self._fail_streak = 0          # tracks in a row that ended without playing
        self._search_timer = None
        self._tick_count = 0

    # ----------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield ArtView(mode=self.cfg.art_mode, id="art")
            yield Separator(id="sep")
            with Vertical(id="infocol"):
                yield InfoView(id="info")
                yield SpectrumView(id="minispec")
            yield LyricsView(id="lyrics")
            yield IdleView(id="idle")
        with Horizontal(id="mid"):
            with Vertical(id="progress"):
                yield WaveView(id="wave")
            with Vertical(id="controls"):
                with Horizontal(id="btnrow"):
                    yield Button("<<<", id="btn-prev")
                    yield Button("PLAY", id="btn-play")
                    yield Button(">>>", id="btn-next")
                yield VolumeView(id="volume")
        with Horizontal(id="searchrow"):
            with Horizontal(id="searchbox"):
                yield Label("/l:", id="prefix")
                yield Input(placeholder="type to filter your library · o = switch to online",
                            id="search")
            yield Button("O", id="btn-mode")
        with Horizontal(id="bottom"):
            yield DataTable(id="results", cursor_type="row", show_header=False)
            with Vertical(id="queuebox"):
                yield Static("ADD TRACKS TO QUEUE", id="queue-empty")
                yield DataTable(id="queue", cursor_type="row", show_header=False)
        yield Static("", id="status")

    def on_mount(self) -> None:
        self.query_one("#top").border_title = None
        self.query_one("#progress").border_title = "PROGRESS BAR"
        self.query_one("#progress").border_subtitle = "[ 00:00 ]-[ 00:00 ]"
        self.query_one("#searchbox").border_title = "SEARCH LOCAL"
        self.query_one("#queuebox").border_title = "QUEUE"
        res = self.query_one("#results", DataTable)
        res.add_column("#", key="n", width=4)
        res.add_column("Title", key="title", width=40)
        res.add_column("Artist", key="artist", width=20)
        res.add_column("Time", key="time", width=8)
        q = self.query_one("#queue", DataTable)
        q.add_column("#", width=3)
        q.add_column("Title", width=36)
        q.add_column("Artist", width=18)
        q.add_column("Time", width=8)
        self._update_results_title()
        self._refresh_queue()
        self._update_volume()
        self._update_play_button()
        self._apply_lyrics_visibility()
        self.query_one("#info", InfoView).show(None, "Scanning library…" if self.cfg.music_dirs else
                                               "Currently No Track Loaded")
        self.call_after_refresh(self._fit_layout)
        self._ticker = self.set_interval(1 / 20, self._tick)
        self.scan_library()
        res.focus()
        hint = "space play/pause · n/b next/prev · x shuffle-next · / search · o online · ? manual"
        if not FFMPEG:
            hint = "[b red]ffmpeg not found - playback disabled. Fix: pip install imageio-ffmpeg[/]"
        elif self.engine.backend != "portaudio":
            hint = f"[yellow]audio output unavailable: {self.engine.backend}[/]"
        self._status(hint)

    def _status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    # ---------------------------------------------------------------- library
    @work(thread=True, exclusive=True, group="scan")
    def scan_library(self) -> None:
        dirs = self.cfg.music_dirs
        if not dirs:
            self.call_from_thread(self._status,
                                  "No music folders yet - press [b]s[/b] to add a path in Settings")
            self.call_from_thread(self._scan_done)
            return
        self.call_from_thread(self._status, "Scanning library…")
        self.library.scan(dirs, progress=lambda n: self.call_from_thread(
            self._status, f"Scanning library… {n} tracks"))
        self.call_from_thread(self._scan_done)

    def _scan_done(self) -> None:
        n = len(self.library.tracks)
        if self.now is None:
            self.query_one("#info", InfoView).show(None)
        self._filter_local(self.query_one("#search", Input).value if not self.online_mode else "")
        if self.cfg.music_dirs:
            self._status(f"{n} local tracks · press [b]?[/b] for the manual")

    def _filter_local(self, query: str) -> None:
        tracks = self.library.search(query) if query.strip() else self.library.tracks
        if not query.strip():
            tracks = sort_tracks(tracks, self.cfg.sort)
        self.local_view = tracks
        if not self.online_mode:
            self._fill_results(tracks)

    def _fill_results(self, tracks: list[Track]) -> None:
        t = self.query_one("#results", DataTable)
        t.clear()
        for i, tr in enumerate(tracks, 1):
            playing = self.now is not None and tr.source == self.now.source
            n = Text(f"{i}", style="bold #fde047" if playing else "")
            t.add_row(n, Text(tr.display_title), Text(tr.display_artist), fmt_time(tr.duration))
        self._update_results_title()
        self._size_columns()

    def _update_results_title(self) -> None:
        box = self.query_one("#results", DataTable)
        if self.online_mode:
            box.border_title = "ONLINE RESULTS"
            box.border_subtitle = f"( {len(self.online_view)} results )"
        else:
            box.border_title = f"LOCAL AUDIO FILES (sort: {SORT_LABEL.get(self.cfg.sort, self.cfg.sort)})"
            box.border_subtitle = f"( {len(self.local_view)} tracks )"

    def _size_columns(self) -> None:
        # Share the width between Title and Artist. Measured inside the border,
        # minus the fixed columns, 1 cell of padding each side of all 4 columns,
        # and the vertical scrollbar, so the table never scrolls sideways.
        for tid in ("#results", "#queue"):
            t = self.query_one(tid, DataTable)
            cols = list(t.columns.values())
            if len(cols) != 4 or not t.display:
                continue
            fixed = cols[0].width + cols[3].width + 2 * 4 + 1
            avail = max(12, t.content_size.width - fixed)
            cols[1].width = int(avail * 0.62)
            cols[2].width = avail - cols[1].width
            cols[1].auto_width = cols[2].auto_width = False
            # DataTable only recomputes its scrollable width when rows change;
            # ask it to do so now that the column widths changed.
            if hasattr(t, "_require_update_dimensions"):
                t._require_update_dimensions = True
            if hasattr(t, "_clear_caches"):
                t._clear_caches()
            t.refresh(layout=True)

    def _fit_layout(self) -> None:
        """Adapt the top panel to the window: square art, drop extras when tight."""
        top = self.query_one("#top")
        h = max(1, top.content_size.height)
        w = self.size.width
        art = self.query_one("#art")
        # A braille cell is 2x4 dots and about 1:2 in shape, so 2 columns per row.
        art.styles.width = max(12, min(2 * h, 44, w // 3))
        self.query_one("#sep").display = w >= 100
        self.query_one("#minispec").display = h >= 10

    def on_resize(self, _event) -> None:
        self.call_after_refresh(self._fit_layout)
        self.call_after_refresh(self._size_columns)

    @property
    def view(self) -> list[Track]:
        return self.online_view if self.online_mode else self.local_view

    # ----------------------------------------------------------------- search
    @on(Input.Changed, "#search")
    def _search_changed(self, ev: Input.Changed) -> None:
        val = ev.value
        if val.startswith("/s ") and not self.online_mode:
            self._set_mode(True, keep_text=val[3:])
            return
        if val.startswith("/l ") and self.online_mode:
            self._set_mode(False, keep_text=val[3:])
            return
        if val.startswith("/s ") and self.online_mode:
            with ev.input.prevent(Input.Changed):
                ev.input.value = val[3:]
            return
        if not self.online_mode:
            # Wait for a short pause in typing so big libraries stay responsive.
            if self._search_timer is not None:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.12, lambda: self._filter_local(
                self.query_one("#search", Input).value))

    @on(Input.Submitted, "#search")
    def _search_submit(self, ev: Input.Submitted) -> None:
        if self.online_mode:
            q = ev.value.strip()
            if q:
                self._status(f"Searching online for “{esc(q)}”…")
                self.search_online(q)
        self.query_one("#results", DataTable).focus()

    @work(thread=True, exclusive=True, group="online-search")
    def search_online(self, query: str) -> None:
        try:
            res = online.search(query, self.cfg.online_results)
        except Exception as exc:
            self.call_from_thread(self._status, f"[red]Online search failed:[/] {esc(str(exc))}")
            return
        self.call_from_thread(self._online_results, res, query)

    def _online_results(self, res: list[Track], query: str) -> None:
        self.online_view = res
        if self.online_mode:
            self._fill_results(res)
        self._status(f"{len(res)} online results for “{esc(query)}” · enter play · a queue · d download")

    def _set_mode(self, online_mode: bool, keep_text: str | None = None) -> None:
        self.online_mode = online_mode
        box = self.query_one("#searchbox")
        box.border_title = "SEARCH ONLINE" if online_mode else "SEARCH LOCAL"
        self.query_one("#prefix", Label).update("/s:" if online_mode else "/l:")
        self.query_one("#btn-mode", Button).label = "L" if online_mode else "O"
        inp = self.query_one("#search", Input)
        inp.placeholder = ("search YouTube, press enter · o = back to local" if online_mode
                           else "type to filter your library · o = switch to online")
        with inp.prevent(Input.Changed):
            inp.value = keep_text or ""
        if online_mode:
            self._fill_results(self.online_view)
        else:
            self._filter_local(inp.value)
        if keep_text is not None:
            inp.focus()
            inp.cursor_position = len(inp.value)

    def action_toggle_online(self) -> None:
        if not online.available():
            self._status("[red]yt-dlp is not installed[/] - pip install yt-dlp")
            return
        self._set_mode(not self.online_mode)
        self.query_one("#search", Input).focus()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_leave_input(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#results", DataTable).focus()

    def action_cycle_sort(self) -> None:
        i = SORT_CYCLE.index(self.cfg.sort) if self.cfg.sort in SORT_CYCLE else 0
        self.cfg.sort = SORT_CYCLE[(i + 1) % len(SORT_CYCLE)]
        self.cfg.save()
        if not self.online_mode:
            self._filter_local(self.query_one("#search", Input).value)

    # --------------------------------------------------------------- playback
    @on(DataTable.RowSelected, "#results")
    def _results_selected(self, ev: DataTable.RowSelected) -> None:
        if 0 <= ev.cursor_row < len(self.view):
            self.play_list(list(self.view), ev.cursor_row)

    @on(DataTable.RowSelected, "#queue")
    def _queue_selected(self, ev: DataTable.RowSelected) -> None:
        i = ev.cursor_row
        if 0 <= i < len(self.queue):
            tr = self.queue.pop(i)
            self._refresh_queue()
            self.play(tr)

    def play_list(self, tracks: list[Track], index: int) -> None:
        self.context = tracks
        self.context_idx = index
        self.play(tracks[index])

    def play(self, track: Track, push_back: bool = True, auto: bool = False) -> None:
        if not FFMPEG:
            self._status("[b red]ffmpeg not found - run: pip install imageio-ffmpeg  (then restart)[/]")
            return
        if not auto:
            self._fail_streak = 0
        # Tracks from playlists and history only carry title/artist/duration;
        # use the library's copy so the info panel shows album, format, etc.
        track = self.library.find(track.source) or track
        if push_back and self.now is not None:
            self.back_stack.append(self.now)
            self.back_stack = self.back_stack[-200:]
        if self.context:
            for i, t in enumerate(self.context):
                if t.source == track.source:
                    self.context_idx = i
                    break
        self._play_token += 1
        token = self._play_token
        self.now = track
        self.lyrics = None
        self._last_error = None
        if self._wave_cancel:
            self._wave_cancel.set()
        cancel = threading.Event()
        self._wave_cancel = cancel
        self.engine.stop()
        self.query_one("#wave", WaveView).set_levels(None)
        self.query_one("#wave", WaveView).set_progress(0)
        self.query_one("#art", ArtView).set_cover(None)
        self.query_one("#lyrics", LyricsView).set_lyrics(None, "Looking for lyrics…")
        self._show_info("Loading…")
        self._loading = True
        self._update_play_button()
        self._mark_playing_row()
        self._start_track(track, token, cancel)

    @work(thread=True, group="player")
    def _start_track(self, track: Track, token: int, cancel: threading.Event) -> None:
        src, headers = track.source, None
        if track.is_online:
            try:
                src, headers = online.resolve_stream(track)
            except Exception as exc:
                self.call_from_thread(self._play_failed, track, token, str(exc))
                return
        elif not os.path.isfile(src):
            self.call_from_thread(self._play_failed, track, token, "file not found")
            return
        if token != self._play_token:
            return
        self.engine.load(src, duration=track.duration, headers=headers)
        self.call_from_thread(self._started, track, token)
        # side-loads: cover, lyrics, waveform
        cover = online.fetch_bytes(track.thumbnail) if track.is_online and track.thumbnail \
            else (None if track.is_online else read_cover(src))
        self.call_from_thread(self._set_cover, cover, token)
        self._load_lyrics(track, token)
        # A waveform of an online track means downloading it a second time, so
        # only do that for normal song lengths.
        if track.is_online and (not track.duration or track.duration > 15 * 60):
            return
        levels = compute_waveform(src, headers, buckets=500, cancel=cancel)
        if levels is not None:
            self.call_from_thread(self._set_levels, levels, token)

    def _set_cover(self, cover, token: int) -> None:
        if token == self._play_token:
            self.query_one("#art", ArtView).set_cover(cover)

    def _set_levels(self, levels, token: int) -> None:
        if token == self._play_token:
            self.query_one("#wave", WaveView).set_levels(levels)

    @work(thread=True, group="lyrics")
    def _load_lyrics(self, track: Track, token: int) -> None:
        try:
            ly = lyrics_mod.fetch(track)
        except Exception:
            ly = None
        if token == self._play_token:
            self.call_from_thread(self._set_lyrics, ly)

    def _set_lyrics(self, ly) -> None:
        self.lyrics = ly
        msg = "" if ly else "No lyrics found"
        self.query_one("#lyrics", LyricsView).set_lyrics(ly, msg)

    def _started(self, track: Track, token: int) -> None:
        if token != self._play_token:
            return
        self._loading = False
        self.history.add(track)
        self._show_info()
        self._update_play_button()
        self._status(f"▶ {esc(track.display_title)} — {esc(track.display_artist)}")

    def _play_failed(self, track: Track, token: int, err: str) -> None:
        if token != self._play_token:
            return
        self._loading = False
        self.now = None
        self._show_info()
        self._update_play_button()
        self._status(f"[red]Could not play[/] {esc(track.display_title)}: {esc(err[:200])}")

    def _show_info(self, status: str = "") -> None:
        extra = f"{'mono' if self.cfg.mono else 'stereo'} · loudnorm {'on' if self.cfg.normalize else 'off'}"
        self.query_one("#info", InfoView).show(self.now, status, extra)

    def _mark_playing_row(self) -> None:
        t = self.query_one("#results", DataTable)
        rows = self.view
        for i, tr in enumerate(rows):
            playing = self.now is not None and tr.source == self.now.source
            try:
                t.update_cell_at((i, 0), Text(f"{i + 1}", style="bold #fde047" if playing else ""))
            except Exception:
                break

    def _next_track(self, random_pick: bool = False) -> Track | None:
        if self.queue and not random_pick:
            tr = self.queue.pop(0)
            self._refresh_queue()
            return tr
        pool = self.context or self.view
        if not pool:
            return None
        if random_pick or self.cfg.shuffle:
            choices = [t for t in pool if self.now is None or t.source != self.now.source]
            return random.choice(choices) if choices else pool[0]
        nxt = self.context_idx + 1
        if nxt >= len(pool):
            if self.cfg.repeat == "all":
                nxt = 0
            else:
                return None
        self.context_idx = nxt
        return pool[nxt]

    def _track_ended(self, played: bool = True) -> None:
        if not played:
            # The track ended without producing audio (unreadable file, dead
            # stream). Skip on, but don't spin through a whole broken library.
            self._fail_streak += 1
            if self._fail_streak >= 3:
                self.engine.stop()
                self._update_play_button()
                self._status("[red]Stopped: 3 tracks in a row could not be played.[/] "
                             + esc(self.engine.error or ""))
                return
        else:
            self._fail_streak = 0
        if self.cfg.repeat == "one" and self.now is not None and played:
            self.play(self.now, push_back=False, auto=True)
            return
        nxt = self._next_track()
        if nxt is None:
            self._status("End of list")
            self.engine.stop()
            self._update_play_button()
        else:
            self.play(nxt, auto=True)

    def action_toggle_play(self) -> None:
        if self._loading:
            return  # a track is already starting
        if not self.engine.loaded:
            if self.now is not None:
                self.play(self.now, push_back=False)
            elif self.view:
                t = self.query_one("#results", DataTable)
                self.play_list(list(self.view), max(0, t.cursor_row or 0))
            return
        self.engine.toggle()
        self._update_play_button()

    def action_next(self) -> None:
        nxt = self._next_track()
        if nxt:
            self.play(nxt)

    def action_random_next(self) -> None:
        nxt = self._next_track(random_pick=True)
        if nxt:
            self.play(nxt)

    def action_prev(self) -> None:
        if self.engine.loaded and self.engine.position > 3:
            self.engine.seek(0)
            return
        if self.back_stack:
            self.play(self.back_stack.pop(), push_back=False)
        elif self.context and self.context_idx > 0:
            self.context_idx -= 1
            self.play(self.context[self.context_idx], push_back=False)

    def action_seek(self, delta: int) -> None:
        self.engine.seek_relative(delta)

    @on(WaveView.Seek)
    def _wave_seek(self, ev: WaveView.Seek) -> None:
        if self.engine.loaded and self.engine.duration:
            self.engine.seek(ev.fraction * self.engine.duration)

    def action_volume(self, delta: int) -> None:
        self._set_volume(self.engine.volume + delta)

    @on(VolumeView.Changed)
    def _vol_changed(self, ev: VolumeView.Changed) -> None:
        self._set_volume(ev.volume)

    def _set_volume(self, v: int) -> None:
        self.engine.set_volume(v)
        self.cfg.volume = self.engine.volume
        self.cfg.save()
        self._update_volume()

    def _update_volume(self) -> None:
        flags = (f"shuffle {'on' if self.cfg.shuffle else 'off'} · repeat {self.cfg.repeat} · "
                 f"{'mono' if self.cfg.mono else 'stereo'} · norm {'on' if self.cfg.normalize else 'off'}")
        self.query_one("#volume", VolumeView).set(self.engine.volume, flags)

    def _update_play_button(self) -> None:
        btn = self.query_one("#btn-play", Button)
        if self._loading:
            btn.label = "…"
        else:
            btn.label = "PAUSE" if (self.engine.loaded and not self.engine.paused) else "PLAY"

    def action_toggle_shuffle(self) -> None:
        self.cfg.shuffle = not self.cfg.shuffle
        self.cfg.save()
        self._update_volume()
        self._status(f"Shuffle {'on' if self.cfg.shuffle else 'off'}")

    def action_cycle_repeat(self) -> None:
        order = ["off", "all", "one"]
        self.cfg.repeat = order[(order.index(self.cfg.repeat) + 1) % 3] if self.cfg.repeat in order else "off"
        self.cfg.save()
        self._update_volume()
        self._status(f"Repeat {self.cfg.repeat}")

    def action_toggle_normalize(self) -> None:
        self.cfg.normalize = not self.cfg.normalize
        self.engine.set_normalize(self.cfg.normalize)
        self.cfg.save()
        self._update_volume()
        self._show_info()
        self._status(f"Loudness normalization {'on' if self.cfg.normalize else 'off'}")

    def action_toggle_mono(self) -> None:
        self.cfg.mono = not self.cfg.mono
        self.engine.set_mono(self.cfg.mono)
        self.cfg.save()
        self._update_volume()
        self._show_info()
        self._status(f"Output: {'mono' if self.cfg.mono else 'stereo'}")

    def action_toggle_lyrics(self) -> None:
        self.cfg.lyrics = not self.cfg.lyrics
        self.cfg.save()
        self._apply_lyrics_visibility()
        self._status(f"Lyrics {'on' if self.cfg.lyrics else 'off'}")

    def action_toggle_art(self) -> None:
        self.cfg.art_mode = "disc" if self.cfg.art_mode == "album" else "album"
        self.cfg.save()
        art = self.query_one("#art", ArtView)
        art.set_mode(self.cfg.art_mode)
        if self.cfg.art_mode == "disc":
            self._status("Artwork: rotating CD")
        elif art.cover is None and self.now is not None:
            self._status("Artwork: album cover (this track has none, so the CD stays)")
        else:
            self._status("Artwork: album cover")

    @on(ArtView.Clicked)
    def _art_clicked(self) -> None:
        self.action_toggle_art()

    def _apply_lyrics_visibility(self) -> None:
        self.query_one("#lyrics").display = self.cfg.lyrics
        self.query_one("#idle").display = not self.cfg.lyrics

    @on(Button.Pressed)
    def _buttons(self, ev: Button.Pressed) -> None:
        bid = ev.button.id
        if bid == "btn-play":
            self.action_toggle_play()
        elif bid == "btn-next":
            self.action_next()
        elif bid == "btn-prev":
            self.action_prev()
        elif bid == "btn-mode":
            self.action_toggle_online()

    # ------------------------------------------------------------------ queue
    def enqueue(self, tracks: list[Track]) -> None:
        self.queue.extend(tracks)
        self._refresh_queue()
        self.notify(f"Added {len(tracks)} track(s) to queue", timeout=2)

    def action_enqueue_selected(self) -> None:
        t = self.query_one("#results", DataTable)
        if self.view and t.cursor_row is not None and t.cursor_row < len(self.view):
            self.enqueue([self.view[t.cursor_row]])

    def action_enqueue_all(self) -> None:
        if self.view:
            self.enqueue(list(self.view))

    def action_clear_queue(self) -> None:
        self.queue.clear()
        self._refresh_queue()

    def action_queue_remove(self) -> None:
        q = self.query_one("#queue", DataTable)
        if self.focused is q and self.queue and q.cursor_row < len(self.queue):
            row = q.cursor_row
            del self.queue[row]
            self._refresh_queue(row)

    def action_queue_move(self, d: int) -> None:
        q = self.query_one("#queue", DataTable)
        if self.focused is not q or not self.queue:
            return
        i, j = q.cursor_row, q.cursor_row + d
        if 0 <= i < len(self.queue) and 0 <= j < len(self.queue):
            self.queue[i], self.queue[j] = self.queue[j], self.queue[i]
            self._refresh_queue(j)

    def _refresh_queue(self, cursor: int | None = None) -> None:
        q = self.query_one("#queue", DataTable)
        q.clear()
        for i, tr in enumerate(self.queue, 1):
            q.add_row(str(i), Text(tr.display_title), Text(tr.display_artist), fmt_time(tr.duration))
        empty = not self.queue
        was_hidden = not q.display
        self.query_one("#queue-empty").display = empty
        q.display = not empty
        if was_hidden and not empty:
            # Its columns were sized while it had no width; size them now.
            self.call_after_refresh(self._size_columns)
        self.query_one("#queuebox").border_subtitle = f"( {len(self.queue)} queued )" if self.queue else ""
        if cursor is not None and self.queue:
            q.move_cursor(row=min(cursor, len(self.queue) - 1))

    # ---------------------------------------------------------------- menus
    def action_playlists(self) -> None:
        self.push_screen(PlaylistScreen(self.library.tracks, list(self.queue), self.now))

    def action_history(self) -> None:
        self.push_screen(HistoryScreen(self.history))

    def action_manual(self) -> None:
        self.push_screen(ManualScreen())

    def action_settings(self) -> None:
        def done(changed):
            if changed:
                self.engine.loudness = self.cfg.loudness_target
                self.engine.set_normalize(self.cfg.normalize)
                self.engine.set_mono(self.cfg.mono)
                self._apply_lyrics_visibility()
                self.query_one("#art", ArtView).set_mode(self.cfg.art_mode)
                self._update_volume()
                self._show_info()
                self.scan_library()
        self.push_screen(SettingsScreen(self.cfg), done)

    def action_edit_meta(self) -> None:
        t = self.query_one("#results", DataTable)
        target = None
        if not self.online_mode and self.view and t.cursor_row is not None and t.cursor_row < len(self.view):
            target = self.view[t.cursor_row]
        elif self.now is not None and not self.now.is_online:
            target = self.now
        if target is None:
            self._status("Select a local track to edit (online tracks: press d to download first)")
            return

        def done(fields):
            if fields:
                self.library.refresh_track(target)
                lib_copy = self.library.find(target.source)
                if lib_copy is not None and lib_copy is not target:
                    self.library.refresh_track(lib_copy)
                if not self.online_mode:
                    self._filter_local(self.query_one("#search", Input).value)
                if self.now is not None and self.now.source == target.source:
                    self.now = target
                    self._show_info()
        self.push_screen(MetadataScreen(target, self.cfg.acoustid_key), done)

    def action_download(self) -> None:
        if not self.online_mode:
            self._status("Downloads work on online results - press o to search online")
            return
        t = self.query_one("#results", DataTable)
        if self.view and t.cursor_row is not None and t.cursor_row < len(self.view):
            tr = self.view[t.cursor_row]
            self._status(f"Downloading {esc(tr.display_title)}…")
            self.download_track(tr)

    @work(thread=True, group="download")
    def download_track(self, track: Track) -> None:
        last = [-1]

        def prog(f):
            pct = int(f * 100)
            if pct // 5 != last[0]:
                last[0] = pct // 5
                self.call_from_thread(self._status, f"Downloading {esc(track.display_title)}… {pct}%")
        try:
            path = online.download(track, self.cfg.download_dir, prog)
        except Exception as exc:
            self.call_from_thread(self._status, f"[red]Download failed:[/] {esc(str(exc)[:200])}")
            return
        self.call_from_thread(self._downloaded, path)

    def _downloaded(self, path: str) -> None:
        dl = self.cfg.download_dir
        if not any(os.path.normcase(os.path.abspath(dl)) == os.path.normcase(os.path.abspath(d))
                   for d in self.cfg.music_dirs):
            self.cfg.music_dirs.append(dl)
            self.cfg.save()
        self._status(f"Saved to {esc(path)} · rescanning library…")
        self.scan_library()

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        # The timer can fire once more while the app is shutting down, after the
        # widgets are gone; skip that frame instead of crashing on exit.
        try:
            self._tick_frame()
        except NoMatches:
            pass

    def _tick_frame(self) -> None:
        e = self.engine
        if e.error and e.error != self._last_error:
            self._last_error = e.error
            self._status(f"[red]Playback error:[/] {esc(e.error[:200])}")
        if e.finished and e.loaded and not self._loading:
            e.finished = False
            self._track_ended(played=e.position > 0.5)
            return
        self._tick_count += 1
        if self._tick_count % 40 == 0 and e.loaded and not e.paused:   # every ~2 s
            if e.ensure_output():
                self._status("Audio device changed - reconnected to the default output")
        playing = e.loaded and not e.paused
        bands = e.spectrum(40) if (playing or e.loaded) else None
        if bands is not None:
            self.query_one("#minispec", SpectrumView).set_bands(bands)
        if playing:
            self.query_one("#art", ArtView).spin(0.12)
        if not self.cfg.lyrics:
            self.query_one("#idle", IdleView).tick(bands if playing else None, 0.15 if playing else 0.0)
        dur = e.duration or (self.now.duration if self.now else 0)
        if self.now is not None and self.now.duration and not e.duration:
            e.duration = self.now.duration
        pos = e.position if e.loaded else 0.0
        self.query_one("#lyrics", LyricsView).tick(pos, dur)
        self.query_one("#wave", WaveView).set_progress(pos / dur if dur else 0.0)
        self.query_one("#progress").border_subtitle = f"[ {fmt_time(pos)} ]-[ {fmt_time(dur)} ]"
        btn = self.query_one("#btn-play", Button)
        want = "…" if self._loading else ("PAUSE" if playing else "PLAY")
        if str(btn.label) != want:
            btn.label = want

    def on_unmount(self) -> None:
        ticker = getattr(self, "_ticker", None)
        if ticker is not None:
            ticker.stop()
        self.engine.close()


def main() -> None:
    import sys

    from . import __version__
    if any(a in ("--version", "-V") for a in sys.argv[1:]):
        print(f"Termux PL {__version__}")
        return
    TermuxPL().run()


if __name__ == "__main__":
    main()
