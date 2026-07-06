"""Global hold-to-talk hotkey via pynput.

Requires Input Monitoring (and Accessibility) permission for the process running
LocalFlow. macOS auto-repeats key-down events while a key is held, hence the guard.
"""
from pynput import keyboard


# macOS quirk: pynput reports LEFT modifiers as the GENERIC key (Key.cmd,
# Key.alt, Key.ctrl) — only the right-hand ones carry the _r suffix. A holder
# waiting for Key.cmd_l would never fire.
_DARWIN_LEFT = {"cmd_l": "cmd", "alt_l": "alt", "ctrl_l": "ctrl", "shift_l": "shift"}


def parse_key(name: str):
    name = name.strip().lower()
    name = _DARWIN_LEFT.get(name, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        raise ValueError(
            "Unknown hotkey '%s'. Use e.g. alt_r, alt_l, cmd_r, ctrl_r, f13, or a single character." % name
        )


class TapThenHold:
    """One key, two gestures. A plain hold fires on_start("primary"); a quick
    TAP (held < tap_max, typically no speech) immediately followed by a hold
    (within window) fires on_start("armed"). A normal dictation hold is long,
    so it never arms — single-hold stays zero-latency. Clock injectable for tests.
    """

    def __init__(self, key, on_start, on_stop, tap_max=0.35, window=0.45, clock=None):
        import time as _time

        self.key = key
        self.on_start = on_start
        self.on_stop = on_stop
        self.tap_max = tap_max
        self.window = window
        self._clock = clock or _time.monotonic
        self._down = False
        self._press_t = 0.0
        self._last_release = -1e9
        self._last_short = False

    def press(self) -> None:
        if self._down:  # macOS auto-repeat while held
            return
        self._down = True
        now = self._clock()
        self._press_t = now
        armed = (now - self._last_release < self.window) and self._last_short
        self.on_start("armed" if armed else "primary")

    def release(self) -> None:
        if not self._down:
            return
        self._down = False
        now = self._clock()
        self._last_short = (now - self._press_t) < self.tap_max
        self._last_release = now
        self.on_stop()


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
