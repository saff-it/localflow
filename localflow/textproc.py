"""Deterministic text post-processing: whitespace tidy + personal dictionary."""
import re
from typing import Dict


def tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def apply_dictionary(text: str, replacements: Dict[str, str]) -> str:
    """Case-insensitive, word-boundary replacements ("wrong" -> "right")."""
    for wrong, right in replacements.items():
        pattern = re.compile(r"(?i)(?<!\w)" + re.escape(wrong) + r"(?!\w)")
        text = pattern.sub(right, text)
    return text
