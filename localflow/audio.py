"""Microphone capture. 16 kHz mono float32 — exactly what Whisper expects.

The input stream stays open for the whole daemon lifetime; the hotkey only
toggles collection. Opening/closing a CoreAudio stream per utterance proved
flaky (stream silently stops delivering after a few cycles) and clipped the
first word. If the stream dies, start() reopens it transparently.
"""
import threading
from typing import List, Optional

import numpy as np


class Recorder:
    def __init__(self, sample_rate: int = 16000, device: Optional[str] = None):
        self.sample_rate = sample_rate
        self.device = device
        self._frames: List[np.ndarray] = []
        self._collecting = False
        self._stream = None
        self._lock = threading.Lock()

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

    def ensure_open(self) -> None:
        with self._lock:
            if self._stream is None or not self._stream.active:
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                self._open_stream()

    def _on_block(self, indata, _frames, _time, _status) -> None:
        if self._collecting:
            self._frames.append(indata.copy())

    def start(self) -> None:
        self.ensure_open()
        with self._lock:
            self._frames = []
            self._collecting = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self._collecting = False
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
