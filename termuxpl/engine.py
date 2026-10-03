"""Audio engine.

ffmpeg decodes any source (local file or a network stream URL) to raw float32
PCM at a fixed sample rate.  A reader thread feeds the PCM into a bounded
queue, and the sounddevice (PortAudio) callback pulls from it.  Because every
sample passes through our own callback we get, for free:

* sample-accurate position tracking and pause,
* software volume,
* a tap for the spectrum visualizer (FFT of the most recent samples).

Loudness normalization and stereo/mono are ffmpeg audio filters, so toggling
them restarts the decoder at the current position (seamless enough in practice).
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

import numpy as np

CHANNELS = 2
BLOCK = 1024
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def find_ffmpeg() -> str | None:
    """Locate ffmpeg even when it isn't on PATH.

    Order: TERMUXPL_FFMPEG env var → PATH → the running Python's own folders
    (conda puts ffmpeg in <prefix>/Library/bin, which is only on PATH when the
    env is activated) → winget / scoop / choco / common manual installs →
    the binary bundled by the imageio-ffmpeg package.
    """
    env = os.environ.get("TERMUXPL_FFMPEG")
    if env and os.path.isfile(env):
        return env
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    candidates: list[str] = []
    for prefix in {sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)}:
        candidates += [os.path.join(prefix, "Library", "bin", name),
                       os.path.join(prefix, "Scripts", name),
                       os.path.join(prefix, "bin", name),
                       os.path.join(prefix, name)]
    if sys.platform == "win32":
        la = os.environ.get("LOCALAPPDATA", "")
        up = os.environ.get("USERPROFILE", "")
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        candidates += [
            os.path.join(la, "Microsoft", "WinGet", "Links", name),
            os.path.join(up, "scoop", "shims", name),
            os.path.join(pd, "chocolatey", "bin", name),
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            os.path.join(la, "ffmpeg", "bin", name),
            os.path.join(up, "anaconda3", "Library", "bin", name),
            os.path.join(up, "miniconda3", "Library", "bin", name),
        ]
        pkgs = os.path.join(la, "Microsoft", "WinGet", "Packages")
        try:
            for d in os.listdir(pkgs):
                if "ffmpeg" in d.lower():
                    for root, _dirs, files in os.walk(os.path.join(pkgs, d)):
                        if name in files:
                            candidates.append(os.path.join(root, name))
        except OSError:
            pass
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    try:  # pip install imageio-ffmpeg ships a static ffmpeg build
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


FFMPEG = find_ffmpeg()


def _ffmpeg_input_args(src: str, headers: dict | None) -> list[str]:
    args: list[str] = []
    if src.startswith(("http://", "https://")):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            args += ["-headers", hdr]
    return args


class _NullOutput:
    """Fallback 'sound card' that just consumes samples in real time.

    Used when PortAudio is unavailable (headless servers, CI) so the rest of the
    player keeps working and stays testable.
    """

    def __init__(self, samplerate: int, callback: Callable):
        self.samplerate = samplerate
        self.callback = callback
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()

    def _run(self):
        buf = np.zeros((BLOCK, CHANNELS), dtype=np.float32)
        period = BLOCK / self.samplerate
        nxt = time.perf_counter()
        while not self._stop.is_set():
            try:
                self.callback(buf, BLOCK, None, None)
            except Exception:
                if self._stop.is_set():
                    break
            nxt += period
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()

    def stop(self):
        self._stop.set()

    def close(self):
        self.stop()


class AudioEngine:
    def __init__(self, sample_rate: int = 48000, volume: int = 70,
                 normalize: bool = True, loudness: float = -14.0, mono: bool = False):
        self.sr = sample_rate
        self.volume = volume
        self.normalize = normalize
        self.loudness = loudness
        self.mono = mono

        self._src: str | None = None
        self._headers: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._gen = 0
        self._q: queue.Queue = queue.Queue(maxsize=48)
        self._pending: np.ndarray | None = None
        self._eof = False
        self._lock = threading.Lock()

        self._offset = 0.0     # seconds where the current decoder started
        self._frames = 0       # frames played since decoder start
        self.paused = True
        self.loaded = False
        self.finished = False  # set when a track plays to its end
        self.error: str | None = None
        self.duration = 0.0

        self._ring = np.zeros(4096, dtype=np.float32)
        self._ring_pos = 0
        self._bands_smooth: np.ndarray | None = None

        self.backend = "portaudio"
        try:
            import sounddevice as sd  # noqa: WPS433
            self._stream = sd.OutputStream(samplerate=self.sr, channels=CHANNELS,
                                           dtype="float32", blocksize=BLOCK,
                                           callback=self._callback)
        except Exception as exc:  # PortAudio missing / no device
            self.backend = f"silent ({exc.__class__.__name__})"
            self._stream = _NullOutput(self.sr, self._callback)
        self._stream.start()

    # ------------------------------------------------------------------ decoder
    def _filters(self) -> str:
        f = []
        if self.normalize:
            f.append(f"loudnorm=I={self.loudness}:TP=-1.5:LRA=11")
        if self.mono:
            f.append("pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")
        return ",".join(f)

    def _start_decoder(self, start: float) -> None:
        if not FFMPEG:
            self.error = "ffmpeg not found on PATH"
            return
        self._gen += 1
        gen = self._gen
        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
        args += _ffmpeg_input_args(self._src, self._headers)
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", self._src, "-vn"]
        af = self._filters()
        if af:
            args += ["-af", af]
        args += ["-ac", str(CHANNELS), "-ar", str(self.sr), "-f", "f32le", "-"]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError as exc:
            self.error = str(exc)
            return
        self._proc = proc
        self._offset = start
        self._frames = 0
        self._eof = False
        threading.Thread(target=self._reader, args=(proc, gen), daemon=True).start()

    def _reader(self, proc: subprocess.Popen, gen: int) -> None:
        nbytes = BLOCK * 4 * CHANNELS * 4
        stdout = proc.stdout
        leftover = b""
        while gen == self._gen:
            data = stdout.read(nbytes)
            if not data:
                break
            data = leftover + data
            usable = len(data) - len(data) % (4 * CHANNELS)
            leftover = data[usable:]
            chunk = np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, CHANNELS)
            while gen == self._gen:
                try:
                    self._q.put(chunk, timeout=0.2)
                    break
                except queue.Full:
                    continue
        if gen == self._gen:
            err = b""
            try:
                err = proc.stderr.read() or b""
            except Exception:
                pass
            rc = proc.wait()
            if rc not in (0, None) and self._frames == 0 and self._q.empty():
                self.error = err.decode(errors="replace").strip()[-300:] or f"ffmpeg exited {rc}"
            self._eof = True

    def _kill(self) -> None:
        self._gen += 1
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._pending = None

    # ---------------------------------------------------------------- callback
    def _callback(self, outdata, frames, _time, _status) -> None:
        # Never let an exception escape: PortAudio stops the stream for good if
        # the callback raises, which would silence the player until restart.
        try:
            self._fill(outdata, frames)
        except Exception:
            outdata.fill(0)

    def _fill(self, outdata, frames) -> None:
        if self.paused or not self.loaded:
            outdata.fill(0)
            return
        with self._lock:
            filled = 0
            while filled < frames:
                if self._pending is None or len(self._pending) == 0:
                    try:
                        self._pending = self._q.get_nowait()
                    except queue.Empty:
                        break
                take = min(frames - filled, len(self._pending))
                outdata[filled:filled + take] = self._pending[:take]
                self._pending = self._pending[take:]
                filled += take
            if filled < frames:
                outdata[filled:].fill(0)
                if self._eof and self._q.empty() and (self._pending is None or len(self._pending) == 0):
                    if not self.finished:
                        self.finished = True
                        self.paused = True
            self._frames += filled

        if filled:
            mono = outdata[:filled].mean(axis=1)
            n = len(mono)
            ring = self._ring
            pos = self._ring_pos
            if n >= len(ring):
                ring[:] = mono[-len(ring):]
                pos = 0
            else:
                end = pos + n
                if end <= len(ring):
                    ring[pos:end] = mono
                else:
                    cut = len(ring) - pos
                    ring[pos:] = mono[:cut]
                    ring[:n - cut] = mono[cut:]
                pos = end % len(ring)
            self._ring_pos = pos
            g = (self.volume / 100.0) ** 1.6
            outdata *= g

    # -------------------------------------------------------------- public API
    def load(self, src: str, duration: float = 0.0, headers: dict | None = None,
             start: float = 0.0, autoplay: bool = True) -> None:
        with self._lock:
            self._kill()
            self._src = src
            self._headers = headers
            self.duration = duration
            self.error = None
            self.finished = False
            self.loaded = True
            self._start_decoder(start)
            self.paused = not autoplay

    def stop(self) -> None:
        with self._lock:
            self._kill()
            self.loaded = False
            self.paused = True
            self._frames = 0
            self._offset = 0.0

    def toggle(self) -> None:
        if self.loaded:
            self.paused = not self.paused

    def play(self) -> None:
        if self.loaded:
            self.paused = False

    def pause(self) -> None:
        self.paused = True

    @property
    def position(self) -> float:
        return self._offset + self._frames / self.sr

    def seek(self, seconds: float) -> None:
        if not self.loaded or not self._src:
            return
        if self.duration:
            seconds = min(seconds, max(0.0, self.duration - 0.5))
        seconds = max(0.0, seconds)
        with self._lock:
            self._kill()
            self.finished = False
            self._start_decoder(seconds)

    def seek_relative(self, delta: float) -> None:
        self.seek(self.position + delta)

    def set_volume(self, v: int) -> None:
        self.volume = int(max(0, min(100, v)))

    def _restart_here(self) -> None:
        if self.loaded:
            was_paused = self.paused
            self.seek(self.position)
            self.paused = was_paused

    def set_normalize(self, on: bool) -> None:
        if on != self.normalize:
            self.normalize = on
            self._restart_here()

    def set_mono(self, on: bool) -> None:
        if on != self.mono:
            self.mono = on
            self._restart_here()

    def spectrum(self, bands: int) -> np.ndarray:
        """Return `bands` log-spaced magnitudes in 0..1 for the visualizer."""
        n = 2048
        pos = self._ring_pos
        ring = self._ring
        data = np.concatenate((ring[pos:], ring[:pos]))[-n:]
        if self.paused or not self.loaded:
            target = np.zeros(bands)
        else:
            win = data * np.hanning(n)
            mag = np.abs(np.fft.rfft(win)) / (n / 4)
            freqs = np.fft.rfftfreq(n, 1 / self.sr)
            edges = np.geomspace(40, 16000, bands + 1)
            idx = np.searchsorted(freqs, edges)
            target = np.empty(bands)
            for i in range(bands):
                a, b = idx[i], max(idx[i + 1], idx[i] + 1)
                target[i] = mag[a:b].max() if b <= len(mag) else 0.0
            db = 20 * np.log10(target + 1e-9)
            target = np.clip((db + 60) / 60, 0, 1)
        prev = self._bands_smooth
        if prev is None or len(prev) != bands:
            prev = np.zeros(bands)
        # fast attack, slow decay
        out = np.where(target > prev, target, prev * 0.82 + target * 0.18)
        self._bands_smooth = out
        return out

    def close(self) -> None:
        self._kill()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


def compute_waveform(src: str, headers: dict | None = None, buckets: int = 400,
                     cancel: threading.Event | None = None) -> np.ndarray | None:
    """Decode a low-rate mono copy and return per-bucket peak levels (0..1)."""
    if not FFMPEG:
        return None
    rate = 2000
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    args += _ffmpeg_input_args(src, headers)
    args += ["-i", src, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except OSError:
        return None
    win = rate // 10  # 100 ms windows
    levels: list[float] = []
    rest = np.zeros(0, dtype=np.float32)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.kill()
                return None
            data = proc.stdout.read(win * 2 * 200)
            if not data:
                break
            data = data[: len(data) - len(data) % 2]
            x = np.concatenate((rest, np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768))
            n = len(x) // win
            if n:
                blocks = x[: n * win].reshape(n, win)
                levels.extend(np.sqrt((blocks ** 2).mean(axis=1)).tolist())
            rest = x[n * win:]
    finally:
        proc.wait()
    if not levels:
        return None
    lv = np.asarray(levels)
    if len(lv) < buckets:
        lv = np.interp(np.linspace(0, len(lv) - 1, buckets), np.arange(len(lv)), lv)
    else:
        lv = np.array([c.max() for c in np.array_split(lv, buckets)])
    peak = lv.max() or 1.0
    return np.clip(lv / peak, 0, 1)
