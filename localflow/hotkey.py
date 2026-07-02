"""Global hold-to-talk hotkey via pynput.

Requires Input Monitoring (and Accessibility) permission for the process running
LocalFlow. macOS auto-repeats key-down events while a key is held, hence the guard.
"""
from pynput import keyboard


def parse_key(name: str):
    name = name.strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        raise ValueError(
            "Unknown hotkey '%s'. Use e.g. alt_r, alt_l, cmd_r, ctrl_r, f13, or a single character." % name
        )


class HoldToTalk:
    """on_start fires when the key goes down, on_stop when it comes back up."""

    def __init__(self, key, on_start, on_stop):
        self.key = key
        self.on_start = on_start
        self.on_stop = on_stop
        self._held = False

    def _on_press(self, key):
        if key == self.key and not self._held:
            self._held = True
            self.on_start()

    def _on_release(self, key):
        if key == self.key and self._held:
            self._held = False
            self.on_stop()

    def run_forever(self):
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            listener.join()
