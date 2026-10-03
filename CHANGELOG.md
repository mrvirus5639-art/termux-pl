# Changelog

All notable changes to Termux PL are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/):
MAJOR for breaking changes, MINOR for new features, PATCH for bug fixes.

Add your changes under **Unreleased** as you work. `python scripts/release.py`
turns that section into the next version.

## [Unreleased]

### Fixed
- A damaged audio file could freeze playback after a few seconds on Windows (ffmpeg's
  error output filled up and blocked it).
- Saving in the metadata editor deleted the track number and cut full dates such as
  `1996-05-01` down to `1996`. It now only writes the fields you change.
- The queue's title column was squeezed to a few characters when the queue first
  appeared, and lists could scroll sideways.
- Small windows (down to 80×24) scrolled and squeezed the track info; the layout now
  adapts and the art stays square.
- Pressing space while a track was still loading started it a second time.
- If the audio device disappears (headphones switched off, USB DAC unplugged), the
  player reconnects to the default device instead of going silent.
- Several unplayable files in a row no longer make the player skip through the whole
  list; it stops after three and says why. Error messages are shorter and clearer.
- Renaming a playlist to an existing name overwrote that playlist.
- On OneDrive-synced folders, scanning no longer downloads every cloud-only file just
  to read its tags.
- A cover or waveform from the previous track could appear after switching quickly.

### Changed
- Deleting a playlist and clearing history ask for a second press.
- Local search waits for a short pause in typing and is faster on big libraries.
- Tracks played from playlists or history show their full details (album, format…).
- The playlist library list says when it only shows the first 500 matches.
- Online tracks longer than 15 minutes no longer download a second time just to
  draw the waveform.

## [1.0.1] - 2026-10-03

### Added
- Switch the artwork between the album cover and the rotating CD with `c`, by
  clicking the artwork, or in Settings. The choice is remembered. Tracks without
  cover art still show the CD.

### Fixed
- An error in the audio callback no longer stops sound until restart; that
  block is played as silence instead.
- Quitting could print a "No nodes match" error from the screen refresh timer.

## [1.0.0] - 2026-10-02

### Added
- Keyboard- and mouse-driven terminal UI: braille cover art, track info, spectrum
  visualizer, waveform progress bar with click-to-seek, volume bar, search, results and queue.
- Local library with fast `scandir` scanning, a tag cache, sort modes and
  accent-insensitive search (`arzte` finds `Ärzte`).
- Online search, streaming and downloading via yt-dlp.
- Synced lyrics from `.lrc` files or LRCLIB, with a lyrics toggle (`l`).
- Playlists menu for building `.m3u8` playlists from local or downloaded tracks.
- Metadata editor with AcoustID fingerprint lookup.
- Listening history with top tracks and "add top 10/25 to queue".
- Shuffle to a random next title (`x`), shuffle mode, and repeat.
- Stereo output, mono option, and EBU R128 loudness normalization.
- Settings menu, including adding music folders by path.
- Settings, history and playlists from the earlier TuneTerm build (`~/.tuneterm`)
  are imported automatically on first run.
- Built-in user manual (`?` / `F1`) and `termuxpl --version`.

### Fixed
- ffmpeg is now found even when it isn't on PATH (conda `Library\bin`, winget,
  scoop, choco), with a bundled `imageio-ffmpeg` fallback. `TERMUXPL_FFMPEG`
  overrides the choice.

[Unreleased]: https://github.com/mrvirus5639-art/termux-pl/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/mrvirus5639-art/termux-pl/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/mrvirus5639-art/termux-pl/releases/tag/v1.0.0
