"""Menu-bar UI (rumps): run LocalFlow without a terminal window.

The heavy engine loads in a background thread so the icon appears instantly;
a 1s timer mirrors the daemon status into the menu.
"""
import os
import pathlib
import subprocess
import threading

import rumps

from . import config
from .app import LocalFlowDaemon

LANGUAGES = [
    ("Italiano", "it"),
    ("Auto (rileva la lingua)", ""),
    ("Español", "es"),
    ("English", "en"),
]
MODELS = [
    ("Massima precisione (~4-5s)", "large-v3-q5_0"),
    ("Veloce (~1-2s)", "large-v3-turbo-q8_0"),
]
HOTKEYS = [
    ("⌘ Command destro", "cmd_r"),
    ("⌥ Option destro", "alt_r"),
    ("⌃ Control destro", "ctrl_r"),
    ("F13", "f13"),
]
COPY_HOTKEYS = [("Disattivato", "")] + HOTKEYS
PLIST = pathlib.Path.home() / "Library" / "LaunchAgents" / "com.localflow.plist"


class LocalFlowMenuApp(rumps.App):
    def __init__(self):
        super().__init__("LocalFlow", title="🎤", quit_button=rumps.MenuItem("Esci"))
        self.daemon = None
        self.status_item = rumps.MenuItem("Stato: carico il modello...")
        self.status_item.set_callback(None)
        self.lang_items = {}
        lang_menu = rumps.MenuItem("Lingua")
        cfg = config.load()
        for label, code in LANGUAGES:
            item = rumps.MenuItem(label, callback=self._make_lang_cb(code))
            item.state = 1 if cfg.language == code else 0
            self.lang_items[code] = item
            lang_menu.add(item)
        self.model_items = {}
        model_menu = rumps.MenuItem("Precisione")
        for label, name in MODELS:
            item = rumps.MenuItem(label, callback=self._make_model_cb(name))
            item.state = 1 if cfg.whispercpp_model == name else 0
            self.model_items[name] = item
            model_menu.add(item)
        self.model_menu = model_menu
        self.hotkey_items = {}
        hotkey_menu = rumps.MenuItem("Tasto di dettatura")
        for label, code in HOTKEYS:
            item = rumps.MenuItem(label, callback=self._make_hotkey_cb(code))
            item.state = 1 if cfg.hotkey == code else 0
            self.hotkey_items[code] = item
            hotkey_menu.add(item)
        self.hotkey_menu = hotkey_menu
        self.copykey_items = {}
        copykey_menu = rumps.MenuItem("Tasto di copia (solo appunti)")
        for label, code in COPY_HOTKEYS:
            item = rumps.MenuItem(label, callback=self._make_copykey_cb(code))
            item.state = 1 if cfg.copy_hotkey == code else 0
            self.copykey_items[code] = item
            copykey_menu.add(item)
        self.copykey_menu = copykey_menu
        self.pause_item = rumps.MenuItem("⏻ Spegni microfono", callback=self._toggle_pause)
        self.translate_item = rumps.MenuItem("Traduci in inglese (parli IT, esce EN)", callback=self._toggle_translate)
        self.translate_item.state = 1 if cfg.translate_enabled else 0
        self.stream_item = rumps.MenuItem("Streaming (trascrive mentre parli)", callback=self._toggle_streaming)
        self.stream_item.state = 1 if cfg.streaming_enabled else 0
        self.polish_item = rumps.MenuItem("Polish AI (LLM)", callback=self._toggle_polish)
        self.polish_item.state = 1 if cfg.format_enabled else 0
        self.login_item = rumps.MenuItem("Avvia al login", callback=self._toggle_login)
        self.login_item.state = 1 if self._login_enabled() else 0
        open_cfg = rumps.MenuItem("Apri configurazione", callback=self._open_config)
        recent = rumps.MenuItem("Ultime dettature", callback=self._show_recent)
        restart = rumps.MenuItem("Riavvia LocalFlow", callback=self._restart)
        self.menu = [self.status_item, self.pause_item, None, lang_menu, self.model_menu, self.hotkey_menu,
                     self.copykey_menu, self.translate_item, self.stream_item, self.polish_item, None, recent, None,
                     self.login_item, open_cfg, restart, None]
        threading.Thread(target=self._boot, daemon=True).start()
        rumps.Timer(self._refresh_status, 1).start()

    def _boot(self):
        try:
            self.daemon = LocalFlowDaemon(config.load())
            self.daemon.start_listener()
        except Exception as exc:
            rumps.notification("LocalFlow", "Errore di avvio", str(exc))

    SYMBOLS = {"pronto": "mic", "trascrivo...": "waveform", "in pausa": "mic.slash"}

    def _refresh_status(self, _timer):
        if not getattr(self, "_dock_hidden", False):
            # Hide the Dock/Cmd-Tab "Python" entry. Must happen AFTER the app is
            # fully launched (doing it at boot made rumps exit silently).
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().setActivationPolicy_(1)  # accessory
                self._dock_hidden = True
            except Exception:
                self._dock_hidden = True  # don't retry forever
        if self.daemon is None:
            return
        state = self.daemon.status
        self._set_symbol(self.SYMBOLS.get(state, "hourglass"), state)
        self.status_item.title = "Stato: %s  (tieni premuto ⌥ destro)" % state

    def _set_symbol(self, name, state):
        """Native SF Symbols in the status item — same style as the system icons.
        Falls back to emoji title if the AppKit internals ever change."""
        if getattr(self, "_current_symbol", None) == name:
            return
        try:
            from AppKit import NSImage

            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "LocalFlow")
            if image is None:
                raise ValueError("unknown SF Symbol: " + name)
            image.setTemplate_(True)
            button = self._nsapp.nsstatusitem.button()
            button.setImage_(image)
            button.setTitle_("")
            self._current_symbol = name
        except Exception:
            fallback = {"pronto": "🎤", "trascrivo...": "✍️", "in pausa": "💤"}
            self.title = fallback.get(state, "⏳")

    def _make_lang_cb(self, code):
        def cb(_item):
            if self.daemon is None:
                return
            for c, item in self.lang_items.items():
                item.state = 1 if c == code else 0
            threading.Thread(target=self.daemon.set_language, args=(code,), daemon=True).start()
        return cb

    def _make_hotkey_cb(self, code):
        def cb(_item):
            if self.daemon is None:
                return
            for c, item in self.hotkey_items.items():
                item.state = 1 if c == code else 0
            threading.Thread(target=self.daemon.set_hotkey, args=(code,), daemon=True).start()
        return cb

    def _make_copykey_cb(self, code):
        def cb(_item):
            if self.daemon is None:
                return
            for c, item in self.copykey_items.items():
                item.state = 1 if c == code else 0
            threading.Thread(target=self.daemon.set_copy_hotkey, args=(code,), daemon=True).start()
        return cb

    def _make_model_cb(self, name):
        def cb(_item):
            if self.daemon is None:
                return
            for n, item in self.model_items.items():
                item.state = 1 if n == name else 0
            threading.Thread(target=self.daemon.set_model, args=(name,), daemon=True).start()
        return cb

    def _toggle_pause(self, item):
        if self.daemon is None:
            return
        # Never run pause/resume on the UI thread: a wedged CoreAudio call in
        # there froze the whole menu bar once. Background thread, always.
        if self.daemon.status == "in pausa":
            item.title = "⏻ Spegni microfono"
            threading.Thread(target=self.daemon.resume, daemon=True).start()
        else:
            item.title = "⏻ Accendi microfono"
            threading.Thread(target=self.daemon.pause, daemon=True).start()

    def _toggle_translate(self, item):
        if self.daemon is None:
            return
        item.state = 0 if item.state else 1
        threading.Thread(target=self.daemon.set_translate, args=(bool(item.state),), daemon=True).start()

    def _toggle_streaming(self, item):
        if self.daemon is None:
            return
        item.state = 0 if item.state else 1
        self.daemon.set_streaming(bool(item.state))

    def _toggle_polish(self, item):
        if self.daemon is None:
            return
        item.state = 0 if item.state else 1
        self.daemon.set_polish(bool(item.state))
        if item.state and not self.daemon.use_llm:
            rumps.notification("LocalFlow", "Polish non attivo",
                               "Ollama non raggiungibile: avvialo con 'brew services start ollama'.")

    def _login_enabled(self):
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        return "com.localflow" in result.stdout

    def _toggle_login(self, item):
        if not PLIST.exists():
            rumps.notification("LocalFlow", "File mancante", "Non trovo %s" % PLIST)
            return
        if item.state:
            subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
            item.state = 0
        else:
            subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True)
            item.state = 1
            rumps.notification(
                "LocalFlow", "Avvio al login attivato",
                "Se questa istanza l'avevi lanciata a mano, chiudila (Esci) per evitare doppioni.",
            )

    def _open_config(self, _item):
        subprocess.run(["open", str(config.CONFIG_PATH)])

    def _show_recent(self, _item):
        try:
            log = pathlib.Path.home() / ".localflow" / "localflow.log"
            texts = [line.split("] ", 1)[1].strip()
                     for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.startswith("[") and "] " in line]
            rumps.alert("Ultime dettature", "\n\n".join(texts[-5:]) or "Nessuna dettatura nel log.")
        except Exception as exc:
            rumps.alert("LocalFlow", "Impossibile leggere il log: %s" % exc)

    def _restart(self, _item):
        # launchd receives the request and restarts us even though we die mid-call
        subprocess.Popen(["launchctl", "kickstart", "-k", "gui/%d/com.localflow" % os.getuid()])


def main():
    LocalFlowMenuApp().run()
