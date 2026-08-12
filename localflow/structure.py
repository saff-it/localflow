"""Liste e paragrafi dal parlato, con regole e senza modello.

Dettare produce un blocco unico: la spesa esce come una fila di virgole, e una
dettatura di un minuto esce senza un solo a capo. Il percorso LLM esisteva gia'
(`formatter.format_paragraphs`) ma costa 4,7 GB di RAM e 20 secondi misurati, quindi
resta spento. Qui non c'e' nessun modello: si riconoscono i segnali del parlato e si
inseriscono a capo.

La garanzia e' `_words_preserved`, che gira a ogni dettatura: possono comparire solo
spazi e il marcatore "- ", e l'unica parola che puo' sparire e' la "e" che unisce
l'ultimo elemento di un elenco. Qualunque altra differenza fa vincere il testo
originale. Vedi docs/superpowers/specs/2026-08-12-struttura-testo-design.md.
"""
import re
import unicodedata
from typing import Callable, List, Optional

# Frasi con cui si annuncia un elenco parlando. Senza una di queste, tre virgole
# di fila restano tre virgole di fila: e' cosi' che una frase normale non diventa
# una lista per sbaglio.
LIST_CUES = (
    "la lista", "le liste", "l'elenco", "l elenco", "elenco",
    "mi servono", "mi serve", "ci servono", "ci serve", "servono", "serve",
    "le cose sono", "le cose che ci servono", "dobbiamo comprare", "devo comprare",
    "compra", "prendi", "ti do", "ecco",
)

# Stacchi veri del discorso: aprono un paragrafo perche' cambiano argomento.
# Misurati sul log reale dell'utente, non presi da una lista generica.
PARAGRAPH_CUES = (
    "poi", "inoltre", "per quanto riguarda", "un'altra cosa", "un altra cosa",
    "infine", "in piu'", "in piu", "detto questo", "per il resto", "quindi per",
    "ultima cosa", "l'ultima cosa",
)

CONNECTORS = {"e", "ed", "and"}   # l'unica parola che una lista puo' perdere

MIN_LIST_ITEMS = 3        # due cose non sono un elenco, sono una frase
MAX_WORDS_PER_ITEM = 4    # oltre, sono proposizioni: non si spezzano
MIN_PARAGRAPH_CHARS = 250 # sotto, un a capo e' rumore


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _content_words(s: str) -> List[str]:
    s = _norm(s)
    s = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in s)
    return s.split()


def _words_preserved(original: str, formatted: str) -> bool:
    """Vero se il testo formattato dice esattamente le stesse parole, a parte i
    connettori di elenco che possono sparire. Niente parole nuove, niente parole
    cambiate, niente parole spostate."""
    import difflib

    a, b = _content_words(original), _content_words(formatted)
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete" and all(w in CONNECTORS for w in a[i1:i2]):
            continue
        return False
    return True


def _split_sentences(text: str) -> List[str]:
    """Spezza sui punti fermi tenendo la punteggiatura attaccata alla frase."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]


def _as_items(chunk: str) -> Optional[List[str]]:
    """Gli elementi di un elenco, o None se questo non e' un elenco."""
    pieces = [p.strip(" .;") for p in chunk.split(",")]
    if len(pieces) < 2:
        return None
    last = pieces[-1]
    tail = re.split(r"\b(?:e|ed|and)\b", last, maxsplit=1)
    if len(tail) == 2 and tail[0].strip() and tail[1].strip():
        pieces[-1] = tail[0].strip()
        pieces.append(tail[1].strip())
    pieces = [p for p in pieces if p]
    if len(pieces) < MIN_LIST_ITEMS:
        return None
    if any(len(p.split()) > MAX_WORDS_PER_ITEM for p in pieces):
        return None
    return pieces


def _bulletize(text: str) -> str:
    sentences = _split_sentences(text)
    for i, sentence in enumerate(sentences):
        if not any(cue in _norm(sentence) for cue in LIST_CUES):
            continue
        # gli elementi stanno dopo i due punti nella stessa frase...
        head, sep, tail = sentence.partition(":")
        if sep and _as_items(tail):
            items = _as_items(tail)
            sentences[i] = head.rstrip() + ":\n" + "\n".join("- " + it for it in items)
            return " ".join(sentences)
        # ...oppure nella frase successiva
        if i + 1 < len(sentences):
            items = _as_items(sentences[i + 1])
            if items:
                intro = sentence.rstrip().rstrip(".") + ":"
                block = intro + "\n" + "\n".join("- " + it for it in items)
                before = " ".join(sentences[:i])
                after = " ".join(sentences[i + 2:])
                # cio' che segue l'elenco riparte a capo: attaccato all'ultimo
                # trattino diventerebbe un elemento della lista che non e'.
                parts = [p for p in (before, block, after) if p]
                return "\n".join(parts) if len(parts) > 1 else parts[0]
    return text


def _paragraphize(text: str) -> str:
    if len(text) < MIN_PARAGRAPH_CHARS:
        return text
    sentences = _split_sentences(text)
    out: List[str] = []
    for sentence in sentences:
        opens = any(_norm(sentence).startswith(cue) for cue in PARAGRAPH_CUES)
        if opens and out:
            out.append("\n\n" + sentence)
        elif out:
            out.append(" " + sentence)
        else:
            out.append(sentence)
    return "".join(out)


def apply(text: str, enabled: bool = True, _rules: Optional[Callable[[str], str]] = None) -> str:
    """Testo con liste e paragrafi. Da spenta non analizza nemmeno: costo zero."""
    if not enabled or not text or not text.strip():
        return text
    if "\n" in text:
        return text  # gia' strutturato (assistente, traduzione): non ci si mette in mezzo
    rules = _rules or (lambda t: _paragraphize(_bulletize(t)))
    out = rules(text).strip()
    if not _words_preserved(text, out):
        return text  # la garanzia: se le parole non tornano, vince l'originale
    return out
