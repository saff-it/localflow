"""Configuration: ~/.localflow/config.toml, created with commented defaults on first run."""
import dataclasses
import pathlib
import re
from typing import Dict, List

try:
    import tomllib  # Python >= 3.11
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib

# La soglia della voce si calibra sui clip veri, quindi vive in audio.py insieme
# alla misura. Qui la si legge e basta: `app.py` fa
# `audio.MIN_SPEECH_RMS = cfg.min_speech_rms`, cioe' il config vince sempre sul
# codice, e una seconda copia scritta a mano qui zittisce ogni ricalibrazione
# (successo dal 15 al 21 agosto 2026: dettature scartate senza motivo apparente).
from .audio import MIN_SPEECH_RMS

CONFIG_DIR = pathlib.Path.home() / ".localflow"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = """\
# LocalFlow — private, fully-local dictation. Edit and restart.

[hotkey]
# Hold this key to talk; release to transcribe & paste.
# Options: alt_r (right Option), alt_l, cmd_r, ctrl_r, ctrl_l, f13 ... or a single character.
# The fn key cannot be captured from Python — that one needs a native event tap (see roadmap).
key = "alt_r"
copy_key = ""          # secondo tasto: detta e COPIA negli appunti senza incollare ("" = disattivato)
assistant_key = ""       # tasto assistente vocale. USA UN TASTO FUNZIONE (f13/f14): i
                         # modificatori (cmd/alt/ctrl) confliggono con le scorciatoie. "" = spento

[audio]
sample_rate = 16000
device = ""            # "" = system default input device
mic_release_seconds = 300 # release the mic (orange dot off) after N idle seconds; 0 = always ready
debug_keep_audio = true   # keep the last 5 dictation clips in ~/.localflow/debug (local only) for tuning
min_speech_rms = @MIN_SPEECH_RMS@  # soglia voce del cancello anti-allucinazioni (abbassala se parli piano)

[asr]
engine = "auto"        # auto = whisper.cpp (Metal GPU) if available, else faster-whisper (CPU)
whispercpp_model = "large-v3-turbo-q8_0"  # ggml model for whisper.cpp (localflow download ggml:<name>)
model = "small"        # faster-whisper fallback: tiny | base | small | medium | large-v3 | large-v3-turbo
language = ""          # "" = auto-detect per utterance; or force "it", "en", ...
compute_type = "int8"
beam_size = 5          # 5 = più accurato, gratis su GPU (whisper.cpp); con fallback CPU conviene 1
translate = false          # parla italiano, esce inglese
translate_quality = true   # true = traduzione AI (inglese naturale, ~6-9s); false = nativa Whisper (letterale, ~3s)
streaming = true       # trascrivi MENTRE parli: al rilascio resta solo la coda (~1s di attesa)
chunk_seconds = 7      # dimensione dei blocchi trascritti in corsa (tagliati nelle tue pause)

[assistant]
enabled = true         # assistente locale (risponde in notifica); richiede Ollama
voice_enabled = false  # leggi la risposta ad alta voce? (canale primario = notifica)
voice = "Alice"        # voce macOS per la risposta parlata (say -v ?); "" = voce di sistema
rate = 0               # velocità voce (parole/min); 0 = predefinita
model = ""             # modello LLM dell'assistente; "" = usa ollama_model
vision = true          # può vedere lo schermo quando la domanda lo richiede (modello locale)
vision_model = "qwen2.5vl:3b"  # modello visivo locale (gratis); on-demand, poi scaricato
internet = true        # può cercare online (DuckDuckGo gratis + sintesi locale) quando serve
search_model = "qwen2.5:7b"  # sintesi dei risultati web: 7b (accurato); il 3b sbagliava i fatti
cloud_model = "claude-sonnet-4-5"  # modello Claude per il Boost Ultra (a pagamento, opt-in)

[format]
enabled = true         # AI cleanup via local Ollama; skipped automatically when Ollama isn't running
punctuate = false      # rescue pass on long unpunctuated dictations — costs ~5GB RAM parked for the LLM
paragraphs = "auto"    # auto (paragrafi solo fuori dalle chat) | always | never — spezza testi lunghi in paragrafi/elenchi
structure = true       # liste e paragrafi a REGOLE: nessun modello, nessuna attesa, nessuna parola cambiata
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
restore_clipboard = false # false = la dettatura resta negli appunti (comportamento da tool di dettatura)
sounds = true            # feedback sounds on record start / text ready
""".replace("@MIN_SPEECH_RMS@", repr(MIN_SPEECH_RMS))


@dataclasses.dataclass
class Config:
    hotkey: str = "alt_r"
    copy_hotkey: str = ""
    assistant_key: str = "alt_r"
    assistant_enabled: bool = True
    assistant_voice_enabled: bool = False
    assistant_voice: str = "Alice"
    assistant_rate: int = 0
    assistant_model: str = ""
    assistant_vision: bool = True
    assistant_vision_model: str = "qwen2.5vl:3b"
    assistant_internet: bool = True
    assistant_search_model: str = "qwen2.5:7b"
    assistant_cloud_model: str = "claude-sonnet-4-5"
    sample_rate: int = 16000
    device: str = ""
    mic_release_seconds: float = 300.0
    debug_keep_audio: bool = True
    min_speech_rms: float = MIN_SPEECH_RMS
    model: str = "small"
    language: str = ""
    compute_type: str = "int8"
    beam_size: int = 5
    engine: str = "auto"
    whispercpp_model: str = "large-v3-turbo-q8_0"
    translate_enabled: bool = False
    translate_quality: bool = True
    streaming_enabled: bool = True
    chunk_seconds: float = 7.0
    format_enabled: bool = True
    punctuate_enabled: bool = False
    paragraphs: str = "auto"
    structure_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    app_aware_tone: bool = True
    terms: List[str] = dataclasses.field(default_factory=list)
    replacements: Dict[str, str] = dataclasses.field(default_factory=dict)
    paste: bool = True
    restore_clipboard: bool = False
    sounds: bool = True


def set_key(key: str, raw_toml_value: str) -> None:
    """Rewrite `key = value` in config.toml in place, keeping comments.
    Works because the keys we touch (language, enabled) are unique in the template."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"(?m)^(%s\s*=\s*)([^#\n]*)" % re.escape(key))
    text = pattern.sub(lambda m: m.group(1) + raw_toml_value + " ", text, count=1)
    CONFIG_PATH.write_text(text, encoding="utf-8")


def load() -> Config:
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding="utf-8")
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = Config()

    hk = data.get("hotkey", {})
    cfg.hotkey = hk.get("key", cfg.hotkey)
    cfg.copy_hotkey = hk.get("copy_key", cfg.copy_hotkey)
    cfg.assistant_key = hk.get("assistant_key", cfg.assistant_key)

    asst = data.get("assistant", {})
    cfg.assistant_enabled = bool(asst.get("enabled", cfg.assistant_enabled))
    cfg.assistant_voice_enabled = bool(asst.get("voice_enabled", cfg.assistant_voice_enabled))
    cfg.assistant_voice = asst.get("voice", cfg.assistant_voice)
    cfg.assistant_rate = int(asst.get("rate", cfg.assistant_rate))
    cfg.assistant_model = asst.get("model", cfg.assistant_model)
    cfg.assistant_vision = bool(asst.get("vision", cfg.assistant_vision))
    cfg.assistant_vision_model = asst.get("vision_model", cfg.assistant_vision_model)
    cfg.assistant_internet = bool(asst.get("internet", cfg.assistant_internet))
    cfg.assistant_search_model = asst.get("search_model", cfg.assistant_search_model)
    cfg.assistant_cloud_model = asst.get("cloud_model", cfg.assistant_cloud_model)

    au = data.get("audio", {})
    cfg.sample_rate = int(au.get("sample_rate", cfg.sample_rate))
    cfg.device = au.get("device", cfg.device)
    cfg.mic_release_seconds = float(au.get("mic_release_seconds", cfg.mic_release_seconds))
    cfg.debug_keep_audio = bool(au.get("debug_keep_audio", cfg.debug_keep_audio))
    cfg.min_speech_rms = float(au.get("min_speech_rms", cfg.min_speech_rms))

    asr = data.get("asr", {})
    cfg.model = asr.get("model", cfg.model)
    cfg.language = asr.get("language", cfg.language)
    cfg.compute_type = asr.get("compute_type", cfg.compute_type)
    cfg.beam_size = int(asr.get("beam_size", cfg.beam_size))
    cfg.engine = asr.get("engine", cfg.engine)
    cfg.whispercpp_model = asr.get("whispercpp_model", cfg.whispercpp_model)
    cfg.translate_enabled = bool(asr.get("translate", cfg.translate_enabled))
    cfg.translate_quality = bool(asr.get("translate_quality", cfg.translate_quality))
    cfg.streaming_enabled = bool(asr.get("streaming", cfg.streaming_enabled))
    cfg.chunk_seconds = float(asr.get("chunk_seconds", cfg.chunk_seconds))

    fmt = data.get("format", {})
    cfg.format_enabled = bool(fmt.get("enabled", cfg.format_enabled))
    cfg.punctuate_enabled = bool(fmt.get("punctuate", cfg.punctuate_enabled))
    cfg.paragraphs = fmt.get("paragraphs", cfg.paragraphs)
    cfg.structure_enabled = bool(fmt.get("structure", cfg.structure_enabled))
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
