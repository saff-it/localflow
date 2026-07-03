"""Local ASR via faster-whisper (CTranslate2). Wispr's cloud ASR stage, on-device."""
from typing import Optional, Tuple


def create_transcriber(cfg, model: Optional[str] = None, language: Optional[str] = None,
                       initial_prompt: str = "", persistent: bool = False):
    """Pick the best available engine: whisper.cpp (Metal GPU) when binary+model
    are present, faster-whisper (CPU) otherwise. cfg.engine can force either.
    persistent=True (the daemon) keeps the model resident via whisper-server;
    one-shot CLI calls use whisper-cli instead."""
    from . import whispercpp

    lang = cfg.language if language is None else language
    want_cpp = cfg.engine in ("auto", "whispercpp")
    if want_cpp and whispercpp.find_binary() and whispercpp.ggml_model_path(cfg.whispercpp_model).exists():
        cls = whispercpp.WhisperCppServer if persistent else whispercpp.WhisperCppTranscriber
        return cls(cfg.whispercpp_model, lang, initial_prompt)
    if cfg.engine == "whispercpp":
        raise RuntimeError("engine=whispercpp but whisper-cli or the ggml model is missing")
    return Transcriber(model or cfg.model, cfg.compute_type, lang, initial_prompt, cfg.beam_size)


def resolve_model(name: str) -> str:
    """Prefer a locally downloaded model dir (localflow download <name>) over the HF hub cache."""
    from .download import model_dir

    local = model_dir(name)
    if (local / "model.bin").exists():
        return str(local)
    return name


class Transcriber:
    def __init__(
        self,
        model_name: str,
        compute_type: str = "int8",
        language: str = "",
        initial_prompt: str = "",
        beam_size: int = 1,
    ):
        from faster_whisper import WhisperModel  # heavy import, keep it here

        self._model = WhisperModel(resolve_model(model_name), device="cpu", compute_type=compute_type)
        self.language: Optional[str] = language or None
        self.initial_prompt: Optional[str] = initial_prompt or None
        self.beam_size = beam_size

    def transcribe(self, audio) -> Tuple[str, str]:
        """audio: numpy float32 mono @16 kHz, or a file path. Returns (text, language)."""
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            initial_prompt=self.initial_prompt,
            beam_size=self.beam_size,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (info.language or "")
