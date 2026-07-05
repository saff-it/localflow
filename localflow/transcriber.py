"""Local ASR via faster-whisper (CTranslate2). Wispr's cloud ASR stage, on-device."""
from typing import Optional, Tuple


def create_transcriber(cfg, model: Optional[str] = None, language: Optional[str] = None,
                       initial_prompt: str = "", persistent: bool = False,
                       native_translate: Optional[bool] = None):
    """Pick the best available engine: whisper.cpp (Metal GPU) when binary+model
    are present, faster-whisper (CPU) otherwise. cfg.engine can force either.
    persistent=True (the daemon) keeps the model resident via whisper-server;
    one-shot CLI calls use whisper-cli instead."""
    from . import whispercpp

    lang = cfg.language if language is None else language
    # native_translate: Whisper's own translate task. Overridable because the
    # daemon prefers LLM translation (turbo ASR + qwen) when Ollama is up.
    use_native = cfg.translate_enabled if native_translate is None else native_translate
    ggml_model = cfg.whispercpp_model
    if use_native and "turbo" in ggml_model:
        # turbo was distilled WITHOUT the translate task (outputs the source
        # language unchanged): translation needs the full large-v3.
        full = "large-v3-q5_0"
        if whispercpp.ggml_model_path(full).exists():
            ggml_model = full
    want_cpp = cfg.engine in ("auto", "whispercpp")
    if want_cpp and whispercpp.find_binary() and whispercpp.ggml_model_path(ggml_model).exists():
        cls = whispercpp.WhisperCppServer if persistent else whispercpp.WhisperCppTranscriber
        return cls(ggml_model, lang, initial_prompt, beam_size=cfg.beam_size,
                   translate=use_native)
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

    def transcribe(self, audio, sample_rate: int = 16000, prompt: Optional[str] = None) -> Tuple[str, str]:
        """audio: numpy float32 mono @16 kHz, or a file path. Returns (text, language)."""
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            initial_prompt=prompt if prompt is not None else self.initial_prompt,
            beam_size=self.beam_size,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (info.language or "")
