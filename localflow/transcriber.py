"""Local ASR via faster-whisper (CTranslate2). Wispr's cloud ASR stage, on-device."""
from typing import Optional, Tuple


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
    ):
        from faster_whisper import WhisperModel  # heavy import, keep it here

        self._model = WhisperModel(resolve_model(model_name), device="cpu", compute_type=compute_type)
        self.language: Optional[str] = language or None
        self.initial_prompt: Optional[str] = initial_prompt or None

    def transcribe(self, audio) -> Tuple[str, str]:
        """audio: numpy float32 mono @16 kHz, or a file path. Returns (text, language)."""
        segments, info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            initial_prompt=self.initial_prompt,
            beam_size=5,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text, (info.language or "")
