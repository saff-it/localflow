"""Free internet for the assistant: DuckDuckGo search (no key) + local synthesis.

Top results are fetched, then handed to the local LLM to answer the question and
cite sources. Zero per-use cost. Lower polish than a premium (Claude) search,
but private-ish (only the query leaves, not your data) and free.
"""
import json
import re
from typing import Callable, List, Optional

import requests

_SENTENCE_END = re.compile(r"[.!?…]['\")]?\s")
_BODY_MAX = 220  # trim each result body: less for the LLM to chew = faster

# Trigger words: the question wants fresh/online info the local model can't know.
SEARCH_TRIGGERS = (
    "cerca online", "cerca su internet", "cerca sul web", "cerca in rete",
    "ultime notizie", "notizie di oggi", "che tempo fa", "meteo",
    "chi ha vinto", "risultato", "quotazione", "prezzo di", "quanto costa",
    "novità su", "aggiornamenti su", "oggi", "ieri", "questa settimana",
    "nel 2025", "nel 2026", "attuale", "adesso online",
)


def wants_search(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in SEARCH_TRIGGERS)


def search(query: str, max_results: int = 3, region: str = "it-it") -> List[dict]:
    try:
        from ddgs import DDGS

        return list(DDGS().text(query, max_results=max_results, region=region))
    except Exception:
        return []


def results_context(results: List[dict]) -> str:
    """Compact the results into a prompt block the LLM can synthesize + cite."""
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()[:_BODY_MAX]
        href = r.get("href", "") or r.get("url", "")
        lines.append("[%d] %s\n%s\nFonte: %s" % (i, title, body, href))
    return "\n\n".join(lines)


SEARCH_SYSTEM = (
    "Rispondi alla domanda dell'utente USANDO i risultati di ricerca forniti. "
    "Sii conciso e conversazionale (la risposta può essere letta ad alta voce). "
    "Cita le fonti tra parentesi quadre [1], [2] quando usi un'informazione. "
    "Se i risultati non bastano, dillo con onestà. Rispondi in italiano."
)


def answer_with_search(question: str, url: str, model: str, timeout: float = 60.0,
                       on_sentence: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Free-internet answer: search + STREAMED local synthesis. on_sentence is
    called per completed sentence (for early text-to-speech). Returns the full
    answer, or None if the search failed."""
    results = search(question)
    if not results:
        return None
    context = results_context(results)
    buf, full = "", []
    try:
        with requests.post(
            url + "/api/chat",
            json={"model": model, "stream": True, "keep_alive": "5m",
                  "options": {"temperature": 0.2, "num_predict": 250},
                  "messages": [
                      {"role": "system", "content": SEARCH_SYSTEM},
                      {"role": "user", "content": "DOMANDA: %s\n\nRISULTATI:\n%s" % (question, context)},
                  ]},
            timeout=timeout, stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                piece = json.loads(line).get("message", {}).get("content", "")
                if not piece:
                    continue
                buf += piece
                full.append(piece)
                if on_sentence:
                    while True:
                        m = _SENTENCE_END.search(buf)
                        if not m:
                            break
                        sent, buf = buf[:m.end()].strip(), buf[m.end():]
                        if sent:
                            on_sentence(sent)
        if on_sentence and buf.strip():
            on_sentence(buf.strip())
    except (requests.RequestException, ValueError, KeyError):
        return None
    return "".join(full).strip() or None
