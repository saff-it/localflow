"""Persistent assistant memory: a plain-text .md log that survives restarts.

Every exchange is appended (human-readable, weightless). On startup the recent
tail is reloaded as conversation context, so the assistant "remembers what we
said" across sessions. A full running summary is a future enhancement; for now
the recent window bounds token cost.
"""
import datetime
import pathlib
import re
from typing import List

MEMORY_PATH = pathlib.Path.home() / ".localflow" / "assistant-memory.md"
_ENTRY = re.compile(r"^## .*? — (tu|assistente)\s*$")


class AssistantMemory:
    def __init__(self, path: pathlib.Path = MEMORY_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("# LocalFlow — memoria assistente\n\n", encoding="utf-8")

    def append(self, role: str, content: str) -> None:
        who = "tu" if role == "user" else "assistente"
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        block = "## %s — %s\n%s\n\n" % (stamp, who, content.strip())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(block)

    def recent_turns(self, max_turns: int = 12) -> List[dict]:
        """Reload the last N exchanges as [{role, content}] for LLM context."""
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        turns: List[dict] = []
        role, buf = None, []

        def flush():
            if role and buf:
                turns.append({"role": role, "content": "\n".join(buf).strip()})

        for line in lines:
            m = _ENTRY.match(line)
            if m:
                flush()
                role = "user" if m.group(1) == "tu" else "assistant"
                buf = []
            elif role is not None:
                buf.append(line)
        flush()
        return turns[-2 * max_turns:]
