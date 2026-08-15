"""Microphone capture. 16 kHz mono float32 — exactly what Whisper expects.

The input stream stays open for the whole daemon lifetime; the hotkey only
toggles collection. Opening/closing a CoreAudio stream per utterance proved
flaky (stream silently stops delivering after a few cycles) and clipped the
first word. If the stream dies, start() reopens it transparently.
"""
import threading
from typing import List, Optional

import numpy as np


# Calibrated three times on the user's real mic: daytime voice peaks >= 0.029,
# LATE-NIGHT WHISPERED dictation sits at 0.016-0.022 with sustained windows
# at 0.010+ (7-14 hot), but the quietest real dictations measured (15 ago,
# clip-1786822109/132) sustain only 0.003-0.006 for several seconds straight —
# below the old 0.006 floor, so has_speech() rejected them outright even
# though the words were there, silently, with no louder passage in the whole
# clip for the classic-path redo to fall back on. The key-click ghost lights
# only 1-3 windows at any threshold, so the sustained-count rule is still the
# click killer; the RMS floor just needs clear room above the ~0.0009 room
# noise floor and below 0.003. Overridable via config min_speech_rms.
MIN_SPEECH_RMS = 0.0035


def has_speech(clip: np.ndarray, sample_rate: int, threshold: float = 0.0) -> bool:
    """True only with SUSTAINED speech-level energy: >=5 hot 100ms windows at
    50ms hop (~250ms cumulative). A single loud transient — the mechanical
    key click that fooled the first single-frame gate into 'Grazie a tutti' —
    lights up 1-3 windows; real voice starts at 7+ (measured on the user's mic)."""
    threshold = threshold or MIN_SPEECH_RMS
    frame = int(0.1 * sample_rate)
    hop = frame // 2
    hot = 0
    for i in range(0, max(1, len(clip) - frame), hop):
        if float(np.sqrt(np.mean(clip[i:i + frame] ** 2))) >= threshold:
            hot += 1
            if hot >= 5:
                return True
    return False


def find_quiet_cut(clip: np.ndarray, sample_rate: int, search_seconds: float) -> int:
    """Index of the quietest 200 ms centre within the LAST `search_seconds`
    of `clip` — the least damaging place to cut speech."""
    frame = int(0.2 * sample_rate)
    hop = frame // 2
    lo = max(0, len(clip) - int(search_seconds * sample_rate))
    segment = clip[lo:]
    best_i, best_rms = 0, float("inf")
    for i in range(0, max(1, len(segment) - frame), hop):
        rms = float(np.sqrt(np.mean(segment[i:i + frame] ** 2)))
        if rms < best_rms:
            best_rms, best_i = rms, i
    return lo + best_i + frame // 2


def split_on_silence(clip: np.ndarray, sample_rate: int,
                     max_seconds: float = 28.0, search_seconds: float = 8.0) -> List[np.ndarray]:
    """Split a long clip at the quietest 200 ms found in the tail of each
    ~28 s window, so Whisper's 30 s window seam always lands in a pause
    instead of mid-word (the cause of garbled text on long dictations)."""
    max_n = int(max_seconds * sample_rate)
    if len(clip) <= max_n:
        return [clip]
    parts: List[np.ndarray] = []
    start = 0
    while len(clip) - start > max_n:
        cut = start + find_quiet_cut(clip[start:start + max_n], sample_rate, search_seconds)
        parts.append(clip[start:cut])
        start = cut
    parts.append(clip[start:])
    return parts


class Recorder:
    """The stream stays open while dictating; after `idle_stop_seconds` of
    quiet a watchdog suspends audio IO so macOS's orange mic indicator goes
    away. Hardened after a freeze incident: CoreAudio calls (open/start/stop/
    close) run under their own lock, NEVER under the fast frames lock, so a
    wedged CoreAudio call can no longer block the hotkey listener or the UI.
    Any failure path falls back to a full close-and-reopen."""

    def __init__(self, sample_rate: int = 16000, device: Optional[str] = None,
                 idle_stop_seconds: float = 300.0):
        self.sample_rate = sample_rate
        self.device = device
        self.idle_stop_seconds = idle_stop_seconds
        self._frames: List[np.ndarray] = []
        self._collecting = False
        self._stream = None
        self._frames_lock = threading.Lock()   # fast: only flags and buffers
        self._audio_lock = threading.Lock()    # slow: CoreAudio open/start/stop/close
        self._last_activity = 0.0
        self._watchdog_started = False

    def _open_stream(self) -> None:
        import sounddevice as sd  # lazy: file-transcribe mode never touches the mic

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._on_block,
        )
        self._stream.start()

    def _start_watchdog(self) -> None:
        if self._watchdog_started or self.idle_stop_seconds <= 0:
            return
        self._watchdog_started = True

        def watch():
            import time

            interval = 1 if self.idle_stop_seconds <= 10 else 5
            # Full close frees coreaudiod's battery-draining sleep assertion, but
            # reopening costs ~0.5s at the next press: do it only after a real break.
            close_after = max(600.0, self.idle_stop_seconds)
            while True:
                time.sleep(interval)
                if self._collecting:
                    continue
                idle = time.monotonic() - self._last_activity
                if idle <= self.idle_stop_seconds:
                    continue
                with self._audio_lock:
                    stream = self._stream
                    if stream is None or getattr(stream, "closed", False):
                        continue
                    try:
                        if stream.active:
                            stream.stop()  # stage 1: orange dot off, instant resume
                        elif idle > close_after:
                            # stage 2: release the device — a stopped-but-open stream
                            # still holds coreaudiod's PreventUserIdleSystemSleep
                            # assertion and quietly drains the battery.
                            stream.close()
                            self._stream = None
                    except Exception:
                        try:
                            stream.close()
                        except Exception:
                            pass
                        self._stream = None  # reopened on next start()

        threading.Thread(target=watch, daemon=True).start()

    def ensure_open(self) -> None:
        import time

        self._last_activity = time.monotonic()
        with self._audio_lock:
            if self._stream is None or getattr(self._stream, "closed", False):
                self._open_stream()
            elif self._stream.stopped:
                try:
                    self._stream.start()  # resume suspended IO
                except Exception:
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._open_stream()
        self._start_watchdog()

    def reopen(self) -> None:
        """Full reset — used when a dictation came back empty (wedged stream)."""
        with self._audio_lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            self._open_stream()

    def _on_block(self, indata, _frames, _time, _status) -> None:
        if self._collecting:
            self._frames.append(indata.copy())

    def start(self) -> None:
        import time

        self.ensure_open()
        with self._frames_lock:
            self._frames = []
            self._collecting = True
        self._last_activity = time.monotonic()

    def drain(self) -> np.ndarray:
        """Take what's buffered so far WITHOUT stopping collection — the tap
        the streaming pipeline drinks from while the key is still held."""
        with self._frames_lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames)[:, 0]

    def stop(self) -> np.ndarray:
        import time

        with self._frames_lock:
            self._collecting = False
            frames = self._frames
            self._frames = []
        self._last_activity = time.monotonic()
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames)[:, 0]

    def close(self) -> None:
        with self._audio_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
