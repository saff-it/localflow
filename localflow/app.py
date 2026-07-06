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

from . import assistant as assistant_mod
from . import asst_memory, cloud
from . import audio, config, formatter, hotkey, inject, screen, streaming, textproc, websearch
from .transcriber import create_transcriber

# Words that mean "look at my screen" — trigger a screenshot for the vision model.
_SCREEN_TRIGGERS = (
    "schermo", "schermata", "questa pagina", "qui", "questo qui", "guarda", "vedi questo",
    "che vedi", "cosa vedi", "sullo schermo", "questa schermata", "questo errore",
    "questa immagine", "che c'è scritto", "cosa c'è scritto", "aiutami con questo",
    "questo codice", "questa foto", "il video", "questa scheda", "leggi questo",
)


def _notify(title: str, text: str) -> None:
    """macOS notification via osascript — works from launchd, no rumps needed."""
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    try:
        subprocess.Popen(
            ["osascript", "-e", 'display notification "%s" with title "%s"' % (esc(text[:230]), esc(title))],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

SOUND_START = "/System/Library/Sounds/Pop.aiff"
SOUND_DONE = "/System/Library/Sounds/Glass.aiff"
SOUND_COPY = "/System/Library/Sounds/Tink.aiff"
SOUND_WRONG = "/System/Library/Sounds/Basso.aiff"

MIN_UTTERANCE_SECONDS = 0.3  # shorter than this = accidental tap, skip (also kills silence hallucinations)

# "auto" paragraphs run ONLY in real writing apps (positive list): everywhere
# else — browsers, chats, unknown apps — delivery must be instant. A negative
# list once let a 25s reflow fire on every long dictation into Opera.
EXPANSIVE_APPS = {
    "Mail", "Notes", "Note", "Pages", "TextEdit", "Microsoft Word",
    "Obsidian", "Bear", "Craft", "Ulysses", "Notion",
}


_sounds = {}


def _play(path: str, enabled: bool) -> None:
    if not enabled:
        return
    try:  # in-process NSSound: instant, no afplay spawn to wait for
        if path not in _sounds:
            from AppKit import NSSound

            _sounds[path] = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
        _sounds[path].stop()  # rewind if still playing from a rapid previous use
        _sounds[path].play()
    except Exception:
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


def _glossary_prompt(cfg: config.Config, native_translate: bool = False) -> str:
    parts = []
    terms = list(cfg.terms) + list(cfg.replacements.values())
    if terms:
        parts.append("Glossario: " + ", ".join(terms) + ".")
    # Only with Whisper's NATIVE translate the decoder writes English; with
    # LLM translation the ASR still writes the spoken language.
    primer_lang = "en" if native_translate else cfg.language
    primer = PUNCTUATION_PRIMERS.get(primer_lang)
    if primer:
        parts.append(primer)
    return " ".join(parts)


class LocalFlowDaemon:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.status = "avvio..."
        audio.MIN_SPEECH_RMS = cfg.min_speech_rms  # gate threshold, user-tunable
        self.recorder = audio.Recorder(cfg.sample_rate, cfg.device or None, cfg.mic_release_seconds)
        try:
            self.recorder.ensure_open()  # open the mic once, up front (permission prompt included)
        except Exception as exc:
            print("⚠️  impossibile aprire il microfono: %s" % exc)
        self.ollama_up = formatter.available(cfg.ollama_url)
        # Translation strategy: LLM (turbo ASR + qwen, quality) when Ollama is
        # up, Whisper-native (large-v3, literal) otherwise.
        self._llm_translate = cfg.translate_enabled and cfg.translate_quality and self.ollama_up
        native_tr = cfg.translate_enabled and not self._llm_translate
        self.transcriber = create_transcriber(
            cfg, initial_prompt=_glossary_prompt(cfg, native_tr), persistent=True,
            native_translate=native_tr,
        )
        print("ASR engine: %s" % type(self.transcriber).__name__)
        if cfg.translate_enabled:
            print("Traduzione in inglese: %s" % ("LLM (%s)" % cfg.ollama_model if self._llm_translate else "nativa Whisper"))
        self.use_llm = cfg.format_enabled and self.ollama_up
        if self.use_llm:
            print("AI cleanup: on — %s via Ollama" % cfg.ollama_model)
        else:
            print("AI cleanup: off")
        if self.ollama_up and cfg.punctuate_enabled:
            print("Punteggiatura di soccorso: on (solo su dettature lunghe senza segni)")
        # Warm qwen at boot ONLY for features that run inline on dictations
        # (translate/polish/punctuate). Assistant and paragraphs pay their own
        # cold start instead: a resident 4.6GB LLM contends with Whisper for
        # the GPU and made dictation latency jitter 0.7s -> 2-4s.
        if self.ollama_up and (self.use_llm or cfg.punctuate_enabled or self._llm_translate):
            threading.Thread(target=formatter.warmup, args=(cfg.ollama_url, cfg.ollama_model), daemon=True).start()
        self._busy = threading.Lock()
        self._last_paste = 0.0
        self._listener = None
        self._session = None
        self._monitor_stop = threading.Event()
        self._boost = False  # Boost Ultra (Claude) — session-only, never persisted
        self.assistant = assistant_mod.Assistant(
            cfg.ollama_url, cfg.assistant_model or cfg.ollama_model,
            voice=cfg.assistant_voice, rate=cfg.assistant_rate,
            speak_enabled=cfg.assistant_voice_enabled, memory=asst_memory.AssistantMemory())
        if cfg.assistant_enabled:
            print("Assistente: on — tieni premuto '%s', chiedi, rilascia (voce %s)"
                  % (cfg.assistant_key, "on" if cfg.assistant_voice_enabled else "off"))
        if cfg.streaming_enabled and getattr(self.transcriber, "supports_streaming", False):
            print("Streaming: on — trascrivo mentre parli (blocchi da %.0fs)" % cfg.chunk_seconds)
        threading.Thread(target=self._wake_watch, daemon=True).start()
        self.status = "pronto"

    def _wake_watch(self):
        """Keep the engine hot. Two enemies: (1) standby evicts the model from
        RAM (first dictation paid ~7s); (2) Metal releases GPU buffers after
        180s idle (first dictation after a few minutes pays ~1-2s). Cure: warm
        after a wall-clock jump AND heartbeat every ~150s of idle — a 0.5s
        silent inference, ~0.02W average, negligible on battery."""
        last = time.time()
        self._last_engine_use = time.time()
        while True:
            time.sleep(30)
            now = time.time()
            slept = now - last > 120
            idle = now - self._last_engine_use > 150
            if slept or idle:
                threading.Thread(target=self._warm_engine, daemon=True).start()
            last = now

    def _mark_engine_use(self):
        self._last_engine_use = time.time()

    def _warm_engine(self):
        if not self._busy.acquire(False):  # never delay a real dictation
            return
        try:
            import numpy as np

            self.transcriber.transcribe(np.zeros(int(0.5 * self.cfg.sample_rate), dtype=np.float32))
            self._mark_engine_use()
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

    def set_translate_mode(self, mode: str) -> None:
        """SESSION-ONLY by design (never persisted): a special mode silently
        surviving a restart cost the user a confused night — every boot starts
        in plain transcription. Modes: off | en_fast (Whisper native, literal)
        | en_ai / Spanish / French / German (LLM, natural)."""
        self.ollama_up = formatter.available(self.cfg.ollama_url)
        if mode not in ("off", "en_fast") and not self.ollama_up:
            print("⚠️  traduzione AI non disponibile senza Ollama — ripiego")
            return self.set_translate_mode("en_fast" if mode == "en_ai" else "off")
        native_tr = mode == "en_fast"
        self.cfg.translate_enabled = mode != "off"
        self._llm_translate = mode not in ("off", "en_fast")
        self._tr_target = {"en_ai": "English"}.get(mode, mode if self._llm_translate else None)
        if self._llm_translate:
            threading.Thread(target=formatter.warmup,
                             args=(self.cfg.ollama_url, self.cfg.ollama_model), daemon=True).start()
        with self._busy:
            self.status = "cambio modalità..."
            old = self.transcriber
            self.transcriber = create_transcriber(
                self.cfg, initial_prompt=_glossary_prompt(self.cfg, native_tr), persistent=True,
                native_translate=native_tr,
            )
            if hasattr(old, "close"):
                old.close()
            self.status = "pronto"
        print("Traduzione: %s" % ("spenta" if mode == "off" else
                                  "nativa EN" if native_tr else "AI -> %s" % self._tr_target))

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

    def _cancel_hold(self):
        """A modifier shortcut used the assistant key: abort silently. NEVER
        touches a dictation hold — only an in-progress assistant recording."""
        if getattr(self, "_mode", None) != "assistant":
            return
        self._holding = False
        self._cancelled = True
        try:
            self.recorder.stop()
        except Exception:
            pass

    def _on_start(self, mode="paste"):
        # Mutual exclusion: while one key is held, ignore any other key-down.
        # Sharing one recorder across three keys let a stray ⌘-shortcut on the
        # assistant key corrupt an in-flight dictation ("parole a caso").
        if getattr(self, "_holding", False):
            return
        self._mode = mode
        self._copy_mode = mode == "copy"
        self._cancelled = False
        if mode == "assistant":
            self.assistant.stop_speaking()  # re-press = barge-in: shut it up first
        self._hold_seq = getattr(self, "_hold_seq", 0) + 1
        self._holding = True
        hold_id = self._hold_seq

        def still_held():
            return self._holding and self._hold_seq == hold_id

        def go():  # never block the pynput callback thread: a hung CoreAudio
            # cmd_l doubles as ⌘C/⌘V/⌘Tab: a 300ms arming delay means quick
            # shortcuts never reach the mic (they get cancelled first), so no
            # Pop and no recording on every keyboard shortcut.
            if mode == "assistant":
                time.sleep(0.3)
                if not still_held() or getattr(self, "_cancelled", False):
                    return
            # NOTHING else may run before the recorder: any pre-check (AX focus
            # queries can take ~0.75s on Electron apps) delays the mic past
            # the user's first words. The focus check moved to release time.
            if not still_held():
                return  # key already released: NEVER start a zombie recording
            try:   # call here used to kill the hotkey for good
                self.recorder.start()
            except Exception as exc:
                print("⚠️  microfono non disponibile: %s" % exc)
                return
            if not still_held():  # released while the mic was waking up: discard
                self.recorder.stop()
                return
            # Pop only when the mic is REALLY listening: if the device needed
            # reopening after a long break, the sound arrives late but honest —
            # no syllables spoken into a mic that wasn't there yet.
            _play(SOUND_START, self.cfg.sounds)
            if mode == "paste":
                # Early "not-ready-to-dictate" cue: if the focus isn't a text
                # field, a deny sound follows the Pop (async, never delays the
                # mic). We still record → the text is saved to the clipboard.
                def deny_if_not_editable():
                    if still_held() and not inject.focused_is_editable():
                        _play(SOUND_WRONG, self.cfg.sounds)
                threading.Thread(target=deny_if_not_editable, daemon=True).start()
            if mode in ("paste", "copy") and self._use_streaming():
                self._monitor_stop.set()  # belt: no stray monitor may survive
                self._monitor_stop = threading.Event()
                chunk_s = max(self.cfg.chunk_seconds, 10.0) if self._llm_translate else self.cfg.chunk_seconds
                session = streaming.StreamingSession(
                    self.transcriber, self.cfg.sample_rate, chunk_s,
                    base_prompt=_glossary_prompt(self.cfg),
                    post_process=self._translate_chunk if self._llm_translate else None,
                )
                if not still_held():  # paranoia pays: released during setup
                    session.abort()
                    self.recorder.stop()
                    return
                self._session = session
                threading.Thread(target=self._monitor,
                                 args=(session, self._monitor_stop), daemon=True).start()

        threading.Thread(target=go, daemon=True).start()

    def _on_stop(self):
        self._holding = False  # any late go() for this hold now aborts itself
        if getattr(self, "_cancelled", False):  # shortcut used the assistant key
            self._cancelled = False
            if self._session is not None:
                self._session.abort()
                self._session = None
            return
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

        mode = getattr(self, "_mode", "paste")
        copy_mode = mode == "copy"

        if mode == "assistant":
            def ask():
                with self._busy:
                    self.status = "penso..."
                    try:
                        self._assistant_process(clip, duration)
                    except Exception as exc:
                        print("⚠️  errore assistente: %s" % exc)
                    finally:
                        self.status = "pronto"
            threading.Thread(target=ask, daemon=True).start()
            return

        def work():
            app_name = inject.frontmost_app()  # where the user was at key release
            # Focus check at RELEASE, in parallel with transcription (its
            # ~0.1-0.75s is free here): pasting into a non-field becomes
            # clipboard + warning sound instead of a blind ⌘V.
            editable = True if copy_mode else inject.focused_is_editable()
            with self._busy:
                self.status = "trascrivo..."
                try:
                    self._process(clip, duration, app_name, copy_mode, session, editable)
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

    def _assistant_process(self, clip, duration):
        cfg = self.cfg
        if not audio.has_speech(clip, cfg.sample_rate):
            print("(assistente: nessuna domanda rilevata)")
            return
        pieces = audio.split_on_silence(clip, cfg.sample_rate)
        question = textproc.tidy(textproc.join_chunks(self.transcriber.transcribe(p)[0] for p in pieces))
        self._mark_engine_use()
        if not question:
            return
        print("🎙️  %s" % question)
        # BOOST ULTRA (Claude): deliberate, session-only. Handles text, vision
        # and live web search in one premium call — on the user's dime.
        if getattr(self, "_boost", False) and cloud.available():
            image_b64 = None
            if cfg.assistant_vision and any(t in question.lower() for t in _SCREEN_TRIGGERS):
                shot = screen.capture()
                if shot is not None:
                    image_b64 = screen.as_base64(shot)
                    print("🚀👁️  Boost guarda lo schermo")
            _notify("🚀 " + question[:60], "Boost (Claude)…")
            answer = cloud.ask(question, model=cfg.assistant_cloud_model or cloud.DEFAULT_MODEL,
                               image_b64=image_b64, web_search=True)
            self.assistant.history += [{"role": "user", "content": question},
                                       {"role": "assistant", "content": answer}]
            if self.assistant.memory:
                self.assistant.memory.append("user", question + " [Boost]")
                self.assistant.memory.append("assistant", answer)
            inject.set_clipboard(answer)
            _notify("🚀 Boost", answer)
            print("🚀 %s" % answer)
            if cfg.assistant_voice_enabled:
                self.assistant.speak(answer)
            return
        # Needs fresh info from the web? Free DuckDuckGo search + local synthesis.
        if cfg.assistant_internet and websearch.wants_search(question):
            _notify("🌐 " + question[:60], "cerco online…")
            print("🌐 cerco online…")
            gen = self.assistant.begin_speech()  # stream synthesis → early voice
            web_answer = websearch.answer_with_search(
                question, cfg.ollama_url, cfg.assistant_search_model or cfg.ollama_model,
                on_sentence=lambda s: self.assistant.feed_speech(gen, s))
            if web_answer:
                self.assistant.history.append({"role": "user", "content": question})
                self.assistant.history.append({"role": "assistant", "content": web_answer})
                if self.assistant.memory:
                    self.assistant.memory.append("user", question + " [ricerca web]")
                    self.assistant.memory.append("assistant", web_answer)
                inject.set_clipboard(web_answer)
                _notify("🌐 Assistente", web_answer)
                print("🌐 %s" % web_answer)
                return
            print("(ricerca web fallita, rispondo in locale)")
        # Does the question refer to the screen? If so, grab it for the vision model.
        image_b64, vmodel = None, None
        ql = question.lower()
        if cfg.assistant_vision and any(t in ql for t in _SCREEN_TRIGGERS):
            shot = screen.capture()
            if shot is not None:
                image_b64 = screen.as_base64(shot)
                vmodel = cfg.assistant_vision_model
                print("👁️  guardo lo schermo (%s)" % vmodel)
        _notify("💬 " + question[:60], "guardo lo schermo…" if image_b64 else "sto pensando…")
        # Streamed: the first sentence is SPOKEN while the rest still generates.
        answer = self.assistant.ask_and_speak(question, image_b64=image_b64, model=vmodel)
        # Safety net: the local model refused for lack of live data → go to the web.
        if image_b64 is None and cfg.assistant_internet and websearch.is_refusal(answer):
            print("↻ risposta locale insufficiente → cerco online")
            _notify("🌐 " + question[:60], "cerco online…")
            gen = self.assistant.begin_speech()  # cut the local refusal voice, speak the real answer
            web = websearch.answer_with_search(
                question, cfg.ollama_url, cfg.assistant_search_model or cfg.ollama_model,
                on_sentence=lambda s: self.assistant.feed_speech(gen, s))
            if web:
                answer = web
                self.assistant.history[-1] = {"role": "assistant", "content": web}
                if self.assistant.memory:
                    self.assistant.memory.append("assistant", "[corretto via web] " + web)
        inject.set_clipboard(answer)  # full answer on the clipboard, to paste if wanted
        _notify("🤖 Assistente", answer)  # primary channel: read it
        print("🤖 %s" % answer)
        if answer.startswith("Scusa, non sono riuscito"):
            _play(SOUND_WRONG, cfg.sounds)

    def set_assistant_voice(self, enabled: bool) -> None:
        config.set_key("voice_enabled", "true" if enabled else "false")
        self.cfg.assistant_voice_enabled = enabled
        self.assistant.speak_enabled = enabled
        if not enabled:
            self.assistant.stop_speaking()

    def new_conversation(self) -> None:
        self.assistant.reset()
        print("(assistente: nuova conversazione)")

    def set_boost(self, enabled: bool) -> bool:
        """Boost Ultra = route to Claude. Session-only (never persisted): it
        costs money, so it must never silently survive a restart. Returns the
        effective state (False if enabling failed for lack of an API key)."""
        if enabled and not cloud.available():
            _notify("🚀 Boost non attivo", "Manca la chiave API di Claude (menu → Boost → Imposta chiave).")
            self._boost = False
            return False
        self._boost = enabled
        print("Boost Ultra: %s" % ("ON (Claude)" if enabled else "off (locale)"))
        return enabled

    def _maybe_paragraphs(self, text, app_name):
        """Reflow into paragraphs/bullets when the mode and app call for it."""
        mode = self.cfg.paragraphs
        if mode == "never" or not self.ollama_up or not formatter.needs_paragraphs(text):
            return text
        if mode == "auto" and app_name not in EXPANSIVE_APPS:
            return text  # only true writing apps pay the reflow cost
        return formatter.format_paragraphs(text, self.cfg.ollama_url, self.cfg.ollama_model)

    def _translate_chunk(self, text, done_texts):
        """Per-chunk LLM translation, overlapped with the user's speech."""
        context = " ".join(done_texts)[-200:].strip()
        return formatter.translate_text(text, self.cfg.ollama_url, self.cfg.ollama_model,
                                        target=getattr(self, "_tr_target", None) or "English",
                                        context=context)

    def _process(self, clip, duration, app_name, copy_mode=False, session=None, editable=True):
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
            if cfg.debug_keep_audio:
                self._save_debug_clip(clip)  # save BEFORE ASR: audio never lost
            if not audio.has_speech(clip, cfg.sample_rate):
                print("(ignorato: nessuna voce rilevata)")
                return
            try:
                pieces = audio.split_on_silence(clip, cfg.sample_rate)
                results = [self.transcriber.transcribe(piece) for piece in pieces]
            except Exception as exc:
                # Dead engine (crashed/restarted whisper-server): relaunch it and retry
                # once — a dictation must survive even an engine death.
                print("⚠️  motore non risponde (%s): lo rilancio e riprovo" % type(exc).__name__)
                old = self.transcriber
                self.transcriber = create_transcriber(cfg, initial_prompt=_glossary_prompt(cfg), persistent=True)
                if hasattr(old, "close"):
                    try:
                        old.close()
                    except Exception:
                        pass
                pieces = audio.split_on_silence(clip, cfg.sample_rate)
                results = [self.transcriber.transcribe(piece) for piece in pieces]
            text = textproc.join_chunks(t for t, _ in results)
            lang = results[0][1]
        elif cfg.debug_keep_audio:
            self._save_debug_clip(clip)
        if self._llm_translate and not streamed and text:
            # Short/batch dictations: translate the whole text in one shot
            # (streamed chunks were already translated on the fly).
            text = formatter.translate_text(text, cfg.ollama_url, cfg.ollama_model,
                                            target=getattr(self, "_tr_target", None) or "English")
        asr_secs = time.time() - started  # in streaming = attesa percepita al rilascio
        self._mark_engine_use()
        text = textproc.tidy(text)
        if not text:
            print("(ignorato: nessuna voce rilevata)")
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
        fmt_started = time.time()  # reflow cost must NEVER be invisible in the log
        text = self._maybe_paragraphs(text, app_name)
        llm_secs += time.time() - fmt_started
        if copy_mode:  # copy hotkey: clipboard only, paste it wherever you like
            inject.set_clipboard(text)
            _play(SOUND_COPY, cfg.sounds)
            print("[%4.1fs audio | asr %.1fs | %s] (negli appunti) %s" % (duration, asr_secs, lang, text))
            return
        if not editable:  # paste key used outside a text field: warn, keep the text safe
            inject.set_clipboard(text)
            _play(SOUND_WRONG, cfg.sounds)
            print("[%4.1fs audio | asr %.1fs | %s] (non eri in un campo di testo: negli appunti) %s" % (duration, asr_secs, lang, text))
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

        # cmd_r: plain hold = dictate & paste; tap-then-hold = copy.
        dict_key = hotkey.parse_key(self.cfg.hotkey)
        dict_gesture = hotkey.TapThenHold(
            dict_key,
            lambda g: self._on_start("copy" if g == "armed" else "paste"),
            self._on_stop,
        )

        # Assistant on its own key, with combo-cancel: another key pressed while
        # it's held = a shortcut, not a question.
        asst_key = None
        if self.cfg.assistant_enabled and self.cfg.assistant_key:
            asst_key = hotkey.parse_key(self.cfg.assistant_key)
        asst_holder = (hotkey.HoldToTalk(asst_key, partial(self._on_start, "assistant"), self._on_stop)
                       if asst_key is not None else None)
        astate = {"down": False}

        def on_press(key):
            if key == dict_key:
                dict_gesture.press()
            if asst_holder is not None:
                if key == asst_key:
                    astate["down"] = True
                elif astate["down"] and getattr(self, "_mode", None) == "assistant":
                    self._cancel_hold()  # only cancels an assistant hold, never dictation
                asst_holder._on_press(key)

        def on_release(key):
            if key == dict_key:
                dict_gesture.release()
            if asst_holder is not None:
                if key == asst_key:
                    astate["down"] = False
                asst_holder._on_release(key)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def set_assistant_key(self, key_name: str) -> None:
        config.set_key("assistant_key", '"%s"' % key_name)
        self.cfg.assistant_key = key_name
        self.cfg.assistant_enabled = bool(key_name)
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.start_listener()

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
