"""The dictation daemon: hold key -> record -> transcribe -> clean -> paste.

LocalFlowDaemon is UI-agnostic: `localflow run` drives it headless from the
terminal, `localflow ui` wraps it in a menu-bar app. Language and polish can
be changed at runtime (the ASR engine is rebuilt behind the busy lock).
"""
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


class LocalFlowDaemon:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.status = "avvio..."
        self.recorder = audio.Recorder(cfg.sample_rate, cfg.device or None)
        try:
            self.recorder.ensure_open()  # open the mic once, up front (permission prompt included)
        except Exception as exc:
            print("⚠️  impossibile aprire il microfono: %s" % exc)
        self.transcriber = create_transcriber(cfg, initial_prompt=_glossary_prompt(cfg), persistent=True)
        print("ASR engine: %s" % type(self.transcriber).__name__)
        self.use_llm = cfg.format_enabled and formatter.available(cfg.ollama_url)
        if self.use_llm:
            print("AI cleanup: on — %s via Ollama" % cfg.ollama_model)
            threading.Thread(target=formatter.warmup, args=(cfg.ollama_url, cfg.ollama_model), daemon=True).start()
        else:
            print("AI cleanup: off")
        self._busy = threading.Lock()
        self._last_paste = 0.0
        self._listener = None
        self.status = "pronto"

    # -- runtime switches (called from the menu bar) --------------------------

    def set_language(self, lang: str) -> None:
        """Persist + rebuild the ASR engine with the new language (a few seconds)."""
        config.set_key("language", '"%s"' % lang)
        self.cfg.language = lang
        with self._busy:
            self.status = "cambio lingua..."
            old = self.transcriber
            self.transcriber = create_transcriber(
                self.cfg, initial_prompt=_glossary_prompt(self.cfg), persistent=True
            )
            if hasattr(old, "close"):
                old.close()
            self.status = "pronto"

    def pause(self) -> None:
        """Stop listening AND release the microphone (orange dot goes away)."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.recorder.close()
        self.status = "in pausa"

    def resume(self) -> None:
        self.recorder.ensure_open()
        self.start_listener()
        self.status = "pronto"

    def set_polish(self, enabled: bool) -> None:
        config.set_key("enabled", "true" if enabled else "false")
        self.cfg.format_enabled = enabled
        self.use_llm = enabled and formatter.available(self.cfg.ollama_url)
        if self.use_llm:
            threading.Thread(
                target=formatter.warmup, args=(self.cfg.ollama_url, self.cfg.ollama_model), daemon=True
            ).start()

    # -- dictation pipeline ----------------------------------------------------

    def _on_start(self):
        _play(SOUND_START, self.cfg.sounds)
        try:
            self.recorder.start()
        except Exception as exc:  # a crash here would kill the hotkey listener
            print("⚠️  microfono non disponibile: %s" % exc)

    def _on_stop(self):
        clip = self.recorder.stop()
        duration = len(clip) / float(self.cfg.sample_rate)
        if duration < MIN_UTTERANCE_SECONDS:
            print("(ignorato: %.2fs di audio — pressione troppo breve o mic muto)" % duration)
            return
        app_name = inject.frontmost_app() if (self.use_llm and self.cfg.app_aware_tone) else ""

        def work():
            with self._busy:
                self.status = "trascrivo..."
                try:
                    self._process(clip, duration, app_name)
                except Exception as exc:  # never die silently in a worker thread
                    print("⚠️  errore dettatura: %s" % exc)
                finally:
                    self.status = "pronto"

        threading.Thread(target=work, daemon=True).start()

    def _process(self, clip, duration, app_name):
        cfg = self.cfg
        started = time.time()
        pieces = audio.split_on_silence(clip, cfg.sample_rate)
        results = [self.transcriber.transcribe(piece) for piece in pieces]
        text = textproc.join_chunks(t for t, _ in results)
        lang = results[0][1]
        asr_secs = time.time() - started
        text = textproc.tidy(text)
        if not text:
            return
        llm_started = time.time()
        if self.use_llm:
            raw_text = text
            text = formatter.cleanup(text, cfg.ollama_url, cfg.ollama_model, app_name)
            if text != raw_text:
                print("  (grezzo: %s)" % raw_text)
        llm_secs = time.time() - llm_started
        text = textproc.apply_dictionary(text, cfg.replacements)
        if cfg.paste:
            # Consecutive dictations: add the space the previous paste didn't leave.
            if self._last_paste and time.time() - self._last_paste < 180 and not text[0].isspace():
                text = " " + text
            if inject.paste_into_frontmost(text, cfg.restore_clipboard):
                self._last_paste = time.time()
        else:
            inject.set_clipboard(text)
        _play(SOUND_DONE, cfg.sounds)
        print("[%4.1fs audio | asr %.1fs + llm %.1fs | %s] %s" % (duration, asr_secs, llm_secs, lang, text))

    # -- lifecycle ---------------------------------------------------------------

    def start_listener(self) -> None:
        from pynput import keyboard

        key = hotkey.parse_key(self.cfg.hotkey)
        holder = hotkey.HoldToTalk(key, self._on_start, self._on_stop)
        self._listener = keyboard.Listener(on_press=holder._on_press, on_release=holder._on_release)
        self._listener.start()

    def shutdown(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self.recorder.close()
        if hasattr(self.transcriber, "close"):
            self.transcriber.close()


def main() -> None:
    # pkill/launchd stop the daemon with SIGTERM: exit via sys.exit so atexit
    # handlers run and the whisper-server child is terminated (no orphans).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    cfg = config.load()
    print("LocalFlow — 100% local dictation, nothing leaves this Mac.")
    print("hotkey=hold '%s'  config=%s" % (cfg.hotkey, config.CONFIG_PATH))
    daemon = LocalFlowDaemon(cfg)
    daemon.start_listener()
    print("Ready. Hold the hotkey, speak, release. Ctrl+C to quit.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nBye.")
        daemon.shutdown()
