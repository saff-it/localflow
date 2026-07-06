"""Boost Ultra: the optional Claude channel. Inert without an API key.

Used only when the user turns Boost on (deliberate, session-only) or confirms a
one-off. Higher quality text + vision + built-in web search. Costs a few cents
per call — that's why it's opt-in and never the default. The key lives in
~/.localflow/secrets (chmod 600, gitignored), or the ANTHROPIC_API_KEY env var.
"""
import json
import os
import pathlib
from typing import List, Optional

import requests

SECRET_PATH = pathlib.Path.home() / ".localflow" / "secrets"
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"  # override via [assistant] cloud_model

SYSTEM = (
    "Sei l'assistente di LocalFlow in modalità Boost. Rispondi in italiano, in modo "
    "conciso e conversazionale (la risposta può essere letta ad alta voce). Quando "
    "guardi una schermata, aiuta l'utente concretamente con ciò che sta facendo."
)


def api_key() -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        for line in SECRET_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"')
            if line.startswith("sk-"):
                return line
    except OSError:
        pass
    return None


def available() -> bool:
    return api_key() is not None


def save_key(key: str) -> None:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text("ANTHROPIC_API_KEY=%s\n" % key.strip(), encoding="utf-8")
    os.chmod(SECRET_PATH, 0o600)


def ask(question: str, history: Optional[List[dict]] = None, image_b64: Optional[str] = None,
        web_search: bool = True, model: str = DEFAULT_MODEL, timeout: float = 90.0) -> str:
    """One Claude turn. image_b64 → vision; web_search → live internet with
    citations. Returns the answer text, or an honest error string."""
    key = api_key()
    if not key:
        return "Boost non disponibile: manca la chiave API di Claude."
    content: list = [{"type": "text", "text": question}]
    if image_b64:
        content.insert(0, {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": image_b64}})
    messages = list(history or []) + [{"role": "user", "content": content}]
    body = {"model": model, "max_tokens": 1024, "system": SYSTEM, "messages": messages}
    if web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    try:
        resp = requests.post(
            API_URL, headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }, data=json.dumps(body), timeout=timeout)
        if resp.status_code != 200:
            return "Boost: errore Claude (%s). %s" % (resp.status_code, resp.text[:120])
        blocks = resp.json().get("content", [])
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return text or "(nessuna risposta)"
    except requests.RequestException as exc:
        return "Boost: rete non raggiungibile (%s)." % type(exc).__name__
