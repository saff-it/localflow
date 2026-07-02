"""Configuration: ~/.localflow/config.toml, created with commented defaults on first run."""
import dataclasses
import pathlib
from typing import Dict, List

try:
    import tomllib  # Python >= 3.11
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib

CONFIG_DIR = pathlib.Path.home() / ".localflow"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """\
# LocalFlow — private, fully-local dictation. Edit and restart.

[hotkey]
# Hold this key to talk; release to transcribe & paste.
# Options: alt_r (right Option), alt_l, cmd_r, ctrl_r, ctrl_l, f13 ... or a single character.
# The fn key cannot be captured from Python — that one needs a native event tap (see roadmap).
key = "alt_r"

[audio]
sample_rate = 16000
device = ""            # "" = system default input device

[asr]
model = "small"        # tiny | base | small | medium | large-v3 | large-v3-turbo (best quality, ~1.5 GB)
language = ""          # "" = auto-detect per utterance; or force "it", "en", ...
compute_type = "int8"

[format]
enabled = true         # AI cleanup via local Ollama; skipped automatically when Ollama isn't running
ollama_url = "http://127.0.0.1:11434"
ollama_model = "llama3.2:3b"
app_aware_tone = true  # tell the LLM which app the text is being pasted into

[dictionary]
# Terms Whisper should get right (names, jargon) — bias the transcription.
terms = []             # e.g. ["LocalMind Lab", "Traefik", "n8n"]

[dictionary.replacements]
# Hard corrections applied after transcription: "wrong" = "right"
# "local mind" = "LocalMind"

[output]
paste = true             # false = only copy the text to the clipboard
restore_clipboard = true # put the previous clipboard content back after pasting
sounds = true            # feedback sounds on record start / text ready
"""


@dataclasses.dataclass
class Config:
    hotkey: str = "alt_r"
    sample_rate: int = 16000
    device: str = ""
    model: str = "small"
    language: str = ""
    compute_type: str = "int8"
    format_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    app_aware_tone: bool = True
    terms: List[str] = dataclasses.field(default_factory=list)
    replacements: Dict[str, str] = dataclasses.field(default_factory=dict)
    paste: bool = True
    restore_clipboard: bool = True
    sounds: bool = True


def load() -> Config:
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding="utf-8")
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = Config()

    hk = data.get("hotkey", {})
    cfg.hotkey = hk.get("key", cfg.hotkey)

    au = data.get("audio", {})
    cfg.sample_rate = int(au.get("sample_rate", cfg.sample_rate))
    cfg.device = au.get("device", cfg.device)

    asr = data.get("asr", {})
    cfg.model = asr.get("model", cfg.model)
    cfg.language = asr.get("language", cfg.language)
    cfg.compute_type = asr.get("compute_type", cfg.compute_type)

    fmt = data.get("format", {})
    cfg.format_enabled = bool(fmt.get("enabled", cfg.format_enabled))
    cfg.ollama_url = fmt.get("ollama_url", cfg.ollama_url).rstrip("/")
    cfg.ollama_model = fmt.get("ollama_model", cfg.ollama_model)
    cfg.app_aware_tone = bool(fmt.get("app_aware_tone", cfg.app_aware_tone))

    dic = data.get("dictionary", {})
    cfg.terms = list(dic.get("terms", []))
    cfg.replacements = dict(dic.get("replacements", {}))

    out = data.get("output", {})
    cfg.paste = bool(out.get("paste", cfg.paste))
    cfg.restore_clipboard = bool(out.get("restore_clipboard", cfg.restore_clipboard))
    cfg.sounds = bool(out.get("sounds", cfg.sounds))
    return cfg
