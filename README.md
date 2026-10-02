# Termux PL

[![CI](https://github.com/mrvirus5639-art/termux-pl/actions/workflows/ci.yml/badge.svg)](https://github.com/mrvirus5639-art/termux-pl/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mrvirus5639-art/termux-pl)](https://github.com/mrvirus5639-art/termux-pl/releases)

**A terminal music player for people who prefer control, simplicity, and a keyboard.**

Termux PL is a fast, focused TUI with no unnecessary interface layers. It's keyboard-driven, works with the mouse, and is fully configurable. It has spectrum visualizers, synced lyrics, and online streaming, all inside your terminal.

## Features

- **Local and online playback**: filter your library as you type (`/l:`), or search YouTube and stream (`/s:`)
- **Dot-matrix visuals**: braille cover art (or a spinning disc), a live spectrum, and a waveform progress bar you can click to seek
- **Synced lyrics** from `.lrc` files or LRCLIB, highlighted line by line. Toggle with `l`.
- **Queue**, plus **shuffle to a random next title** (`x`), shuffle mode (`z`) and repeat (`r`)
- **Playlists** menu (`p`) for building `.m3u8` playlists from local or downloaded tracks
- **Metadata editor** (`e`) with **AcoustID** fingerprint lookup
- **Listening history** (`h`): recent plays, top tracks, and one-key "add top 10 to queue"
- **Stereo output with loudness normalization** (EBU R128, `N`), plus a mono option (`m`)
- **Settings menu** (`s`): add music folders by path, downloads folder, AcoustID key, and more
- **Built-in user manual** (`?` / `F1`)
- **Fast search on Windows**: `scandir`-based scanning, a tag cache so restarts are instant, and accent-insensitive matching (`arzte` finds `Ärzte`)

## Install (Windows)

1. **Python 3.10+.** Anaconda works.
2. **ffmpeg**: nothing to do. A bundled copy (`imageio-ffmpeg`) is installed automatically. Termux PL also finds a system ffmpeg from PATH, conda's `Library\bin`, winget, scoop or choco. To force a specific one, set `TERMUXPL_FFMPEG=C:\path\to\ffmpeg.exe`.
3. In this folder, run **`install.bat`**, or:
   ```
   python -m pip install -e .
   ```
4. Start it with **`termuxpl`**, `run.bat`, or `python -m termuxpl`.

Use **Windows Terminal** with a font that has braille glyphs (Cascadia Mono / Cascadia Code are the defaults and work). The old `conhost` console can't draw the dot art.

Optional, for AcoustID lookup: download **Chromaprint `fpcalc.exe`** (acoustid.org/chromaprint) and put it on PATH or in this folder. Then get a free API key (acoustid.org/new-application) and paste it in Settings.

### macOS / Linux
```
pip install -e .        # Linux may also need: sudo apt install ffmpeg libportaudio2
termuxpl
```

## First run

Press `s`, paste a music folder path, then **Save & rescan**. Your `Music` folder is added automatically if it exists. Press `?` for the full manual.

## Keys at a glance

| Key | Action | Key | Action |
|---|---|---|---|
| `space` | play / pause | `/` | search |
| `enter` | play selected | `o` | local ⇄ online |
| `n` / `b` | next / previous | `a` / `A` | queue selected / queue all |
| `x` | shuffle to random next | `C` | clear queue |
| `z` / `r` | shuffle mode / repeat | `d` | download online result |
| `[` `]` / `,` `.` | seek ±10 s / ±5 s | `t` | sort library |
| `+` / `-` | volume | `p` `e` `h` `s` | playlists, metadata, history, settings |
| `l` / `N` / `m` | lyrics / normalize / mono | `?` · `q` | manual · quit |

## How it works

```
source (file or yt-dlp stream URL)
   └─ ffmpeg  [-af loudnorm, pan]  → float32 PCM 48 kHz stereo
        └─ reader thread → bounded queue → PortAudio callback (sounddevice)
                                              ├─ volume
                                              ├─ position clock (sample-accurate)
                                              └─ ring buffer → FFT → spectrum
```

| Module | Role |
|---|---|
| `engine.py` | decoding, playback, seek, volume, normalization, spectrum, waveform |
| `library.py` | folder scan, tag cache, search, tag read/write, cover art |
| `online.py` | yt-dlp search, stream resolving, downloads |
| `lyrics.py` | `.lrc` parsing and LRCLIB lookup |
| `acoustid_lookup.py` | fingerprint and match |
| `store.py` | playlists (`.m3u8`) and listening history |
| `art.py` / `widgets.py` | braille rendering and custom panels |
| `screens.py` | playlists, metadata, history, settings and manual menus |
| `app.py` | layout, key bindings, playback flow |

Settings, history, cache, playlists and lyrics live in `~/.termuxpl` (change it with `TERMUXPL_HOME`).

## Versioning and releases

Termux PL uses [Semantic Versioning](https://semver.org/). The version lives in exactly
one place, `termuxpl/__init__.py`, and every change is recorded in [CHANGELOG.md](CHANGELOG.md).

**Day to day**

1. Make your change, and add a line under `## [Unreleased]` in `CHANGELOG.md`
   (in an `### Added`, `### Changed` or `### Fixed` section).
2. `git add -A` and `git commit -m "Describe the change"`, then `git push`.
   CI runs the tests on Windows and Linux.

**Releasing a version**

```
python scripts/release.py patch --push    # bug fixes        1.0.0 -> 1.0.1
python scripts/release.py minor --push    # new features     1.0.0 -> 1.1.0
python scripts/release.py major --push    # breaking change  1.0.0 -> 2.0.0
```

The script bumps the version, dates the changelog section, commits, tags `vX.Y.Z`
and pushes. The tag starts the **Release** workflow, which runs the tests and
publishes a GitHub Release with a zip, a wheel and the changelog notes.
Add `--dry-run` to preview. Run `termuxpl --version` to see the installed version.

## Troubleshooting

- **Online search fails**: run `pip install -U yt-dlp`. YouTube changes often.
- **"audio output unavailable"**: check the default output device, then `pip install -U sounddevice`.
- **Dots show as boxes**: switch the terminal font to Cascadia Mono, JetBrains Mono or similar.
