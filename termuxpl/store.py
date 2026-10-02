"""Playlists (.m3u8 files) and listening history (JSON)."""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter

from .config import HISTORY_FILE, PLAYLIST_DIR
from .models import Track


# --------------------------------------------------------------- playlists
def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "playlist"


def list_playlists() -> list[str]:
    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    return sorted((p.stem for p in PLAYLIST_DIR.glob("*.m3u8")), key=str.casefold)


def load_playlist(name: str) -> list[Track]:
    p = PLAYLIST_DIR / f"{_safe(name)}.m3u8"
    tracks: list[Track] = []
    if not p.exists():
        return tracks
    pending: dict = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line == "#EXTM3U":
            continue
        if line.startswith("#EXTINF:"):
            m = re.match(r"#EXTINF:(-?\d+),(.*)", line)
            if m:
                pending["duration"] = max(0, int(m.group(1)))
                body = m.group(2)
                if " - " in body:
                    a, t = body.split(" - ", 1)
                    pending["artist"], pending["title"] = a, t
                else:
                    pending["title"] = body
            continue
        if line.startswith("#"):
            continue
        kind = "online" if line.startswith(("http://", "https://")) else "local"
        tracks.append(Track(source=line, kind=kind, **pending))
        pending = {}
    return tracks


def save_playlist(name: str, tracks: list[Track]) -> str:
    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe(name)
    lines = ["#EXTM3U"]
    for t in tracks:
        label = f"{t.artist} - {t.display_title}" if t.artist else t.display_title
        lines.append(f"#EXTINF:{int(t.duration or -1)},{label}")
        lines.append(t.source)
    (PLAYLIST_DIR / f"{safe}.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return safe


def delete_playlist(name: str) -> None:
    try:
        os.remove(PLAYLIST_DIR / f"{_safe(name)}.m3u8")
    except OSError:
        pass


def rename_playlist(old: str, new: str) -> str:
    tracks = load_playlist(old)
    safe = save_playlist(new, tracks)
    if _safe(old) != safe:
        delete_playlist(old)
    return safe


# ----------------------------------------------------------------- history
class History:
    MAX = 5000

    def __init__(self):
        self.entries: list[dict] = []
        try:
            self.entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.entries = []

    def add(self, track: Track) -> None:
        self.entries.append({"ts": time.time(), "track": track.to_dict()})
        self.entries = self.entries[-self.MAX:]
        self.save()

    def save(self) -> None:
        try:
            tmp = HISTORY_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries), encoding="utf-8")
            os.replace(tmp, HISTORY_FILE)
        except OSError:
            pass

    def clear(self) -> None:
        self.entries = []
        self.save()

    def recent(self, n: int = 200) -> list[tuple[float, Track]]:
        return [(e["ts"], Track.from_dict(e["track"])) for e in reversed(self.entries[-n:])]

    def top(self, n: int = 50, since_days: int | None = None) -> list[tuple[int, Track]]:
        cutoff = time.time() - since_days * 86400 if since_days else 0
        counts: Counter = Counter()
        latest: dict[str, dict] = {}
        for e in self.entries:
            if e["ts"] < cutoff:
                continue
            k = e["track"]["source"]
            counts[k] += 1
            latest[k] = e["track"]
        return [(c, Track.from_dict(latest[k])) for k, c in counts.most_common(n)]
