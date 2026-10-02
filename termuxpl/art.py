"""Braille-dot rendering: cover art, idle art, waveform and spectrum.

Each terminal cell is a Unicode braille character holding a 2x4 dot grid, so a
cols x rows area gives (2*cols) x (4*rows) "pixels" - roughly square dots.
"""
from __future__ import annotations

import io
import math

import numpy as np
from rich.style import Style
from rich.text import Text

try:
    from PIL import Image, ImageDraw, ImageOps
except ImportError:  # pragma: no cover
    Image = None

# dot bit for (x, y) inside a 2x4 cell
_BITS = np.array([[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]], dtype=np.uint16)

GRADIENT = ["#e879f9", "#c084fc", "#a78bfa", "#818cf8", "#6b8bd6", "#4f8fb8", "#2f9a9e", "#14a39a"]


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def gradient_color(t: float, stops: list[str] = GRADIENT) -> str:
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = int(t)
    if i >= len(stops) - 1:
        return stops[-1]
    f = t - i
    a, b = _hex(stops[i]), _hex(stops[i + 1])
    r, g, bl = (round(a[k] + (b[k] - a[k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def mask_to_cells(mask: np.ndarray) -> np.ndarray:
    """bool array (4*rows, 2*cols) -> array of braille chars (rows, cols)."""
    h, w = mask.shape
    rows, cols = h // 4, w // 2
    m = mask[: rows * 4, : cols * 2].reshape(rows, 4, cols, 2).transpose(0, 2, 1, 3)
    codes = (m * _BITS).sum(axis=(2, 3)) + 0x2800
    return np.vectorize(chr)(codes) if codes.size else np.empty((rows, cols), dtype="<U1")


def mask_to_text(mask: np.ndarray, stops: list[str] = GRADIENT) -> Text:
    cells = mask_to_cells(mask)
    rows = cells.shape[0]
    text = Text(no_wrap=True, overflow="crop")
    for r in range(rows):
        style = Style(color=gradient_color(r / max(rows - 1, 1), stops))
        text.append("".join(cells[r]), style)
        if r < rows - 1:
            text.append("\n")
    return text


# ------------------------------------------------------------ pictures
def image_mask(data: bytes, cols: int, rows: int) -> np.ndarray | None:
    if Image is None or cols < 2 or rows < 2:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
    except Exception:
        return None
    w, h = cols * 2, rows * 4
    img = ImageOps.fit(img, (min(w, h), min(w, h)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=2)
    canvas = Image.new("L", (w, h), 0)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    bw = canvas.convert("1")  # Floyd-Steinberg dithering
    arr = np.array(bw, dtype=bool)
    return arr


def disc_mask(cols: int, rows: int, angle: float = 0.0) -> np.ndarray:
    """A stylised CD: filled disc with a hub ring and a rotating light-cut."""
    w, h = cols * 2, rows * 4
    size = min(w, h)
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    dx, dy = xx - cx + 0.5, yy - cy + 0.5
    r = np.hypot(dx, dy) / (size / 2)
    th = (np.arctan2(dy, dx) - angle) % (2 * math.pi)
    disc = r < 0.97
    hole = r < 0.13
    hub_gap = (r > 0.30) & (r < 0.38)
    # two reflective cuts
    cut1 = (th > 4.55) & (th < 4.75) & (r > 0.38)
    cut2 = (th > 2.25) & (th < 2.38) & (r > 0.38)
    grooves = ((r * 40).astype(int) % 7 == 0) & (r > 0.45)
    checker = ((xx + yy) % 2 == 0) | (r < 0.30)
    return disc & ~hole & ~hub_gap & ~cut1 & ~cut2 & ~(grooves & ~checker)


def cassette_mask(cols: int, rows: int, phase: float = 0.0) -> np.ndarray:
    """Original idle illustration: a cassette tape with turning reels."""
    w, h = cols * 2, rows * 4
    if Image is None:
        return np.zeros((h, w), dtype=bool)
    img = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(img)
    bw, bh = int(w * 0.9), int(min(h * 0.75, w * 0.58))
    x0, y0 = (w - bw) // 2, (h - bh) // 2
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=max(2, bh // 10), fill=1)
    # label window
    lx0, ly0, lx1, ly1 = x0 + bw * 0.1, y0 + bh * 0.12, x0 + bw * 0.9, y0 + bh * 0.62
    d.rounded_rectangle([lx0, ly0, lx1, ly1], radius=max(1, bh // 14), fill=0)
    # reels
    rr = bh * 0.14
    for cxr in (x0 + bw * 0.32, x0 + bw * 0.68):
        cyr = y0 + bh * 0.37
        d.ellipse([cxr - rr, cyr - rr, cxr + rr, cyr + rr], fill=1)
        d.ellipse([cxr - rr * 0.45, cyr - rr * 0.45, cxr + rr * 0.45, cyr + rr * 0.45], fill=0)
        for k in range(3):
            a = phase + k * 2 * math.pi / 3
            d.line([cxr, cyr, cxr + math.cos(a) * rr * 0.9, cyr + math.sin(a) * rr * 0.9], fill=0, width=1)
    # tape window line
    d.line([lx0 + bw * 0.08, ly1 - bh * 0.06, lx1 - bw * 0.08, ly1 - bh * 0.06], fill=1)
    # bottom trapezoid
    d.polygon([(x0 + bw * 0.2, y0 + bh), (x0 + bw * 0.28, y0 + bh * 0.76),
               (x0 + bw * 0.72, y0 + bh * 0.76), (x0 + bw * 0.8, y0 + bh)], fill=0)
    for hx in (0.36, 0.64):
        d.ellipse([x0 + bw * hx - 2, y0 + bh * 0.86 - 2, x0 + bw * hx + 2, y0 + bh * 0.86 + 2], fill=1)
    arr = np.array(img, dtype=bool)
    # dotted fill texture so it reads like the dot-matrix style
    yy, xx = np.mgrid[0:h, 0:w]
    return arr & (((xx + yy) % 2 == 0) | ((xx % 2 == 0) & (yy % 2 == 0)) | (yy % 3 != 1))


# --------------------------------------------------------- waveform / spectrum
def waveform_text(levels: np.ndarray | None, cols: int, rows: int, progress: float,
                  played: str = "#f9a8d4", rest: str = "#7a4d6e", flat: str = "#5b3b55") -> Text:
    w, h = cols * 2, rows * 4
    if w <= 0 or h <= 0:
        return Text("")
    if levels is None or len(levels) == 0:
        mask = np.zeros((h, w), dtype=bool)
        mid = h // 2
        mask[mid, ::1] = True
    else:
        lv = np.interp(np.linspace(0, len(levels) - 1, w), np.arange(len(levels)), levels)
        half = np.maximum(1, np.round(lv * (h / 2))).astype(int)
        yy = np.arange(h)[:, None]
        mid = (h - 1) / 2
        mask = np.abs(yy - mid) <= half[None, :] - 0.5
        mask &= (np.arange(w)[None, :] % 2 == 0)  # dotted columns
    cells = mask_to_cells(mask)
    cut = int(round(min(max(progress, 0.0), 1.0) * cols))
    text = Text(no_wrap=True, overflow="crop")
    for r in range(cells.shape[0]):
        row = "".join(cells[r])
        text.append(row[:cut], Style(color=played, bold=True))
        text.append(row[cut:], Style(color=rest if levels is not None else flat))
        if r < cells.shape[0] - 1:
            text.append("\n")
    return text


def spectrum_text(bands: np.ndarray, cols: int, rows: int, stops: list[str] = GRADIENT) -> Text:
    w, h = cols * 2, rows * 4
    if w <= 0 or h <= 0:
        return Text("")
    vals = np.interp(np.linspace(0, len(bands) - 1, w // 2 or 1), np.arange(len(bands)), bands) \
        if len(bands) else np.zeros(w // 2 or 1)
    heights = np.round(vals * h).astype(int)
    mask = np.zeros((h, w), dtype=bool)
    for i, ht in enumerate(heights):
        if ht <= 0:
            continue
        mask[h - ht:, 2 * i] = True   # one dot column per bar, gap column between
    return mask_to_text(mask, stops)
