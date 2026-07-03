"""Metal-accelerated ASR backend via the whisper.cpp CLI (brew install whisper-cpp).

Same transcribe() interface as transcriber.Transcriber, so the two engines are
interchangeable. whisper.cpp runs on the Apple GPU, which is what makes
large-v3-turbo usable on machines where CPU inference takes 20+ seconds.
"""
import atexit
import os
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import time
import wave
from typing import Optional, Tuple

import numpy as np

MODELS_DIR = pathlib.Path.home() / ".localflow" / "models"
_LANG_RE = re.compile(r"auto-detected language: (\w+)")


def find_binary(name: str = "whisper-cli") -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    brew = "/opt/homebrew/bin/" + name
    return brew if os.path.exists(brew) else None


def write_wav(audio: np.ndarray, sample_rate: int, path: str) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _as_wav(audio, sample_rate: int) -> Tuple[str, Optional[str]]:
    """Returns (path_to_use, tmp_path_to_delete)."""
    if isinstance(audio, np.ndarray):
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="localflow-")
        os.close(fd)
        write_wav(audio, sample_rate, tmp_path)
        return tmp_path, tmp_path
    return str(audio), None


def ggml_model_path(name: str) -> pathlib.Path:
    return MODELS_DIR / ("ggml-" + name + ".bin")


class WhisperCppTranscriber:
    def __init__(
        self,
        model_name: str,
        language: str = "",
        initial_prompt: str = "",
        binary: Optional[str] = None,
    ):
        self.binary = binary or find_binary()
        if self.binary is None:
            raise RuntimeError("whisper-cli not found (brew install whisper-cpp)")
        self.model_path = ggml_model_path(model_name)
        if not self.model_path.exists():
            raise RuntimeError("ggml model missing: %s (run: localflow download ggml:%s)" % (self.model_path, model_name))
        self.language = language or "auto"
        self.initial_prompt = initial_prompt

    def transcribe(self, audio, sample_rate: int = 16000) -> Tuple[str, str]:
        """audio: numpy float32 mono @16 kHz, or a path to an audio file."""
        source, tmp_path = _as_wav(audio, sample_rate)
        cmd = [
            self.binary,
            "-m", str(self.model_path),
            "-f", source,
            "-l", self.language,
            "--no-prints",
            "--no-timestamps",
            "-fa", "1",  # flash attention: free speedup on Metal
        ]
        if self.initial_prompt:
            cmd += ["--prompt", self.initial_prompt]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        finally:
            if tmp_path:
                os.unlink(tmp_path)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-1:] or ["?"]
            raise RuntimeError("whisper-cli failed: " + tail[0])
        text = " ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
        match = _LANG_RE.search(result.stderr or "")
        lang = match.group(1) if match else (self.language if self.language != "auto" else "")
        return text, lang


class WhisperCppServer:
    """Keeps the model resident on the GPU via whisper-server: per-utterance cost
    is pure inference instead of model reload (the whisper-cli tax, ~5s/call)."""

    def __init__(self, model_name: str, language: str = "", initial_prompt: str = ""):
        binary = find_binary("whisper-server")
        if binary is None:
            raise RuntimeError("whisper-server not found (brew install whisper-cpp)")
        model_path = ggml_model_path(model_name)
        if not model_path.exists():
            raise RuntimeError("ggml model missing: %s (run: localflow download ggml:%s)" % (model_path, model_name))
        self.language = language or "auto"
        self.initial_prompt = initial_prompt
        with socket.socket() as probe:  # grab a free ephemeral port
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._proc = subprocess.Popen(
            [binary, "-m", str(model_path), "--host", "127.0.0.1", "--port", str(self.port),
             "-l", self.language, "-fa", "1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(self.close)
        self._wait_ready()

    def _wait_ready(self, timeout: float = 90.0) -> None:
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("whisper-server exited during startup (bad model file?)")
            try:
                requests.get("http://127.0.0.1:%d/" % self.port, timeout=1)
                return
            except requests.RequestException:
                time.sleep(0.3)
        raise RuntimeError("whisper-server did not become ready in %.0fs" % timeout)

    def transcribe(self, audio, sample_rate: int = 16000) -> Tuple[str, str]:
        import requests

        source, tmp_path = _as_wav(audio, sample_rate)
        data = {"temperature": "0.0", "response_format": "json"}
        if self.initial_prompt:
            data["prompt"] = self.initial_prompt
        try:
            with open(source, "rb") as fh:
                resp = requests.post(
                    "http://127.0.0.1:%d/inference" % self.port,
                    files={"file": ("audio.wav", fh, "audio/wav")},
                    data=data,
                    timeout=120,
                )
        finally:
            if tmp_path:
                os.unlink(tmp_path)
        resp.raise_for_status()
        text = " ".join(resp.json().get("text", "").split())
        return text, (self.language if self.language != "auto" else "")

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
