import asyncio
import json

from termuxpl.config import CONFIG_FILE
from termuxpl.widgets import ArtView


def test_art_view_modes():
    v = ArtView()
    assert v.mode == "album" and v.showing_disc          # no cover yet -> CD
    v.cover = b"fake-image"
    assert not v.showing_disc                            # album mode with a cover
    v.set_mode("disc")
    assert v.showing_disc                                # CD even though a cover exists
    v.set_mode("bogus")
    assert v.mode == "disc"                              # unknown modes are ignored


def test_toggle_key_click_and_persistence():
    from termuxpl.app import TermuxPL

    async def run():
        app = TermuxPL()
        app.cfg.art_mode = "album"
        app.cfg.music_dirs = []
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause(0.3)
            art = app.query_one("#art", ArtView)
            assert art.mode == "album"
            await pilot.press("c")
            assert art.mode == "disc" and app.cfg.art_mode == "disc"
            assert json.loads(CONFIG_FILE.read_text())["art_mode"] == "disc"
            await pilot.click("#art")
            await pilot.pause(0.1)
            assert art.mode == "album" and app.cfg.art_mode == "album"

    asyncio.run(run())
