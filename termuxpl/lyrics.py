"""Synced lyrics: local .lrc files first, then the free LRCLIB API (lrclib.net)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import LYRICS_CACHE_DIR
from .models import Track

API = "https://lrclib.net/api"
_TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_NOISE = re.compile(
    r"[\(\[\{][^\)\]\}]*(official|lyric|video|audio|visuali[sz]er|hd|hq|4k|remaster|live|"
    r"slowed|reverb|sped|explicit|clean|music)[^\)\]\}]*[\)\]\}]", re.I)


@dataclass
class Lyrics:
    lines: list[tuple[float, str]]  # (time, text); time = -1 for unsynced
    synced: bool
    source: str = ""

    def index_at(self, t: float) -> int:
        if not self.synced:
            return -1
        lo, hi = 0, len(self.lines) - 1
        ans = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.lines[mid][0] <= t:
                ans = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return ans


def parse_lrc(text: str) -> Lyrics:
    out: list[tuple[float, str]] = []
    plain: list[str] = []
    offset = 0.0
    for raw in text.splitlines():
        m_off = re.match(r"\[offset:\s*([+-]?\d+)\]", raw.strip(), re.I)
        if m_off:
            offset = int(m_off.group(1)) / 1000.0
            continue
        stamps = _TS.findall(raw)
        body = _TS.sub("", raw).strip()
        if stamps:
            for mm, ss in stamps:
                out.append((int(mm) * 60 + float(ss) - offset, body))
        elif raw.strip() and not re.match(r"^\[[a-z]+:.*\]$", raw.strip(), re.I):
            plain.append(raw.strip())
    if out:
        out.sort(key=lambda x: x[0])
        return Lyrics(out, True)
    return Lyrics([(-1.0, line) for line in plain], False)


def clean_query(track: Track) -> tuple[str, str]:
    """Best-effort (artist, title) for online videos like 'Artist - Song (Lyrics)'."""
    title = _NOISE.sub("", track.title or "").strip()
    title = re.sub(r"\s*(ft\.?|feat\.?)\s.*$", "", title, flags=re.I).strip()
    artist = track.artist or ""
    if " - " in title:
        a, t = title.split(" - ", 1)
        if track.is_online or not artist:
            artist, title = a.strip(), t.strip()
    artist = re.sub(r"\s*(-\s*Topic|VEVO)$", "", artist, flags=re.I).strip()
    return artist, title.strip(" -|")


def _get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "TermuxPL/1.0 (terminal music player)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _cache_path(track: Track):
    h = hashlib.sha1(track.key.encode("utf-8", "replace")).hexdigest()[:20]
    return LYRICS_CACHE_DIR / f"{h}.lrc"


def fetch(track: Track) -> Lyrics | None:
    # 1. .lrc next to a local file
    if not track.is_online:
        lrc = os.path.splitext(track.source)[0] + ".lrc"
        if os.path.isfile(lrc):
            try:
                with open(lrc, encoding="utf-8", errors="replace") as fh:
                    ly = parse_lrc(fh.read())
                ly.source = "local .lrc"
                return ly
            except OSError:
                pass
    # 2. cache
    cp = _cache_path(track)
    if cp.exists():
        text = cp.read_text(encoding="utf-8", errors="replace")
        if text.strip() == "#none":
            return None
        ly = parse_lrc(text)
        ly.source = "lrclib (cached)"
        return ly
    # 3. LRCLIB
    artist, title = clean_query(track)
    data = None
    if artist and title:
        params = {"artist_name": artist, "track_name": title}
        if track.album and not track.is_online:
            params["album_name"] = track.album
        if track.duration:
            params["duration"] = int(track.duration)
        data = _get_json(f"{API}/get?{urllib.parse.urlencode(params)}")
        if data and not (data.get("syncedLyrics") or data.get("plainLyrics")):
            data = None
    if data is None:
        q = f"{artist} {title}".strip()
        res = _get_json(f"{API}/search?{urllib.parse.urlencode({'q': q})}")
        if isinstance(res, list) and res:
            def score(r):
                s = 2 if r.get("syncedLyrics") else (1 if r.get("plainLyrics") else -9)
                if track.duration and r.get("duration"):
                    s -= min(abs(float(r["duration"]) - track.duration) / 10, 3)
                return s
            data = max(res, key=score)
    if data is None:
        return None  # network problem: do not cache, try again next time
    text = data.get("syncedLyrics") or data.get("plainLyrics") or ""
    try:
        cp.write_text(text if text.strip() else "#none", encoding="utf-8")
    except OSError:
        pass
    if not text.strip():
        return None
    ly = parse_lrc(text)
    ly.source = "lrclib"
    return ly
