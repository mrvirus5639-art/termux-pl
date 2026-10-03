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


EDIT_TAGS = {"title": "title", "artist": "artist", "album": "album",
             "year": "date", "genre": "genre", "track": "tracknumber"}


def read_edit_tags(path: str) -> dict:
    """Tags exactly as stored in the file, for the metadata editor.

    Unlike read_tags() this keeps the full date ("1996-05-01", not "1996") and
    the track number ("7/12"), so saving the editor never shortens or drops them.
    """
    out = {k: "" for k in EDIT_TAGS}
    if mutagen is None:
        return out
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    if f is None or f.tags is None:
        return out
    for key, tag in EDIT_TAGS.items():
        try:
            v = f.tags.get(tag)
        except Exception:
            v = None
        if v:
            out[key] = str(v[0] if isinstance(v, (list, tuple)) else v).strip()
    return out


def write_tags(path: str, fields: dict) -> None:
    """Write title/artist/album/date/genre/tracknumber. Raises on failure."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("Unsupported file type")
    if f.tags is None:
        f.add_tags()
    for k, tag in EDIT_TAGS.items():
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


# Windows cloud placeholders (OneDrive "Files On-Demand" and similar). Opening
# such a file to read its tags makes Windows download it, so a scan of a synced
# Music folder would pull the whole library down. We list them by file name and
# read the tags once they are actually on disk.
_CLOUD_ATTRS = 0x1000 | 0x40000 | 0x400000  # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS


def _is_cloud_only(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_ATTRS)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def index_track(t: Track) -> None:
    """Fill the pre-folded search fields used by Library.search."""
    t.search_title = fold(t.title)
    t.search_artist = fold(t.artist)
    t.search_key = fold(" ".join((t.title, t.artist, t.album, t.genre, t.source)))


def _walk(root: str):
    """Iterative scandir walk yielding (path, size, mtime, cloud_only) in folder order."""
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
                    yield e.path, st.st_size, int(st.st_mtime), _is_cloud_only(st)
            except OSError:
                continue
        stack.extend(reversed(subdirs))


class Library:
    def __init__(self):
        self.tracks: list[Track] = []
        self._by_path: dict[str, Track] = {}
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
            for path, size, mtime, cloud in _walk(root):
                nkey = _norm(path)
                if nkey in seen:
                    continue
                seen.add(nkey)
                c = self._cache.get(nkey)
                stale = (not c or c.get("size") != size or c.get("mtime") != mtime
                         or (c.get("cloud") and not cloud))
                if stale:
                    if cloud:
                        c = {"cloud": True}       # don't trigger a download just for tags
                    else:
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
                index_track(t)
                tracks.append(t)
                if progress and len(tracks) % 200 == 0:
                    progress(len(tracks))
        with self._lock:
            self.tracks = tracks
            self._by_path = {_norm(t.source): t for t in tracks}
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
        index_track(t)
        try:
            st = os.stat(t.source)
            nkey = _norm(t.source)
            self._cache[nkey] = {**info, "size": st.st_size, "mtime": int(st.st_mtime)}
            self._save_cache()
        except OSError:
            pass

    def find(self, source: str) -> Track | None:
        if not source or source.startswith(("http://", "https://")):
            return None
        return self._by_path.get(_norm(source))

    def search(self, query: str, tracks: list[Track] | None = None) -> list[Track]:
        tracks = self.tracks if tracks is None else tracks
        terms = fold(query).split()
        if not terms:
            return list(tracks)
        scored = []
        for t in tracks:
            key = t.search_key
            if all(term in key for term in terms):
                title = t.search_title
                artist = t.search_artist
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
