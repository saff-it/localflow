"""Streaming transcription: chunks are transcribed WHILE the key is held.

The monitor thread in app.py feeds drained mic samples into the session; when
enough audio accumulates, the session cuts at the quietest recent pause and
queues the chunk for the (single) worker thread, which transcribes it passing
the tail of the text-so-far as context prompt. On key release only the last
chunk remains: perceived latency becomes ~1s regardless of dictation length.

The full audio is retained, so on ANY failure the caller can fall back to the
classic batch pipeline — streaming must never lose a dictation.
"""
import queue
import threading
import time
from typing import List, Optional, Tuple

import numpy as np

from . import audio

MIN_CHUNK_SECONDS = 3.0     # never commit crumbs: seams are where errors live


class StreamingSession:
    def __init__(self, transcriber, sample_rate: int, chunk_seconds: float = 7.0,
                 base_prompt: str = "", context_chars: int = 200):
        self.transcriber = transcriber
        self.sample_rate = sample_rate
        self.chunk_seconds = max(4.0, chunk_seconds)
        self.base_prompt = base_prompt
        self.context_chars = context_chars
        self._carry = np.zeros(0, dtype=np.float32)
        self._full: List[np.ndarray] = []
        self._texts: List[str] = []
        self._lang = ""
        self._queue: "queue.Queue[Optional[np.ndarray]]" = queue.Queue()
        self._done = threading.Event()
        self._error: Optional[Exception] = None
        threading.Thread(target=self._worker, daemon=True).start()

    # -- worker thread: serial ASR with rolling context -----------------------

    def _worker(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk is None:
                break
            if self._error is not None:
                continue  # drain the queue, the caller will fall back to batch
            try:
                prompt = self.base_prompt
                context = " ".join(self._texts)[-self.context_chars:].strip()
                if context:
                    prompt = (prompt + " " + context).strip()
                text, lang = self.transcriber.transcribe(chunk, self.sample_rate, prompt=prompt)
                self._texts.append(text.strip())
                if lang and not self._lang:
                    self._lang = lang
            except Exception as exc:  # noqa: BLE001 — recorded, handled by caller
                self._error = exc
        self._done.set()

    def _commit(self, chunk: np.ndarray) -> None:
        if len(chunk) == 0 or not audio.has_speech(chunk, self.sample_rate):
            return  # silence never reaches the model: no 'grazie mille' out of thin air
        self._queue.put(chunk)

    # -- fed by the monitor thread while the key is held -----------------------

    def feed(self, samples: np.ndarray) -> None:
        if len(samples):
            self._full.append(samples)
            self._carry = np.concatenate([self._carry, samples]) if len(self._carry) else samples
        target = int(self.chunk_seconds * self.sample_rate)
        while len(self._carry) >= target:
            cut = audio.find_quiet_cut(self._carry[:target], self.sample_rate,
                                       search_seconds=min(4.0, self.chunk_seconds / 2))
            if cut < int(MIN_CHUNK_SECONDS * self.sample_rate):
                cut = target
            self._commit(self._carry[:cut])
            self._carry = self._carry[cut:]

    # -- key released -----------------------------------------------------------

    def finish(self, tail: np.ndarray) -> Tuple[List[str], str, float]:
        """Commit carry+tail, wait for the worker, return (texts, lang, wait_secs).
        wait_secs is the user-perceived latency: everything else already ran
        while they were speaking."""
        started = time.time()
        if len(tail):
            self._full.append(tail)
            last = np.concatenate([self._carry, tail]) if len(self._carry) else tail
        else:
            last = self._carry
        self._carry = np.zeros(0, dtype=np.float32)
        self._commit(last)
        self._queue.put(None)
        self._done.wait(timeout=120)
        if self._error is not None:
            raise self._error
        return self._texts, self._lang, time.time() - started

    def abort(self) -> None:
        self._queue.put(None)

    @property
    def total_seconds(self) -> float:
        return sum(len(piece) for piece in self._full) / float(self.sample_rate)

    def full_audio(self) -> np.ndarray:
        return np.concatenate(self._full) if self._full else np.zeros(0, dtype=np.float32)
