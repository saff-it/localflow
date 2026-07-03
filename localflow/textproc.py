"""Deterministic text post-processing: whitespace tidy + personal dictionary."""
import re
from typing import Dict


def tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def join_chunks(texts) -> str:
    """Join per-chunk transcripts: when a chunk doesn't close a sentence, the
    next one continues it, so its spurious leading capital gets lowered."""
    joined = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if joined and joined[-1] not in ".!?…":
            if len(text) > 1 and text[0].isupper() and text[1].islower():
                text = text[0].lower() + text[1:]
            joined += " " + text
        else:
            joined = (joined + " " + text).strip()
    return joined


def apply_dictionary(text: str, replacements: Dict[str, str]) -> str:
    """Case-insensitive, word-boundary replacements ("wrong" -> "right")."""
    for wrong, right in replacements.items():
        pattern = re.compile(r"(?i)(?<!\w)" + re.escape(wrong) + r"(?!\w)")
        text = pattern.sub(right, text)
    return text
