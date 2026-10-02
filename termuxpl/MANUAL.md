# Termux PL — User Manual

Termux PL is a keyboard-first terminal music player. Everything can also be
done with the mouse: click rows, buttons, the waveform (to seek) and the
volume bar (click or scroll the wheel over it).

## Screen layout

| Area | What it shows |
|---|---|
| **Top panel** | Cover art (or a spinning disc), track details, a live spectrum, and synced lyrics. With lyrics off, the right side becomes a large spectrum visualizer. |
| **Progress bar** | The track's waveform. The bright part has played. `[ elapsed ]-[ length ]` sits on the frame. Click to seek. |
| **Buttons** | `<<<` previous · `PLAY/PAUSE` · `>>>` next |
| **Volume bar** | Click or scroll to set. The second line shows shuffle, repeat, stereo/mono and normalization state. |
| **Search** | `/l:` filters your local library as you type. `/s:` searches online when you press Enter. |
| **Results** | Local audio files, or online results. The playing track's number is yellow. |
| **Queue** | Tracks that play next, before the list continues. |

## Keys

### Playback

| Key | Action |
|---|---|
| `space` | Play / pause (starts the selected track if nothing is loaded) |
| `enter` | Play the selected row |
| `n` / `b` | Next / previous (previous restarts the track if you are past 3 s) |
| `x` | **Shuffle to a random next title** from the current list |
| `z` | Shuffle mode on/off (affects what "next" picks) |
| `r` | Repeat: off → all → one |
| `[` / `]` | Seek −10 s / +10 s |
| `,` / `.` | Seek −5 s / +5 s |
| `+` / `-` | Volume up / down |
| `N` | Loudness normalization on/off (EBU R128, target set in Settings) |
| `m` | Stereo / mono output |
| `l` | Lyrics on/off |

### Library, search and queue

| Key | Action |
|---|---|
| `/` | Jump to the search box (`esc` returns to the list) |
| `o` | Switch between **local** and **online** search (or click the `O`/`L` button) |
| `t` | Sort local files: folder order → title → artist → duration |
| `a` | Add the selected track to the queue |
| `A` | Add every track in the current list to the queue |
| `C` | Clear the queue |
| `d` | Download the selected online result into your downloads folder |
| `tab` | Move focus between panels (search, results, queue, buttons) |
| In the queue: `enter` | Play that queued track now |
| In the queue: `del` | Remove it |
| In the queue: `K` / `J` | Move it up / down |

Typing `/s love me not` in the local box switches to online mode with that
text; `/l ` switches back.

### Menus

| Key | Menu |
|---|---|
| `p` | **Playlists** |
| `e` | **Metadata editor** for the selected local track (or the playing one) |
| `h` | **Listening history** |
| `s` | **Settings** |
| `?` or `F1` | This manual |
| `q` | Quit |

`esc` closes any menu.

## Playlists (`p`)

Three columns: your playlists, the tracks in the selected playlist, and your
library.

1. Type a name in the box under *Your playlists* and press **Create** (or Enter).
2. Filter the library on the right and press **Enter** on a track to add it.
3. Use **+ Now playing** or **+ Queue** to add the current track or the whole queue.
4. In the track list: `del` removes, `ctrl+↑/↓` reorders, Enter plays from that track.
5. **Play** plays the playlist, **Add to queue** appends it.

Playlists are standard `.m3u8` files in `~/.termuxpl/playlists`, so other
players can open them too. Downloaded tracks show up in the library after
the download, so they can go into playlists like any local file.

## Metadata editor (`e`)

Edit Title, Artist, Album, Year, Genre and Track number, then **Save tags**.
Works with MP3, FLAC, OGG/Opus, M4A and other formats mutagen supports.

**Fetch from AcoustID** fingerprints the audio and looks it up in the
AcoustID / MusicBrainz database. Pick a match and press Enter to fill the
form, then Save. This needs:

* a free AcoustID API key (create an *application* at acoustid.org), entered in Settings
* `fpcalc` from Chromaprint on your PATH, or placed in the Termux PL folder

## Listening history (`h`)

Every play is logged with a timestamp.

* **Top tracks** ranks your most-played tracks for all time, 30 days or 7 days.
* **Recently played** lists plays newest first.
* **Add top 10 / top 25 to queue** queues your favourites in one step.
  **Add selected** queues a single row. Enter plays.
* **Clear history** wipes the log.

## Settings (`s`)

* **Music folders**: add a path (pasting a quoted Windows "Copy as path" works),
  or remove one. **Save & rescan** reloads the library.
* **Loudness normalization** and **Target LUFS** (default −14, streaming level).
* **Mono output**: off means stereo.
* **Show lyrics**, **Shuffle mode**.
* **Downloads** folder. It is added to your music folders automatically on the
  first download.
* **AcoustID key**, and the number of **online hits** per search.

## Lyrics

Termux PL looks for lyrics in this order:

1. a `.lrc` file with the same name next to the audio file,
2. the free LRCLIB database (lrclib.net), cached in `~/.termuxpl/lyrics`.

Synced lyrics highlight the current line. Plain lyrics scroll with the song.
Titles like `Artist - Song (Official Video)` are cleaned up before searching.

## Files

Everything lives in `~/.termuxpl` (on Windows `C:\Users\<you>\.termuxpl`):
`config.json`, `history.json`, `library_cache.json`, `playlists\`, `lyrics\`.
Set the `TERMUXPL_HOME` environment variable to move it.

## Troubleshooting

* **"ffmpeg not found"**: install ffmpeg and make sure `ffmpeg -version` works in a new terminal.
* **No sound / "audio output unavailable"**: check your default output device; `pip install --upgrade sounddevice`.
* **Online search or playback fails**: update yt-dlp with `pip install -U yt-dlp`. YouTube changes often.
* **Broken box characters or dots**: use Windows Terminal with a font that has braille glyphs (Cascadia Mono, JetBrains Mono, Fira Code).
