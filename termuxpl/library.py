"""Local library: fast folder scan, tag cache, and search.

Windows-oriented optimisations
------------------------------
* ``os.scandir`` everywhere: on Windows its DirEntry objects carry the file size
  and mtime from the directory listing itself, so no extra stat() per file.
* A tag cache keyed by normalised path + size + mtime, so mutagen only opens
  files that are new or changed (opening files is the slow part on NTFS,
  especially with Defender scanning every read).
* Search uses a pre-computed, accent-folded, case-folded key per track, so
  "arzte" finds "Ärzte" and paths with backslashes match like forward slashes.
  Multi-word queries are AND-ed and results are ranked.
"""
from __future__ import annotations

import json
import os
import threading
import unicodedata
from pathlib import Path

from .config import AUDIO_EXTS, CACHE_FILE
from .models import Track

try:
    import mutagen
except ImportError:  # pragma: no cover
    mutagen = None


def fold(text: str) -> str:
    """Case- and accent-insensitive form used for searching."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold().replace("\\", "/")


def _first(tags, *keys) -> str:
    if not tags:
        return ""
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if v:
            if isinstance(v, (list, tuple)):
                v = v[0]
            return str(v).strip()
    return ""


def read_tags(path: str) -> dict:
    """Read basic tags + stream info with mutagen (easy interface)."""
    info: dict = {}
    if mutagen is None:
        return info
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is None:
        return info
    tags = f.tags
    info["title"] = _first(tags, "title")
    info["artist"] = _first(tags, "artist", "albumartist")
    info["album"] = _first(tags, "album")
    date = _first(tags, "date", "year", "originaldate")
    info["year"] = date[:4] if date else ""
    info["genre"] = _first(tags, "genre")
    si = getattr(f, "info", None)
    if si is not None:
        info["duration"] = float(getattr(si, "length", 0) or 0)
        info["sample_rate"] = int(getattr(si, "sample_rate", 0) or 0)
        info["bitrate"] = int((getattr(si, "bitrate", 0) or 0) / 1000)
    return info


def write_tags(path: str, fields: dict) -> None:
    """Write title/artist/album/date/genre/tracknumber. Raises on failure."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("Unsupported file type")
    if f.tags is None:
        f.add_tags()
    mapping = {"title": "title", "artist": "artist", "album": "album",
               "year": "date", "genre": "genre", "track": "tracknumber"}
    for k, tag in mapping.items():
        if k not in fields:
            continue
        val = (fields[k] or "").strip()
        try:
            if val:
                f.tags[tag] = [val]
            elif tag in f.tags:
                del f.tags[tag]
        except (KeyError, ValueError, TypeError):
            pass
    f.save()


def read_cover(path: str) -> bytes | None:
    """Embedded cover art, else cover/folder image next to the file."""
    if mutagen is not None:
        try:
            f = mutagen.File(path)
        except Exception:
            f = None
        if f is not None:
            pics = getattr(f, "pictures", None)  # FLAC
            if pics:
                return pics[0].data
            tags = f.tags
            if tags is not None:
                try:
                    for k in tags.keys():  # ID3 APIC
                        if str(k).startswith("APIC"):
                            return tags[k].data
                except Exception:
                    pass
                try:
                    covr = tags.get("covr")  # MP4
                    if covr:
                        return bytes(covr[0])
                except Exception:
                    pass
                try:  # Ogg / Opus
                    import base64
                    from mutagen.flac import Picture
                    b64 = tags.get("metadata_block_picture")
                    if b64:
                        return Picture(base64.b64decode(b64[0])).data
                except Exception:
                    pass
    folder = Path(path).parent
    for name in ("cover", "folder", "front", "album", "Cover", "Folder"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = folder / f"{name}{ext}"
            if p.is_file():
                try:
                    return p.read_bytes()
                except OSError:
                    return None
    return None


def _walk(root: str):
    """Iterative scandir walk yielding (path, size, mtime) in folder order."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name.casefold())
        except OSError:
            continue
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not e.name.startswith("."):
                        subdirs.append(e.path)
                elif os.path.splitext(e.name)[1].lower() in AUDIO_EXTS:
                    st = e.stat()
                    yield e.path, st.st_size, int(st.st_mtime)
            except OSError:
                continue
        stack.extend(reversed(subdirs))


class Library:
    def __init__(self):
        self.tracks: list[Track] = []
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self):
        try:
            self._cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}

    def _save_cache(self):
        try:
            tmp = CACHE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache), encoding="utf-8")
            os.replace(tmp, CACHE_FILE)
        except OSError:
            pass

    def scan(self, dirs: list[str], progress=None) -> list[Track]:
        tracks: list[Track] = []
        new_cache: dict[str, dict] = {}
        seen: set[str] = set()
        for root in dirs:
            if not root or not os.path.isdir(root):
                continue
            for path, size, mtime in _walk(root):
                nkey = os.path.normcase(os.path.abspath(path))
                if nkey in seen:
                    continue
                seen.add(nkey)
                c = self._cache.get(nkey)
                if not c or c.get("size") != size or c.get("mtime") != mtime:
                    c = read_tags(path)
                    c["size"] = size
                    c["mtime"] = mtime
                new_cache[nkey] = c
                t = Track(source=path, kind="local", size=size,
                          codec=os.path.splitext(path)[1][1:].upper(),
                          **{k: c.get(k) or (0 if k in ("duration", "sample_rate", "bitrate") else "")
                             for k in ("title", "artist", "album", "year", "genre",
                                       "duration", "sample_rate", "bitrate")})
                if not t.title:
                    t.title = os.path.splitext(os.path.basename(path))[0]
                t.index = len(tracks)
                t.search_key = fold(" ".join((t.title, t.artist, t.album, t.genre, path)))
                tracks.append(t)
                if progress and len(tracks) % 200 == 0:
                    progress(len(tracks))
        with self._lock:
            self.tracks = tracks
            self._cache = new_cache
        self._save_cache()
        return tracks

    def refresh_track(self, t: Track) -> None:
        """Re-read tags of one file after editing it."""
        info = read_tags(t.source)
        for k, v in info.items():
            setattr(t, k, v)
        if not t.title:
            t.title = os.path.splitext(os.path.basename(t.source))[0]
        t.search_key = fold(" ".join((t.title, t.artist, t.album, t.genre, t.source)))
        try:
            st = os.stat(t.source)
            nkey = os.path.normcase(os.path.abspath(t.source))
            self._cache[nkey] = {**info, "size": st.st_size, "mtime": int(st.st_mtime)}
            self._save_cache()
        except OSError:
            pass

    def find(self, source: str) -> Track | None:
        n = os.path.normcase(os.path.abspath(source))
        for t in self.tracks:
            if os.path.normcase(os.path.abspath(t.source)) == n:
                return t
        return None

    def search(self, query: str, tracks: list[Track] | None = None) -> list[Track]:
        tracks = self.tracks if tracks is None else tracks
        terms = fold(query).split()
        if not terms:
            return list(tracks)
        scored = []
        for t in tracks:
            key = t.search_key
            if all(term in key for term in terms):
                title = fold(t.title)
                artist = fold(t.artist)
                score = 0
                for term in terms:
                    if title.startswith(term):
                        score += 6
                    elif term in title:
                        score += 4
                    if artist.startswith(term):
                        score += 3
                    elif term in artist:
                        score += 2
                scored.append((-score, t.index, t))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in scored]


def sort_tracks(tracks: list[Track], mode: str) -> list[Track]:
    if mode == "title":
        return sorted(tracks, key=lambda t: fold(t.display_title))
    if mode == "artist":
        return sorted(tracks, key=lambda t: (fold(t.artist), fold(t.album), fold(t.display_title)))
    if mode == "duration":
        return sorted(tracks, key=lambda t: t.duration)
    return sorted(tracks, key=lambda t: t.index)
