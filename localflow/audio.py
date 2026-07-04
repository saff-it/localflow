"""Microphone capture. 16 kHz mono float32 — exactly what Whisper expects.

The input stream stays open for the whole daemon lifetime; the hotkey only
toggles collection. Opening/closing a CoreAudio stream per utterance proved
flaky (stream silently stops delivering after a few cycles) and clipped the
first word. If the stream dies, start() reopens it transparently.
"""
import threading
from typing import List, Optional

import numpy as np


def split_on_silence(clip: np.ndarray, sample_rate: int,
                     max_seconds: float = 28.0, search_seconds: float = 8.0) -> List[np.ndarray]:
    """Split a long clip at the quietest 200 ms found in the tail of each
    ~28 s window, so Whisper's 30 s window seam always lands in a pause
    instead of mid-word (the cause of garbled text on long dictations)."""
    max_n = int(max_seconds * sample_rate)
    if len(clip) <= max_n:
        return [clip]
    frame = int(0.2 * sample_rate)
    hop = frame // 2
    parts: List[np.ndarray] = []
    start = 0
    while len(clip) - start > max_n:
        lo = max(start, start + max_n - int(search_seconds * sample_rate))
        hi = start + max_n
        segment = clip[lo:hi]
        best_i, best_rms = 0, float("inf")
        for i in range(0, len(segment) - frame, hop):
            rms = float(np.sqrt(np.mean(segment[i:i + frame] ** 2)))
            if rms < best_rms:
                best_rms, best_i = rms, i
        cut = lo + best_i + frame // 2
        parts.append(clip[start:cut])
        start = cut
    parts.append(clip[start:])
    return parts


class Recorder:
    """The stream object stays open, but audio IO is suspended after
    `idle_stop_seconds` without dictation: macOS's orange mic indicator goes
    away when idle, without the open/close-per-utterance churn that wedged
    CoreAudio. A watchdog thread handles the release; start() resumes IO."""

    def __init__(self, sample_rate: int = 16000, device: Optional[str] = None,
                 idle_stop_seconds: float = 15.0):
        self.sample_rate = sample_rate
        self.device = device
        self.idle_stop_seconds = idle_stop_seconds
        self._frames: List[np.ndarray] = []
        self._collecting = False
        self._stream = None
        self._lock = threading.Lock()
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

            while True:
                time.sleep(2)
                with self._lock:
                    if (
                        self._stream is not None
                        and not getattr(self._stream, "closed", False)
                        and self._stream.active
                        and not self._collecting
                        and time.monotonic() - self._last_activity > self.idle_stop_seconds
                    ):
                        try:
                            self._stream.stop()  # suspend IO: orange dot off, device stays open
                        except Exception:
                            pass

        threading.Thread(target=watch, daemon=True).start()

    def ensure_open(self) -> None:
        import time

        with self._lock:
            self._last_activity = time.monotonic()
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

    def _on_block(self, indata, _frames, _time, _status) -> None:
        if self._collecting:
            self._frames.append(indata.copy())

    def start(self) -> None:
        import time

        self.ensure_open()
        with self._lock:
            self._frames = []
            self._collecting = True
            self._last_activity = time.monotonic()

    def stop(self) -> np.ndarray:
        import time

        with self._lock:
            self._collecting = False
            self._last_activity = time.monotonic()
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            clip = np.concatenate(self._frames)[:, 0]
            self._frames = []
            return clip

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
