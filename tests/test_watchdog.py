import unittest

from localflow import watchdog


class ElapsedTests(unittest.TestCase):
    def test_none_state_is_zero(self):
        # No work in flight -> the daemon is idle, never "stuck".
        self.assertEqual(watchdog._elapsed(None, 1000.0), 0.0)

    def test_positive_elapsed(self):
        self.assertEqual(watchdog._elapsed((1000.0, "trascrivo"), 1005.0), 5.0)

    def test_clock_going_backwards_never_negative(self):
        # monotonic shouldn't go back, but a negative age must never look "idle".
        self.assertEqual(watchdog._elapsed((1000.0, "x"), 999.0), 0.0)


class CheckTests(unittest.TestCase):
    # dump_after=60, exit_after=240 mirror the installed defaults.
    def check(self, elapsed, dumped):
        return watchdog._check(elapsed, dump_after=60.0, exit_after=240.0, already_dumped=dumped)

    def test_idle_is_noop_and_resets_dump_flag(self):
        # A completed dictation (elapsed back to 0) re-arms the one-shot dump.
        self.assertEqual(self.check(0.0, True), (None, False))

    def test_below_dump_threshold_is_noop(self):
        self.assertEqual(self.check(30.0, False), (None, False))

    def test_crossing_dump_threshold_dumps_once(self):
        action, dumped = self.check(75.0, False)
        self.assertEqual(action, "dump")
        self.assertTrue(dumped)

    def test_does_not_dump_twice_in_one_episode(self):
        # Already dumped this episode: stay quiet until the exit threshold.
        self.assertEqual(self.check(120.0, True), (None, True))

    def test_crossing_exit_threshold_exits(self):
        action, _ = self.check(300.0, True)
        self.assertEqual(action, "exit")

    def test_exit_fires_even_if_never_dumped(self):
        # A jump straight past exit_after (slow poll) must still exit.
        action, _ = self.check(300.0, False)
        self.assertEqual(action, "exit")


class LoopStepTests(unittest.TestCase):
    def test_full_episode_sequence(self):
        # Drive one stuck episode through the pure step and record the actions.
        state = {"work": None}
        actions = []
        dumped = False
        for elapsed in (0.0, 30.0, 75.0, 120.0, 300.0):
            action, dumped = watchdog._check(elapsed, 60.0, 240.0, dumped)
            if action:
                actions.append(action)
        self.assertEqual(actions, ["dump", "exit"])


if __name__ == "__main__":
    unittest.main()
