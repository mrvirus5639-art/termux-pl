import termuxpl
from termuxpl.library import Library, fold, sort_tracks
from termuxpl.lyrics import Lyrics, clean_query, parse_lrc
from termuxpl.models import Track, fmt_size, fmt_time
from termuxpl import store


def test_version_is_semver():
    parts = termuxpl.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


def test_fmt_helpers():
    assert fmt_time(65) == "01:05"
    assert fmt_time(3725) == "1:02:05"
    assert fmt_size(0) == "-"
    assert fmt_size(3_407_872) == "3.25MB"


def test_fold_is_accent_and_case_insensitive():
    assert fold("Die ÄRZTE") == "die arzte"
    assert fold(r"C:\Music\x") == "c:/music/x"


def _lib(*specs):
    lib = Library.__new__(Library)
    lib.tracks = []
    for i, (title, artist) in enumerate(specs):
        t = Track(source=f"/m/{i}.mp3", title=title, artist=artist, index=i)
        t.search_key = fold(f"{title} {artist} {t.source}")
        lib.tracks.append(t)
    return lib


def test_search_ranks_title_matches_and_ands_terms():
    lib = _lib(("Kadal Naan Thaan", "Harris Jayaraj"), ("Naan Un", "A.R. Rahman"),
               ("Lovesong", "Die Ärzte"))
    assert [t.title for t in lib.search("naan")] == ["Naan Un", "Kadal Naan Thaan"]
    assert [t.title for t in lib.search("naan rahman")] == ["Naan Un"]
    assert [t.title for t in lib.search("arzte")] == ["Lovesong"]
    assert lib.search("nothing-matches") == []


def test_sort_modes():
    lib = _lib(("b", "z"), ("a", "y"))
    lib.tracks[0].duration, lib.tracks[1].duration = 10, 5
    assert [t.title for t in sort_tracks(lib.tracks, "title")] == ["a", "b"]
    assert [t.title for t in sort_tracks(lib.tracks, "duration")] == ["a", "b"]
    assert [t.title for t in sort_tracks(lib.tracks, "folder")] == ["b", "a"]


def test_parse_synced_lrc_and_lookup():
    ly = parse_lrc("[ar:x]\n[00:01.00]one\n[00:03.50][00:10.00]two\n")
    assert ly.synced
    assert [t for t, _ in ly.lines] == [1.0, 3.5, 10.0]
    assert ly.index_at(0.5) == -1
    assert ly.index_at(4.0) == 1
    assert ly.index_at(99) == 2


def test_parse_plain_lyrics():
    ly = parse_lrc("line one\nline two\n")
    assert not ly.synced and len(ly.lines) == 2


def test_clean_query_for_online_titles():
    t = Track(source="u", title="Ravyn Lenae - Love Me Not (Official Music Video)",
              artist="Ravyn Lenae", kind="online")
    assert clean_query(t) == ("Ravyn Lenae", "Love Me Not")


def test_playlist_roundtrip():
    tracks = [Track(source="/m/a.mp3", title="A", artist="X", duration=61),
              Track(source="https://www.youtube.com/watch?v=abc", title="B", kind="online")]
    name = store.save_playlist('Road: Trip?', tracks)
    assert name in store.list_playlists()
    back = store.load_playlist(name)
    assert [(t.source, t.title, t.kind) for t in back] == [
        ("/m/a.mp3", "A", "local"), ("https://www.youtube.com/watch?v=abc", "B", "online")]
    store.delete_playlist(name)
    assert name not in store.list_playlists()


def test_history_top_counts():
    h = store.History()
    h.clear()
    a, b = Track(source="/a", title="A"), Track(source="/b", title="B")
    for t in (a, b, a, a):
        h.add(t)
    assert [(c, t.title) for c, t in h.top(5)] == [(3, "A"), (1, "B")]
    assert h.recent(1)[0][1].title == "A"
