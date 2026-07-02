"""The dictation daemon: hold key -> record -> transcribe -> clean -> paste."""
import subprocess
import threading
import time

from . import audio, config, formatter, hotkey, inject, textproc
from .transcriber import Transcriber

SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_DONE = "/System/Library/Sounds/Glass.aiff"

MIN_UTTERANCE_SECONDS = 0.3  # shorter than this = accidental tap, skip (also kills silence hallucinations)


def _play(path: str, enabled: bool) -> None:
    if enabled:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _glossary_prompt(cfg: config.Config) -> str:
    terms = list(cfg.terms) + list(cfg.replacements.values())
    return ("Glossary: " + ", ".join(terms) + ".") if terms else ""


def main() -> None:
    cfg = config.load()
    print("LocalFlow — 100% local dictation, nothing leaves this Mac.")
    print("model=%s (%s)  hotkey=hold '%s'  config=%s" % (cfg.model, cfg.compute_type, cfg.hotkey, config.CONFIG_PATH))
    print("Loading Whisper model (first run downloads it)...")
    transcriber = Transcriber(cfg.model, cfg.compute_type, cfg.language, _glossary_prompt(cfg))
    recorder = audio.Recorder(cfg.sample_rate, cfg.device or None)
    use_llm = cfg.format_enabled and formatter.available(cfg.ollama_url)
    if use_llm:
        print("AI cleanup: on — %s via Ollama" % cfg.ollama_model)
    else:
        print("AI cleanup: off (Ollama not reachable — raw Whisper output will be pasted)")
    print("Ready. Hold the hotkey, speak, release. Ctrl+C to quit.")

    busy = threading.Lock()  # serialize utterances so outputs can't interleave

    def on_start():
        _play(SOUND_START, cfg.sounds)
        recorder.start()

    def on_stop():
        clip = recorder.stop()
        duration = len(clip) / float(cfg.sample_rate)
        if duration < MIN_UTTERANCE_SECONDS:
            return

        def work():
            with busy:
                started = time.time()
                text, lang = transcriber.transcribe(clip)
                text = textproc.tidy(text)
                if not text:
                    return
                app_name = inject.frontmost_app() if (use_llm and cfg.app_aware_tone) else ""
                if use_llm:
                    text = formatter.cleanup(text, cfg.ollama_url, cfg.ollama_model, app_name)
                text = textproc.apply_dictionary(text, cfg.replacements)
                if cfg.paste:
                    inject.paste_into_frontmost(text, cfg.restore_clipboard)
                else:
                    inject.set_clipboard(text)
                _play(SOUND_DONE, cfg.sounds)
                print("[%4.1fs audio | %4.1fs | %s] %s" % (duration, time.time() - started, lang, text))

        threading.Thread(target=work, daemon=True).start()

    key = hotkey.parse_key(cfg.hotkey)
    listener = hotkey.HoldToTalk(key, on_start, on_stop)
    try:
        listener.run_forever()
    except KeyboardInterrupt:
        print("\nBye.")
