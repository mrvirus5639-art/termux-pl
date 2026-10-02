"""Online search, streaming and downloading via yt-dlp (YouTube)."""
from __future__ import annotations

import os
import urllib.request

from .engine import FFMPEG
from .models import Track

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def available() -> bool:
    return yt_dlp is not None


def search(query: str, limit: int = 15) -> list[Track]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed (pip install yt-dlp)")
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
            "skip_download": True, "logger": _QuietLogger(), "no_color": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    out: list[Track] = []
    for i, e in enumerate(info.get("entries") or []):
        if not e:
            continue
        vid = e.get("id")
        url = e.get("url") or e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={vid}"
        thumbs = e.get("thumbnails") or []
        thumb = thumbs[-1]["url"] if thumbs else (f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "")
        out.append(Track(source=url, title=e.get("title") or "", uploader=e.get("channel") or e.get("uploader") or "",
                         artist=e.get("channel") or e.get("uploader") or "",
                         duration=float(e.get("duration") or 0), kind="online",
                         thumbnail=thumb, index=i))
    return out


def resolve_stream(track: Track) -> tuple[str, dict]:
    """Return (direct audio URL, http headers) and fill in stream details."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best",
            "skip_download": True, "logger": _QuietLogger(), "no_color": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.source, download=False)
    url = info.get("url")
    if not url:
        fmts = [f for f in info.get("requested_formats") or [] if f.get("url")]
        url = fmts[0]["url"] if fmts else None
    if not url:
        raise RuntimeError("No audio stream found")
    track.duration = float(info.get("duration") or track.duration or 0)
    track.sample_rate = int(info.get("asr") or 0)
    track.codec = (info.get("acodec") or info.get("ext") or "").split(".")[0].upper()
    track.size = int(info.get("filesize") or info.get("filesize_approx") or 0)
    track.bitrate = int(info.get("abr") or 0)
    if info.get("release_year"):
        track.year = str(info["release_year"])
    elif info.get("upload_date"):
        track.year = str(info["upload_date"])[:4]
    if info.get("track"):
        track.title = info["track"]
    if info.get("artist"):
        track.artist = info["artist"]
    if info.get("thumbnail"):
        track.thumbnail = info["thumbnail"]
    return url, dict(info.get("http_headers") or {})


def fetch_bytes(url: str, timeout: float = 8.0, limit: int = 8_000_000) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TermuxPL"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit)
    except Exception:
        return None


def download(track: Track, dest_dir: str, progress=None) -> str:
    """Download best audio, convert to .m4a/.opus as-is, embed tags + cover."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    os.makedirs(dest_dir, exist_ok=True)
    result: dict = {}

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(d.get("downloaded_bytes", 0) / total)
        if d.get("status") == "finished":
            result["file"] = d.get("filename")

    def pp_hook(d):
        if d.get("status") == "finished":
            fp = d.get("info_dict", {}).get("filepath")
            if fp:
                result["file"] = fp

    opts = {
        "quiet": True, "no_warnings": True, "logger": _QuietLogger(), "no_color": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(dest_dir, "%(title).150B [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        **({"ffmpeg_location": FFMPEG} if FFMPEG else {}),
        "writethumbnail": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "best"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.source, download=True)
        if not result.get("file"):
            result["file"] = ydl.prepare_filename(info)
    return result["file"]
