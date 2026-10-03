"""Persistent settings stored as JSON in the user's home folder."""
from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(os.environ.get("TERMUXPL_HOME", Path.home() / ".termuxpl"))
CONFIG_FILE = APP_DIR / "config.json"
PLAYLIST_DIR = APP_DIR / "playlists"
CACHE_FILE = APP_DIR / "library_cache.json"
HISTORY_FILE = APP_DIR / "history.json"
LYRICS_CACHE_DIR = APP_DIR / "lyrics"

_LOCK = threading.Lock()

AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma",
    ".aiff", ".aif", ".alac", ".ape", ".wv", ".mka", ".webm",
}


def _migrate_from_tuneterm() -> None:
    """First run after the rename: bring over settings, history and playlists."""
    old = Path.home() / ".tuneterm"
    if "TERMUXPL_HOME" in os.environ or APP_DIR.exists() or not old.is_dir():
        return
    try:
        shutil.copytree(old, APP_DIR)
    except OSError:
        pass


def _default_music_dirs() -> list[str]:
    music = Path.home() / "Music"
    return [str(music)] if music.is_dir() else []


@dataclass
class Config:
    music_dirs: list[str] = field(default_factory=_default_music_dirs)
    download_dir: str = str(Path.home() / "Music" / "Termux PL Downloads")
    volume: int = 70
    normalize: bool = True           # EBU R128 loudness normalization
    loudness_target: float = -14.0   # LUFS
    mono: bool = False               # False = stereo output
    lyrics: bool = True
    art_mode: str = "album"          # album (cover art) | disc (rotating CD)
    shuffle: bool = False
    repeat: str = "off"              # off | all | one
    sort: str = "folder"             # folder | title | artist | duration
    acoustid_key: str = ""
    sample_rate: int = 48000
    online_results: int = 15

    @classmethod
    def load(cls) -> "Config":
        _migrate_from_tuneterm()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
        LYRICS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k) and not k.startswith("_"):
                        setattr(cfg, k, v)
            except (OSError, ValueError):
                pass
        return cfg

    def save(self) -> None:
        with _LOCK:
            data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
