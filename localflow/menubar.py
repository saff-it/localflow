"""Menu-bar UI (rumps): run LocalFlow without a terminal window.

The heavy engine loads in a background thread so the icon appears instantly;
a 1s timer mirrors the daemon status into the menu.
"""
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
        self.pause_item = rumps.MenuItem("🔴 Spegni microfono", callback=self._toggle_pause)
        self.polish_item = rumps.MenuItem("Polish AI (LLM)", callback=self._toggle_polish)
        self.polish_item.state = 1 if cfg.format_enabled else 0
        self.login_item = rumps.MenuItem("Avvia al login", callback=self._toggle_login)
        self.login_item.state = 1 if self._login_enabled() else 0
        open_cfg = rumps.MenuItem("Apri configurazione", callback=self._open_config)
        self.menu = [self.status_item, self.pause_item, None, lang_menu, self.polish_item, None, self.login_item, open_cfg, None]
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

    def _toggle_pause(self, item):
        if self.daemon is None:
            return
        if self.daemon.status == "in pausa":
            self.daemon.resume()
            item.title = "🔴 Spegni microfono"
        else:
            self.daemon.pause()
            item.title = "🟢 Accendi microfono"

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


def main():
    LocalFlowMenuApp().run()
