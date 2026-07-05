"""Local voice assistant: hold a key, ask, get a spoken answer — all on-device.

Turbo ASR transcribes the question, a local qwen answers with rolling
conversation memory, the reply is read aloud via macOS `say` and copied to the
clipboard. No cloud, no accounts. It is NOT Claude: a local 7B has no internet,
no tools, and shallow knowledge — good for quick questions, drafts and
explanations, wrong for fresh facts.
"""
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
MAX_TURNS = 12  # keep the last N exchanges in context (bounds latency + RAM)


class Assistant:
    def __init__(self, url: str, model: str, voice: str = "Alice", rate: int = 0):
        self.url = url
        self.model = model
        self.voice = voice
        self.rate = rate
        self.history: List[dict] = []
        self._say_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        self.stop_speaking()
        self.history = []

    def stop_speaking(self) -> None:
        """Barge-in: silence any in-progress spoken reply."""
        with self._lock:
            if self._say_proc is not None and self._say_proc.poll() is None:
                try:
                    self._say_proc.terminate()
                except Exception:
                    pass
            self._say_proc = None

    def ask(self, question: str, timeout: float = 120.0) -> str:
        self.history.append({"role": "user", "content": question})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-2 * MAX_TURNS:]
        try:
            resp = requests.post(
                self.url + "/api/chat",
                json={"model": self.model, "messages": messages, "stream": False,
                      "keep_alive": "30m", "options": {"temperature": 0.4}},
                timeout=timeout,
            )
            resp.raise_for_status()
            answer = resp.json().get("message", {}).get("content", "").strip()
        except (requests.RequestException, ValueError, KeyError) as exc:
            self.history.pop()  # don't poison history with a failed turn
            return "Scusa, non sono riuscito a rispondere (%s)." % type(exc).__name__
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def speak(self, text: str) -> None:
        self.stop_speaking()
        if not text:
            return
        cmd = ["say"]
        if self.voice:
            cmd += ["-v", self.voice]
        if self.rate:
            cmd += ["-r", str(self.rate)]
        cmd.append(text)
        try:
            with self._lock:
                self._say_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self._say_proc = None
