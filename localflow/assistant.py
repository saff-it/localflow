"""Local voice assistant: hold a key, ask, get a spoken answer — all on-device.

Turbo ASR transcribes the question, a local qwen answers with rolling
conversation memory, and the reply is spoken SENTENCE BY SENTENCE while it is
still being generated (streamed): the first sentence starts after ~1-2s
instead of waiting for the whole answer. Barge-in (re-press) aborts both the
speech queue and the generation stream. It is NOT Claude: a local 7B has no
internet, no tools, shallow knowledge — great for quick questions and drafts.
"""
import json
import queue
import re
import subprocess
import threading
from typing import List, Optional

import requests

SYSTEM_PROMPT = (
    "Sei l'assistente vocale locale di LocalFlow. Rispondi in modo breve e "
    "colloquiale, come in una conversazione parlata: la tua risposta viene LETTA "
    "ad alta voce. Niente elenchi lunghi, niente markdown, niente asterischi o "
    "simboli; frasi brevi e naturali. Rispondi nella lingua dell'utente (italiano "
    "salvo diversa indicazione). Se non sai qualcosa con certezza, dillo con "
    "onestà invece di inventare."
)
MAX_TURNS = 12          # rolling context bound (latency + RAM)
_SENTENCE_END = re.compile(r"[.!?…]['\")]?\s")
_CLAUSE_END = re.compile(r"[,;:]\s")
MIN_CLAUSE_CHARS = 60   # flush at a comma only once the clause is meaty enough


class Assistant:
    def __init__(self, url: str, model: str, voice: str = "Alice", rate: int = 0):
        self.url = url
        self.model = model
        self.voice = voice
        self.rate = rate
        self.history: List[dict] = []
        self._say_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._speak_gen = 0            # bumped on every barge-in / new question
        self._say_queue: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._say_worker, daemon=True).start()

    # -- speech queue: sentences play strictly in order ------------------------

    def _say_worker(self) -> None:
        while True:
            gen, text = self._say_queue.get()
            if gen != self._speak_gen:
                continue  # stale sentence from an aborted answer
            cmd = ["say"]
            if self.voice:
                cmd += ["-v", self.voice]
            if self.rate:
                cmd += ["-r", str(self.rate)]
            cmd.append(text)
            try:
                with self._lock:
                    if gen != self._speak_gen:
                        continue
                    self._say_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                                      stderr=subprocess.DEVNULL)
                    proc = self._say_proc
                proc.wait()
            except Exception:
                pass

    def stop_speaking(self) -> None:
        """Barge-in: silence the current sentence and drop everything queued."""
        with self._lock:
            self._speak_gen += 1
            if self._say_proc is not None and self._say_proc.poll() is None:
                try:
                    self._say_proc.terminate()
                except Exception:
                    pass
            self._say_proc = None

    def reset(self) -> None:
        self.stop_speaking()
        self.history = []

    # -- streamed question/answer ----------------------------------------------

    def ask_and_speak(self, question: str, timeout: float = 180.0) -> str:
        """Stream the model's answer, speaking each completed sentence right
        away. Returns the full answer text (for clipboard/log) once done."""
        self.stop_speaking()
        gen = self._speak_gen
        self.history.append({"role": "user", "content": question})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-2 * MAX_TURNS:]
        buf, full = "", []
        try:
            with requests.post(
                self.url + "/api/chat",
                json={"model": self.model, "messages": messages, "stream": True,
                      "keep_alive": "5m",
                      "options": {"temperature": 0.4, "num_predict": 300}},
                timeout=timeout, stream=True,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if gen != self._speak_gen:
                        return "".join(full).strip()  # barged-in: stop generating too
                    if not line:
                        continue
                    piece = json.loads(line).get("message", {}).get("content", "")
                    if not piece:
                        continue
                    buf += piece
                    full.append(piece)
                    while True:  # flush speakable pieces to the voice ASAP:
                        # full sentences always; long clauses at a comma too,
                        # so a single long sentence still starts speaking early.
                        match = _SENTENCE_END.search(buf)
                        if match is None and len(buf) >= MIN_CLAUSE_CHARS:
                            match = _CLAUSE_END.search(buf, MIN_CLAUSE_CHARS - 40)
                        if match is None:
                            break
                        piece_txt, buf = buf[:match.end()].strip(), buf[match.end():]
                        if piece_txt:
                            self._say_queue.put((gen, piece_txt))
        except (requests.RequestException, ValueError, KeyError) as exc:
            self.history.pop()  # don't poison history with a failed turn
            return "Scusa, non sono riuscito a rispondere (%s)." % type(exc).__name__
        if buf.strip() and gen == self._speak_gen:
            self._say_queue.put((gen, buf.strip()))
        answer = "".join(full).strip()
        self.history.append({"role": "assistant", "content": answer})
        return answer
