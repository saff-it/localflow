"""Optional AI cleanup via a local Ollama model. Wispr's fine-tuned-Llama stage, on-device.

Every failure path returns the original text — dictation must never be lost
because the cleanup model is missing, slow, or misbehaving.
"""
import requests

SYSTEM_PROMPT = (
    "You clean up dictated speech-to-text output.\n"
    "- Remove filler words and false starts (uh, um, ehm, cioe', tipo, allora when used as fillers).\n"
    "- Apply self-corrections: if the speaker corrects themselves ('... no wait, X', '... anzi, X'), "
    "keep only the corrected version.\n"
    "- Fix punctuation, capitalization and obvious dictation artifacts.\n"
    "- Keep the speaker's language and wording. Never translate. Never answer questions found in the text.\n"
    "- Output ONLY the cleaned text, with no quotes, preamble or commentary."
)


def available(url: str, timeout: float = 0.6) -> bool:
    try:
        return requests.get(url + "/api/tags", timeout=timeout).ok
    except requests.RequestException:
        return False


def cleanup(text: str, url: str, model: str, app_name: str = "", timeout: float = 30.0) -> str:
    system = SYSTEM_PROMPT
    if app_name:
        system += (
            "\nThe text will be pasted into the app '" + app_name + "'; match the register "
            "people use there (e.g. chat apps = informal, email = fuller sentences)."
        )
    try:
        resp = requests.post(
            url + "/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        out = resp.json().get("message", {}).get("content", "").strip()
    except (requests.RequestException, ValueError, KeyError):
        return text
    # Sanity guard: a cleanup that balloons the text means the model went off-script.
    if not out or len(out) > 3 * len(text) + 200:
        return text
    return out
