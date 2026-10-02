"""AcoustID fingerprint lookup (needs fpcalc from Chromaprint + a free API key)."""
from __future__ import annotations

import os
import shutil

try:
    import acoustid
except ImportError:  # pragma: no cover
    acoustid = None


def fpcalc_path() -> str | None:
    exe = shutil.which("fpcalc")
    if exe:
        return exe
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("fpcalc.exe", "fpcalc"):
        p = os.path.join(here, name)
        if os.path.isfile(p):
            return p
    return None


def lookup(path: str, api_key: str, limit: int = 8) -> list[dict]:
    """Return candidate tag sets: [{title, artist, album, year, score}]"""
    if acoustid is None:
        raise RuntimeError("pyacoustid is not installed (pip install pyacoustid)")
    if not api_key:
        raise RuntimeError("Set your AcoustID API key in Settings (free at acoustid.org/new-application)")
    fp = fpcalc_path()
    if fp:
        os.environ.setdefault("FPCALC", fp)
    try:
        duration, fingerprint = acoustid.fingerprint_file(path)
    except acoustid.NoBackendError:
        raise RuntimeError("fpcalc not found - install Chromaprint and put fpcalc on PATH "
                           "(or in the Termux PL folder)") from None
    except acoustid.FingerprintGenerationError as exc:
        raise RuntimeError(f"Could not fingerprint file: {exc}") from None
    resp = acoustid.lookup(api_key, fingerprint, duration,
                           meta="recordings releasegroups releases")
    if resp.get("status") != "ok":
        err = resp.get("error", {}).get("message", "lookup failed")
        raise RuntimeError(f"AcoustID: {err}")
    out: list[dict] = []
    seen = set()
    for res in resp.get("results", []):
        score = float(res.get("score", 0))
        for rec in res.get("recordings", []) or []:
            title = rec.get("title") or ""
            artist = ", ".join(a.get("name", "") for a in rec.get("artists", []) or [])
            groups = rec.get("releasegroups") or [{}]
            for g in groups[:3]:
                album = g.get("title", "")
                year = ""
                for rel in g.get("releases", []) or []:
                    d = rel.get("date") or {}
                    if d.get("year"):
                        y = str(d["year"])
                        year = y if not year or y < year else year
                key = (title.lower(), artist.lower(), album.lower())
                if title and key not in seen:
                    seen.add(key)
                    out.append({"title": title, "artist": artist, "album": album,
                                "year": year, "score": score})
    out.sort(key=lambda c: -c["score"])
    return out[:limit]
