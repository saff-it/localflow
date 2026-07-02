"""Microphone capture. 16 kHz mono float32 — exactly what Whisper expects."""
import threading
from typing import List, Optional

import numpy as np


class Recorder:
    def __init__(self, sample_rate: int = 16000, device: Optional[str] = None):
        self.sample_rate = sample_rate
        self.device = device
        self._frames: List[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> None:
        import sounddevice as sd  # lazy: file-transcribe mode never touches the mic

        with self._lock:
            if self._stream is not None:
                return
            self._frames = []
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=self._on_block,
            )
            self._stream.start()

    def _on_block(self, indata, _frames, _time, _status) -> None:
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream is None:
                return np.zeros(0, dtype=np.float32)
            self._stream.stop()
            self._stream.close()
            self._stream = None
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            clip = np.concatenate(self._frames)[:, 0]
            self._frames = []
            return clip
