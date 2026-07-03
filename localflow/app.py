"""The dictation daemon: hold key -> record -> transcribe -> clean -> paste."""
import signal
import subprocess
import sys
import threading
import time

from . import audio, config, formatter, hotkey, inject, textproc
from .transcriber import create_transcriber

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
    # pkill/launchd stop the daemon with SIGTERM: exit via sys.exit so atexit
    # handlers run and the whisper-server child is terminated (no orphans).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    cfg = config.load()
    print("LocalFlow — 100% local dictation, nothing leaves this Mac.")
    print("model=%s (%s)  hotkey=hold '%s'  config=%s" % (cfg.model, cfg.compute_type, cfg.hotkey, config.CONFIG_PATH))
    print("Loading Whisper model (first run downloads it)...")
    transcriber = create_transcriber(cfg, initial_prompt=_glossary_prompt(cfg), persistent=True)
    print("ASR engine: %s" % type(transcriber).__name__)
    recorder = audio.Recorder(cfg.sample_rate, cfg.device or None)
    try:
        recorder.ensure_open()  # open the mic once, up front (permission prompt included)
    except Exception as exc:
        print("⚠️  impossibile aprire il microfono: %s" % exc)
    use_llm = cfg.format_enabled and formatter.available(cfg.ollama_url)
    if use_llm:
        print("AI cleanup: on — %s via Ollama" % cfg.ollama_model)
        # Preload the LLM now so the first dictation doesn't pay the cold start.
        threading.Thread(target=formatter.warmup, args=(cfg.ollama_url, cfg.ollama_model), daemon=True).start()
    else:
        print("AI cleanup: off (Ollama not reachable — raw Whisper output will be pasted)")
    print("Ready. Hold the hotkey, speak, release. Ctrl+C to quit.")

    busy = threading.Lock()  # serialize utterances so outputs can't interleave
    state = {"last_paste": 0.0}

    def on_start():
        _play(SOUND_START, cfg.sounds)
        try:
            recorder.start()
        except Exception as exc:  # a crash here would kill the hotkey listener
            print("⚠️  microfono non disponibile: %s" % exc)

    def on_stop():
        clip = recorder.stop()
        duration = len(clip) / float(cfg.sample_rate)
        if duration < MIN_UTTERANCE_SECONDS:
            print("(ignorato: %.2fs di audio — pressione troppo breve o mic muto)" % duration)
            return
        # Capture the destination app at key release, before any slow work.
        app_name = inject.frontmost_app() if (use_llm and cfg.app_aware_tone) else ""

        def work():
            with busy:
                try:
                    _process()
                except Exception as exc:  # never die silently in a worker thread
                    print("⚠️  errore dettatura: %s" % exc)

        def _process():
                started = time.time()
                text, lang = transcriber.transcribe(clip)
                asr_secs = time.time() - started
                text = textproc.tidy(text)
                if not text:
                    return
                llm_started = time.time()
                if use_llm:
                    raw_text = text
                    text = formatter.cleanup(text, cfg.ollama_url, cfg.ollama_model, app_name)
                    if text != raw_text:
                        print("  (grezzo: %s)" % raw_text)
                llm_secs = time.time() - llm_started
                text = textproc.apply_dictionary(text, cfg.replacements)
                if cfg.paste:
                    # Consecutive dictations: add the space the previous paste didn't leave.
                    if state["last_paste"] and time.time() - state["last_paste"] < 180 and not text[0].isspace():
                        text = " " + text
                    if inject.paste_into_frontmost(text, cfg.restore_clipboard):
                        state["last_paste"] = time.time()
                else:
                    inject.set_clipboard(text)
                _play(SOUND_DONE, cfg.sounds)
                print("[%4.1fs audio | asr %.1fs + llm %.1fs | %s] %s" % (duration, asr_secs, llm_secs, lang, text))

        threading.Thread(target=work, daemon=True).start()

    key = hotkey.parse_key(cfg.hotkey)
    listener = hotkey.HoldToTalk(key, on_start, on_stop)
    try:
        listener.run_forever()
    except KeyboardInterrupt:
        print("\nBye.")
