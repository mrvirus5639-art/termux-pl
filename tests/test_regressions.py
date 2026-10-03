"""Regression tests for bugs found in the v1.0.1 review."""
import asyncio
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from termuxpl import engine as engine_mod
from termuxpl import library as library_mod
from termuxpl import store
from termuxpl.library import Library, read_edit_tags, write_tags
from termuxpl.models import Track

FFMPEG = engine_mod.FFMPEG
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")


def _make_mp3(path, seconds=3, tags=None):
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-b:a", "64k", str(path)], check=True)
    if tags:
        from mutagen.easyid3 import EasyID3
        t = EasyID3()
        for k, v in tags.items():
            t[k] = [v]
        t.save(str(path))


# ---------------------------------------------------------------- metadata
@needs_ffmpeg
def test_metadata_editor_keeps_full_date_and_track_number(tmp_path):
    f = tmp_path / "song.mp3"
    _make_mp3(f, tags={"title": "Song", "date": "1996-05-01", "tracknumber": "7/12"})
    orig = read_edit_tags(str(f))
    assert orig["year"] == "1996-05-01" and orig["track"] == "7/12"
    # Saving only a changed title must leave the date and track number alone.
    write_tags(str(f), {"title": "New title"})
    after = read_edit_tags(str(f))
    assert after == {**orig, "title": "New title"}


def test_metadata_screen_only_writes_changed_fields(tmp_path, monkeypatch):
    from termuxpl import screens
    written = {}
    monkeypatch.setattr(screens, "read_edit_tags", lambda p: {
        "title": "T", "artist": "A", "album": "", "year": "1996-05-01", "genre": "", "track": "7/12"})
    monkeypatch.setattr(screens, "write_tags", lambda p, fields: written.update(fields))
    from textual.app import App

    class Host(App):
        def on_mount(self):
            self.push_screen(screens.MetadataScreen(Track(source=str(tmp_path / "x.mp3")), ""))

    async def run():
        app = Host()
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.2)
            app.screen.query_one("#md-genre").value = "Punk"
            await pilot.click("#md-save")
            await pilot.pause(0.2)
    asyncio.run(run())
    assert written == {"genre": "Punk"}


# ------------------------------------------------------------- engine
@needs_ffmpeg
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses Linux F_SETPIPE_SZ")
def test_noisy_ffmpeg_stderr_does_not_freeze_playback(tmp_path, monkeypatch):
    """A damaged file makes ffmpeg print an error per frame. With Windows' 4 KB
    pipe buffer, not draining stderr froze playback after a few seconds."""
    import fcntl
    src = tmp_path / "long.mp3"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=500:duration=60", "-b:a", "32k", str(src)], check=True)
    b = bytearray(src.read_bytes())
    for i in range(4000, len(b), 70):
        b[i] ^= 0xFF
    bad = tmp_path / "corrupt.mp3"
    bad.write_bytes(b)

    real = subprocess.Popen

    class SmallPipePopen(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            if self.stderr is not None:
                fcntl.fcntl(self.stderr.fileno(), 1031, 4096)
    monkeypatch.setattr(engine_mod.subprocess, "Popen", SmallPipePopen)

    e = engine_mod.AudioEngine(normalize=False)
    try:
        e.load(str(bad), duration=60)
        time.sleep(9)
        assert e.position > 8, f"playback stalled at {e.position:.1f}s"
    finally:
        e.close()


@needs_ffmpeg
def test_unreadable_file_reports_error_and_finishes(tmp_path):
    bad = tmp_path / "not_audio.mp3"
    bad.write_bytes(b"this is not audio" * 100)
    e = engine_mod.AudioEngine(normalize=False)
    try:
        e.load(str(bad))
        for _ in range(40):
            if e.finished:
                break
            time.sleep(0.1)
        assert e.finished and e.error
        assert e.position < 0.5
    finally:
        e.close()


def test_ensure_output_reopens_a_dead_stream(monkeypatch):
    e = engine_mod.AudioEngine.__new__(engine_mod.AudioEngine)

    class Dead:
        active = False
        closed = False

        def close(self):
            self.closed = True

    class Alive:
        active = True

    dead = Dead()
    e._stream = dead
    e._last_reopen = 0.0
    monkeypatch.setattr(e, "_open_stream", lambda: Alive(), raising=False)
    assert e.ensure_output() is True
    assert dead.closed and isinstance(e._stream, Alive)
    assert e.ensure_output() is False          # healthy stream: nothing to do


# ------------------------------------------------------------- library
@needs_ffmpeg
def test_cloud_only_files_are_listed_without_reading_tags(tmp_path, monkeypatch):
    _make_mp3(tmp_path / "a.mp3", tags={"title": "Real Title"})
    monkeypatch.setattr(library_mod, "_is_cloud_only", lambda st: True)
    reads = []
    monkeypatch.setattr(library_mod, "read_tags", lambda p: reads.append(p) or {})
    lib = Library()
    tracks = lib.scan([str(tmp_path)])
    assert [t.title for t in tracks] == ["a"] and reads == []
    # Once the file is downloaded (no longer cloud-only) its tags are read.
    monkeypatch.setattr(library_mod, "_is_cloud_only", lambda st: False)
    monkeypatch.setattr(library_mod, "read_tags", lambda p: {"title": "Real Title"})
    assert [t.title for t in lib.scan([str(tmp_path)])] == ["Real Title"]


@needs_ffmpeg
def test_find_uses_index_and_ignores_urls(tmp_path):
    _make_mp3(tmp_path / "a.mp3")
    lib = Library()
    lib.scan([str(tmp_path)])
    assert lib.find(str(tmp_path / "a.mp3")) is lib.tracks[0]
    assert lib.find(os.path.join(str(tmp_path), ".", "a.mp3")) is lib.tracks[0]
    assert lib.find("https://www.youtube.com/watch?v=x") is None


# ------------------------------------------------------------- app
def _app(tmp_path, music=None):
    from termuxpl.app import TermuxPL
    app = TermuxPL()
    app.cfg.music_dirs = [str(music)] if music else []
    return app


@needs_ffmpeg
def test_queue_columns_get_real_width_when_queue_appears(tmp_path):
    for n in range(3):
        _make_mp3(tmp_path / f"{n}.mp3", seconds=1)

    async def run():
        app = _app(tmp_path, tmp_path)
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(1.0)
            app.query_one("#results").focus()
            await pilot.press("A")
            await pilot.pause(0.4)
            q = app.query_one("#queue")
            title_w = list(q.columns.values())[1].width
            assert title_w > q.size.width * 0.4, (title_w, q.size.width)
    asyncio.run(run())


def test_space_while_loading_does_not_start_a_second_load(tmp_path):
    async def run():
        app = _app(tmp_path)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            app.local_view = [Track(source=str(tmp_path / "x.mp3"), title="x")]
            app._loading = True
            token = app._play_token
            app.query_one("#results").focus()
            await pilot.press("space")
            assert app._play_token == token
    asyncio.run(run())


def test_playback_stops_after_three_unplayable_tracks(tmp_path):
    async def run():
        app = _app(tmp_path)
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            started = []
            app.play = lambda t, push_back=True, auto=False: started.append(t)
            app.context = [Track(source=f"/missing/{i}.mp3", title=str(i)) for i in range(10)]
            app.context_idx = 0
            for _ in range(5):
                app._track_ended(played=False)
            assert len(started) == 2          # skipped twice, then stopped
            assert "Stopped" in str(app.query_one("#status").render())
    asyncio.run(run())


# ------------------------------------------------------------- menus
def test_rename_refuses_to_overwrite_existing_playlist(tmp_path):
    from termuxpl.screens import PlaylistScreen
    store.save_playlist("Keep", [Track(source="/m/keep.mp3", title="keep")])
    store.save_playlist("Other", [])

    async def run():
        app = _app(tmp_path)
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(PlaylistScreen([], [], None))
            await pilot.pause(0.3)
            scr = app.screen
            scr.open_playlist("Other")
            scr.query_one("#pl-name").value = "Keep"
            await pilot.click("#pl-rename")
            await pilot.pause(0.2)
    asyncio.run(run())
    assert [t.title for t in store.load_playlist("Keep")] == ["keep"]
    assert "Other" in store.list_playlists()
    store.delete_playlist("Keep")
    store.delete_playlist("Other")


def test_delete_playlist_needs_second_press(tmp_path):
    from termuxpl.screens import PlaylistScreen
    store.save_playlist("Temp", [])

    async def run():
        app = _app(tmp_path)
        async with app.run_test(size=(150, 44)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(PlaylistScreen([], [], None))
            await pilot.pause(0.3)
            app.screen.open_playlist("Temp")
            await pilot.click("#pl-delete")
            await pilot.pause(0.1)
            assert "Temp" in store.list_playlists()
            await pilot.click("#pl-delete")
            await pilot.pause(0.1)
            assert "Temp" not in store.list_playlists()
    asyncio.run(run())
