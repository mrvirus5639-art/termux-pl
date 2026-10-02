"""Custom Textual widgets that draw the player's panels."""
from __future__ import annotations

import math

import numpy as np
from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from . import art
from .lyrics import Lyrics
from .models import Track, fmt_size

CYAN = "#38bdf8"
MAGENTA = "#e879f9"
DIM = "#64748b"
WHITE = "#f8fafc"


class ArtView(Widget):
    """Cover art (braille-dithered) or a spinning dot-matrix disc."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cover: bytes | None = None
        self.angle = 0.0
        self._cache_key = None
        self._cache_mask = None

    def set_cover(self, data: bytes | None) -> None:
        self.cover = data
        self._cache_key = None
        self.refresh()

    def spin(self, delta: float) -> None:
        if self.cover is None:
            self.angle = (self.angle + delta) % (2 * math.pi)
            self.refresh()

    def render(self):
        w, h = self.size.width, self.size.height
        if w < 4 or h < 3:
            return Text("")
        if self.cover is not None:
            key = (id(self.cover), w, h)
            if key != self._cache_key:
                self._cache_mask = art.image_mask(self.cover, w, h)
                self._cache_key = key
            if self._cache_mask is not None:
                return art.mask_to_text(self._cache_mask)
        return art.mask_to_text(art.disc_mask(w, h, self.angle))


class Separator(Widget):
    def render(self):
        h = self.size.height
        return Text("\n".join([" :  : "] * h), style=MAGENTA)


class InfoView(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.track: Track | None = None
        self.status = ""
        self.extra = ""

    def show(self, track: Track | None, status: str = "", extra: str = "") -> None:
        self.track, self.status, self.extra = track, status, extra
        self.refresh()

    def render(self):
        w, h = self.size.width, self.size.height
        t = self.track
        if t is None:
            msg = self.status or "Currently No Track Loaded"
            pad = max(0, h // 2 - 1)
            return Text("\n" * pad + msg, style=Style(color=CYAN), justify="center")
        if t.is_online:
            kind, loc = "online stream", "youtube"
        else:
            kind = "local file"
            loc = t.source.replace("\\", "/").rsplit("/", 2)
            loc = "/".join(loc[-2:-1]) or t.source
        sr = f"{t.sample_rate}Hz" if t.sample_rate else "-"
        rows = [
            ("Name", t.display_title),
            ("Artist", t.display_artist),
            ("album", t.album or "-"),
            ("year", t.year or "-"),
            ("sampling", sr),
            ("bitrate", f"{t.bitrate} kbps" if t.bitrate else "-"),
            ("type", kind),
            ("format", t.codec or "-"),
            ("file size", fmt_size(t.size)),
            ("location", loc),
        ]
        if self.extra:
            rows.append(("output", self.extra))
        text = Text(no_wrap=True, overflow="ellipsis")
        label_w = 10
        val_w = max(4, w - label_w - 3)
        for i, (k, v) in enumerate(rows[:max(1, h - (1 if self.status else 0))]):
            v = str(v)
            if len(v) > val_w:
                v = v[: val_w - 3] + "..."
            text.append(f"{k:<{label_w}}", Style(color=MAGENTA))
            text.append(": ", Style(color=DIM))
            text.append(v, Style(color=CYAN, bold=(i == 0)))
            text.append("\n")
        if self.status:
            text.append(self.status[:w], Style(color="#fde047"))
        return text


class SpectrumView(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bands = np.zeros(32)

    def set_bands(self, bands: np.ndarray) -> None:
        self.bands = bands
        self.refresh()

    def render(self):
        return art.spectrum_text(self.bands, self.size.width, self.size.height)


class LyricsView(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.lyrics: Lyrics | None = None
        self.message = ""
        self.position = 0.0
        self.duration = 0.0
        self._last_idx = None

    def set_lyrics(self, lyrics: Lyrics | None, message: str = "") -> None:
        self.lyrics, self.message = lyrics, message
        self._last_idx = None
        self.refresh()

    def tick(self, pos: float, duration: float) -> None:
        self.position, self.duration = pos, duration
        idx = self._current()
        if idx != self._last_idx:
            self._last_idx = idx
            self.refresh()

    def _current(self) -> int:
        ly = self.lyrics
        if not ly or not ly.lines:
            return -1
        if ly.synced:
            return ly.index_at(self.position + 0.3)
        if self.duration:
            return int(self.position / self.duration * len(ly.lines))
        return 0

    def render(self):
        w, h = self.size.width, self.size.height
        ly = self.lyrics
        if not ly or not ly.lines:
            pad = max(0, h // 2 - 1)
            return Text("\n" * pad + (self.message or "No lyrics"), style=Style(color=DIM), justify="center")
        idx = self._current()
        center = h // 2
        start = max(0, idx - center) if ly.synced or idx >= 0 else 0
        if ly.synced and idx < 0:
            start = 0
        lines = ly.lines[start:start + h]
        text = Text(justify="center", no_wrap=True, overflow="ellipsis")
        for i, (_, line) in enumerate(lines):
            real = start + i
            if real == idx and ly.synced:
                style = Style(color=WHITE, bold=True)
            elif ly.synced and real < idx:
                style = Style(color="#6b7f99")
            else:
                style = Style(color=CYAN)
            line = line or "♪"
            if len(line) > w:
                line = line[: max(1, w - 1)] + "…"
            text.append(line, style)
            if i < len(lines) - 1:
                text.append("\n")
        return text


class IdleView(Widget):
    """Right-hand panel when lyrics are hidden: cassette art or big spectrum."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.phase = 0.0
        self.bands: np.ndarray | None = None

    def tick(self, bands: np.ndarray | None, spin: float) -> None:
        self.bands = bands
        self.phase += spin
        self.refresh()

    def render(self):
        w, h = self.size.width, self.size.height
        if self.bands is not None and self.bands.any():
            return art.spectrum_text(self.bands, w, h)
        return art.mask_to_text(art.cassette_mask(w, h, self.phase))


class WaveView(Widget):
    """Waveform progress bar; click anywhere to seek."""

    class Seek(Message):
        def __init__(self, fraction: float):
            super().__init__()
            self.fraction = fraction

    def __init__(self, **kw):
        super().__init__(**kw)
        self.levels: np.ndarray | None = None
        self.progress = 0.0

    def set_levels(self, levels) -> None:
        self.levels = levels
        self.refresh()

    def set_progress(self, p: float) -> None:
        if abs(p - self.progress) * max(self.size.width, 1) >= 0.5 or p == 0:
            self.progress = p
            self.refresh()

    def render(self):
        return art.waveform_text(self.levels, self.size.width, self.size.height, self.progress)

    def on_click(self, event: events.Click) -> None:
        if self.size.width:
            self.post_message(self.Seek(min(max(event.x / self.size.width, 0.0), 1.0)))


class VolumeView(Widget):
    class Changed(Message):
        def __init__(self, volume: int):
            super().__init__()
            self.volume = volume

    PREFIX = "VOLUME BAR:["

    def __init__(self, **kw):
        super().__init__(**kw)
        self.volume = 70
        self.flags = ""

    def set(self, volume: int, flags: str = "") -> None:
        self.volume, self.flags = volume, flags
        self.refresh()

    def _bar_width(self) -> int:
        return max(5, self.size.width - len(self.PREFIX) - 6)

    def render(self):
        bw = self._bar_width()
        filled = round(self.volume / 100 * bw)
        t = Text(no_wrap=True)
        t.append(self.PREFIX, Style(color=MAGENTA))
        t.append("#" * filled, Style(color=MAGENTA))
        t.append("-" * (bw - filled), Style(color="#7a4d6e"))
        t.append("] ", Style(color=MAGENTA))
        t.append(f"{self.volume}%", Style(color=CYAN))
        if self.flags and self.size.height > 1:
            t.append("\n" + self.flags, Style(color=DIM))
        return t

    def on_click(self, event: events.Click) -> None:
        if event.y != 0:
            return
        x = event.x - len(self.PREFIX)
        bw = self._bar_width()
        if 0 <= x <= bw:
            self.post_message(self.Changed(round(x / bw * 100)))

    def on_mouse_scroll_up(self, event) -> None:
        self.post_message(self.Changed(min(100, self.volume + 5)))

    def on_mouse_scroll_down(self, event) -> None:
        self.post_message(self.Changed(max(0, self.volume - 5)))
