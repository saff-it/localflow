import unittest

from localflow.hotkey import TapThenHold


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TapThenHoldTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.events = []
        self.g = TapThenHold(
            "k",
            lambda mode: self.events.append(("start", mode)),
            lambda: self.events.append(("stop",)),
            tap_max=0.35, window=0.45, clock=self.clock,
        )

    def hold(self, seconds):
        self.g.press()
        self.clock.advance(seconds)
        self.g.release()

    def test_plain_hold_is_primary(self):
        self.hold(1.0)  # normal dictation: long hold
        self.assertEqual(self.events[0], ("start", "primary"))

    def test_tap_then_hold_is_armed(self):
        self.hold(0.1)                 # quick arming tap
        self.clock.advance(0.2)        # within window
        self.g.press()                 # the copy hold
        self.assertEqual(self.events[-1], ("start", "armed"))

    def test_long_dictations_never_arm(self):
        self.hold(0.8)                 # real dictation (long)
        self.clock.advance(0.1)
        self.g.press()                 # immediate next hold
        self.assertEqual(self.events[-1], ("start", "primary"))  # not armed

    def test_tap_then_late_hold_is_primary(self):
        self.hold(0.1)                 # quick tap
        self.clock.advance(0.6)        # BEYOND window
        self.g.press()
        self.assertEqual(self.events[-1], ("start", "primary"))

    def test_autorepeat_press_ignored(self):
        self.g.press()
        self.g.press()  # macOS auto-repeat while held
        starts = [e for e in self.events if e[0] == "start"]
        self.assertEqual(len(starts), 1)


if __name__ == "__main__":
    unittest.main()
