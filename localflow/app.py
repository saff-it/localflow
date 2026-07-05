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

from . import audio, config, formatter, hotkey, inject, streaming, textproc
from .transcriber import create_transcriber

SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_DONE = "/System/Library/Sounds/Glass.aiff"
SOUND_COPY = "/System/Library/Sounds/Tink.aiff"

MIN_UTTERANCE_SECONDS = 0.3  # shorter than this = accidental tap, skip (also kills silence hallucinations)


def _play(path: str, enabled: bool) -> None:
    if enabled:
        subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Whisper's initial_prompt is a STYLE example it tends to continue, not an
# instruction: a well-punctuated primer nudges it to punctuate even rushed
# speech with no prosodic pauses. Only used when the language is fixed
# (a primer in the wrong language would bias auto-detection).
PUNCTUATION_PRIMERS = {
    "it": "Perfetto, allora facciamo così: ci sentiamo domani alle 15, va bene? Benissimo, grazie mille.",
    "en": "Alright, let's do this: I'll call you tomorrow at 3 pm, okay? Great, thanks a lot.",
    "es": "Perfecto, entonces hacemos así: te llamo mañana a las tres, ¿vale? Genial, muchas gracias.",
}


def _glossary_prompt(cfg: config.Config) -> str:
    parts = []
    terms = list(cfg.terms) + list(cfg.replacements.values())
    if terms:
        parts.append("Glossario: " + ", ".join(terms) + ".")
    primer = PUNCTUATION_PRIMERS.get(cfg.language)
    if primer:
        parts.append(primer)
    return " ".join(parts)


class LocalFlowDaemon:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.status = "avvio..."
        self.recorder = audio.Recorder(cfg.sample_rate, cfg.device or None, cfg.mic_release_seconds)
        try:
            self.recorder.ensure_open()  # open the mic once, up front (permission prompt included)
        except Exception as exc:
            print("⚠️  impossibile aprire il microfono: %s" % exc)
        self.transcriber = create_transcriber(cfg, initial_prompt=_glossary_prompt(cfg), persistent=True)
        print("ASR engine: %s" % type(self.transcriber).__name__)
        self.ollama_up = formatter.available(cfg.ollama_url)
        self.use_llm = cfg.format_enabled and self.ollama_up
        if self.use_llm:
            print("AI cleanup: on — %s via Ollama" % cfg.ollama_model)
        else:
            print("AI cleanup: off")
        if self.ollama_up and cfg.punctuate_enabled:
            print("Punteggiatura di soccorso: on (solo su dettature lunghe senza segni)")
        if self.ollama_up and (self.use_llm or cfg.punctuate_enabled):
            threading.Thread(target=formatter.warmup, args=(cfg.ollama_url, cfg.ollama_model), daemon=True).start()
        self._busy = threading.Lock()
        self._last_paste = 0.0
        self._listener = None
        self._session = None
        self._monitor_stop = threading.Event()
        if cfg.streaming_enabled and getattr(self.transcriber, "supports_streaming", False):
            print("Streaming: on — trascrivo mentre parli (blocchi da %.0fs)" % cfg.chunk_seconds)
        threading.Thread(target=self._wake_watch, daemon=True).start()
        self.status = "pronto"

    def _wake_watch(self):
        """After standby macOS evicts the model from RAM/GPU: the first dictation
        paid ~7s of reload. Detect the wall-clock jump of a wake-up and re-warm
        the engine in the background before the user dictates."""
        last = time.time()
        while True:
            time.sleep(30)
            now = time.time()
            if now - last > 120:  # the Mac was asleep
                threading.Thread(target=self._warm_engine, daemon=True).start()
            last = now

    def _warm_engine(self):
        if not self._busy.acquire(False):  # never delay a real dictation
            return
        try:
            import numpy as np

            self.transcriber.transcribe(np.zeros(int(0.5 * self.cfg.sample_rate), dtype=np.float32))
            print("(motore riscaldato dopo lo standby)")
        except Exception:
            pass
        finally:
            self._busy.release()

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

    def set_model(self, ggml_name: str) -> None:
        """Persist + rebuild the ASR engine with another whisper.cpp model (precision/speed switch)."""
        config.set_key("whispercpp_model", '"%s"' % ggml_name)
        self.cfg.whispercpp_model = ggml_name
        with self._busy:
            self.status = "cambio modello..."
            old = self.transcriber
            self.transcriber = create_transcriber(
                self.cfg, initial_prompt=_glossary_prompt(self.cfg), persistent=True
            )
            if hasattr(old, "close"):
                old.close()
            self.status = "pronto"

    def set_hotkey(self, key_name: str) -> None:
        """Persist + swap the hold-to-talk key at runtime."""
        config.set_key("key", '"%s"' % key_name)
        self.cfg.hotkey = key_name
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.start_listener()

    def set_copy_hotkey(self, key_name: str) -> None:
        """Persist + swap (or disable, "") the dictate-and-copy key at runtime."""
        config.set_key("copy_key", '"%s"' % key_name)
        self.cfg.copy_hotkey = key_name
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.start_listener()

    def set_streaming(self, enabled: bool) -> None:
        config.set_key("streaming", "true" if enabled else "false")
        self.cfg.streaming_enabled = enabled

    def set_polish(self, enabled: bool) -> None:
        config.set_key("enabled", "true" if enabled else "false")
        self.cfg.format_enabled = enabled
        self.use_llm = enabled and formatter.available(self.cfg.ollama_url)
        if self.use_llm:
            threading.Thread(
                target=formatter.warmup, args=(self.cfg.ollama_url, self.cfg.ollama_model), daemon=True
            ).start()

    # -- dictation pipeline ----------------------------------------------------

    def _use_streaming(self) -> bool:
        return self.cfg.streaming_enabled and getattr(self.transcriber, "supports_streaming", False)

    def _monitor(self, session, stop_event):
        """Feeds drained mic samples into the streaming session 4×/second."""
        while not stop_event.is_set():
            time.sleep(0.25)
            if stop_event.is_set():
                break
            try:
                session.feed(self.recorder.drain())
            except Exception as exc:
                print("⚠️  streaming feed: %s" % exc)
                break

    def _on_start(self, copy_mode=False):
        self._copy_mode = copy_mode
        _play(SOUND_START, self.cfg.sounds)

        def go():  # never block the pynput callback thread: a hung CoreAudio
            try:   # call here used to kill the hotkey for good
                self.recorder.start()
            except Exception as exc:
                print("⚠️  microfono non disponibile: %s" % exc)
                return
            if self._use_streaming():
                self._monitor_stop = threading.Event()
                self._session = streaming.StreamingSession(
                    self.transcriber, self.cfg.sample_rate, self.cfg.chunk_seconds,
                    base_prompt=_glossary_prompt(self.cfg),
                )
                threading.Thread(target=self._monitor,
                                 args=(self._session, self._monitor_stop), daemon=True).start()

        threading.Thread(target=go, daemon=True).start()

    def _on_stop(self):
        session, self._session = self._session, None
        if session is not None:
            self._monitor_stop.set()
        clip = self.recorder.stop()
        duration = len(clip) / float(self.cfg.sample_rate)
        if session is not None:
            duration += session.total_seconds
        if duration < MIN_UTTERANCE_SECONDS:
            print("(ignorato: %.2fs di audio — pressione troppo breve o mic muto)" % duration)
            if session is not None:
                session.abort()
            if duration == 0:  # zero frames while collecting = wedged stream: heal it
                threading.Thread(target=self.recorder.reopen, daemon=True).start()
            return

        copy_mode = getattr(self, "_copy_mode", False)

        def work():
            app_name = inject.frontmost_app()  # where the user was at key release
            with self._busy:
                self.status = "trascrivo..."
                try:
                    self._process(clip, duration, app_name, copy_mode, session)
                except Exception as exc:  # never die silently in a worker thread
                    print("⚠️  errore dettatura: %s" % exc)
                finally:
                    self.status = "pronto"

        threading.Thread(target=work, daemon=True).start()

    def _save_debug_clip(self, clip):
        """Ring buffer of the last dictations (local only): real-voice evidence
        for tuning ASR configs when something mis-transcribes."""
        try:
            from .whispercpp import write_wav

            debug_dir = config.CONFIG_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            write_wav(clip, self.cfg.sample_rate, str(debug_dir / ("clip-%d.wav" % int(time.time()))))
            clips = sorted(debug_dir.glob("clip-*.wav"))
            for old in clips[:-5]:
                old.unlink()
        except Exception:
            pass  # debugging aid must never break dictation

    def _process(self, clip, duration, app_name, copy_mode=False, session=None):
        cfg = self.cfg
        started = time.time()
        streamed = False
        if session is not None:
            try:
                texts, lang, _wait = session.finish(clip)
                text = textproc.join_chunks(texts)
                clip = session.full_audio()  # complete audio, for the debug ring
                streamed = True
            except Exception as exc:
                print("⚠️  streaming fallito (%s): ripiego sul metodo classico" % exc)
                clip = session.full_audio()
        if not streamed:
            pieces = audio.split_on_silence(clip, cfg.sample_rate)
            results = [self.transcriber.transcribe(piece) for piece in pieces]
            text = textproc.join_chunks(t for t, _ in results)
            lang = results[0][1]
        if cfg.debug_keep_audio:
            self._save_debug_clip(clip)
        asr_secs = time.time() - started  # in streaming = attesa percepita al rilascio
        text = textproc.tidy(text)
        if not text:
            return
        llm_started = time.time()
        if self.use_llm:
            raw_text = text
            text = formatter.cleanup(text, cfg.ollama_url, cfg.ollama_model, app_name)
            if text != raw_text:
                print("  (grezzo: %s)" % raw_text)
        elif self.ollama_up and cfg.punctuate_enabled and formatter.needs_punctuation(text):
            # Rushed speech with no pauses: rescue the punctuation, words untouchable.
            text = formatter.punctuate(text, cfg.ollama_url, cfg.ollama_model)
        llm_secs = time.time() - llm_started
        text = textproc.apply_dictionary(text, cfg.replacements)
        if copy_mode:  # copy hotkey: clipboard only, paste it wherever you like
            inject.set_clipboard(text)
            _play(SOUND_COPY, cfg.sounds)
            print("[%4.1fs audio | asr %.1fs | %s] (negli appunti) %s" % (duration, asr_secs, lang, text))
            return
        if cfg.paste:
            # Slow processing + user moved on: never paste blind into another app.
            if time.time() - started > 5 and app_name:
                current = inject.frontmost_app()
                if current and current != app_name:
                    inject.set_clipboard(text)
                    _play(SOUND_DONE, cfg.sounds)
                    print("⚠️  finestra cambiata ('%s' → '%s'): testo negli appunti, premi Cmd+V dove serve." % (app_name, current))
                    print("[%4.1fs audio | asr %.1fs + llm %.1fs | %s] %s" % (duration, asr_secs, llm_secs, lang, text))
                    return
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
        from functools import partial

        from pynput import keyboard

        holders = [hotkey.HoldToTalk(hotkey.parse_key(self.cfg.hotkey),
                                     partial(self._on_start, False), self._on_stop)]
        if self.cfg.copy_hotkey and self.cfg.copy_hotkey != self.cfg.hotkey:
            holders.append(hotkey.HoldToTalk(hotkey.parse_key(self.cfg.copy_hotkey),
                                             partial(self._on_start, True), self._on_stop))

        def on_press(key):
            for holder in holders:
                holder._on_press(key)

        def on_release(key):
            for holder in holders:
                holder._on_release(key)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
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
