"""Track model shared by the library, online search, queue and history."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    source: str                 # file path, or web page URL for online tracks
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    duration: float = 0.0       # seconds
    kind: str = "local"         # local | online
    sample_rate: int = 0
    codec: str = ""             # e.g. MP3, FLAC, OPUS
    size: int = 0               # bytes
    bitrate: int = 0            # kbps
    uploader: str = ""
    thumbnail: str = ""
    index: int = 0              # position in folder order
    search_key: str = field(default="", repr=False)

    @property
    def key(self) -> str:
        """Stable identity used by history and playlists."""
        return self.source

    @property
    def display_title(self) -> str:
        return self.title or self.source.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def display_artist(self) -> str:
        return self.artist or self.uploader or "Unknown"

    @property
    def is_online(self) -> bool:
        return self.kind == "online"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("search_key", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in names})


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_size(n: int) -> str:
    if not n:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.2f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.2f}TB"
