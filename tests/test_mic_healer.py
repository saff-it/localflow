import subprocess
import unittest

from localflow import mic_healer


class NextRemedyTests(unittest.TestCase):
    def test_no_failures_is_noop(self):
        self.assertIsNone(mic_healer.next_remedy(0))

    def test_first_failure_reopens(self):
        self.assertEqual(mic_healer.next_remedy(1), "reopen")

    def test_second_failure_still_reopens(self):
        self.assertEqual(mic_healer.next_remedy(2), "reopen")

    def test_third_failure_restarts_coreaudiod(self):
        self.assertEqual(mic_healer.next_remedy(3), "coreaudiod")

    def test_fourth_failure_falls_back_to_reopen(self):
        # coreaudiod just restarted; give the cheap remedy one more chance
        # before declaring the whole process unsalvageable.
        self.assertEqual(mic_healer.next_remedy(4), "reopen")

    def test_fifth_failure_restarts_app(self):
        self.assertEqual(mic_healer.next_remedy(5), "restart")

    def test_restart_is_terminal(self):
        # Past the threshold we must not drop back to a milder remedy that has
        # already been proven useless in this episode.
        self.assertEqual(mic_healer.next_remedy(9), "restart")
        self.assertEqual(mic_healer.next_remedy(50), "restart")


class RestartCoreaudiodTests(unittest.TestCase):
    def test_uses_sudo_non_interactive(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0)

        self.assertTrue(mic_healer.restart_coreaudiod(run=fake_run))
        # -n is what keeps a headless healing thread from hanging on a prompt.
        self.assertIn("-n", seen["cmd"])
        self.assertIn("coreaudiod", seen["cmd"])

    def test_missing_sudoers_rule_reports_false(self):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1)

        self.assertFalse(mic_healer.restart_coreaudiod(run=fake_run))

    def test_timeout_reports_false_instead_of_raising(self):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        self.assertFalse(mic_healer.restart_coreaudiod(run=fake_run))

    def test_missing_binary_reports_false(self):
        def fake_run(cmd, **kwargs):
            raise OSError("no such file")

        self.assertFalse(mic_healer.restart_coreaudiod(run=fake_run))


class HealerFake:
    """Builds a MicHealer whose every effect is recorded instead of performed."""

    def __init__(self, audio_ok=True, reopen_raises=False,
                 probe=None, probe_raises=False):
        self.reopened = 0
        self.restarted = 0
        self.audio_restarts = 0
        self.notices = []
        self.slept = []
        self.calls = []          # order of effects, for the reopen-then-probe rule
        self.probes = 0
        self._audio_ok = audio_ok
        self._reopen_raises = reopen_raises
        self._probe_answer = probe
        self._probe_raises = probe_raises
        self.healer = mic_healer.MicHealer(
            reopen=self._reopen,
            restart=self._restart,
            notify=self.notices.append,
            restart_audio=self._restart_audio,
            sleep=self.slept.append,
            probe=None if probe is None and not probe_raises else self._probe,
        )

    def _probe(self):
        self.probes += 1
        self.calls.append("probe")
        if self._probe_raises:
            raise RuntimeError("stream busy")
        return self._probe_answer

    def _reopen(self):
        self.reopened += 1
        self.calls.append("reopen")
        if self._reopen_raises:
            raise RuntimeError("stream refused to open")

    def _restart(self):
        self.restarted += 1

    def _restart_audio(self):
        self.audio_restarts += 1
        return self._audio_ok


class MicHealerTests(unittest.TestCase):
    def test_single_empty_only_reopens(self):
        f = HealerFake()
        self.assertEqual(f.healer.on_empty(), "reopen")
        self.assertEqual(f.reopened, 1)
        self.assertEqual(f.audio_restarts, 0)
        self.assertEqual(f.restarted, 0)

    def test_success_resets_the_episode(self):
        # The real-world guard: occasional stray taps must never accumulate
        # across days into a system audio restart.
        f = HealerFake()
        f.healer.on_empty()
        f.healer.on_empty()
        f.healer.on_success()
        self.assertEqual(f.healer.consecutive_failures, 0)
        self.assertEqual(f.healer.on_empty(), "reopen")
        self.assertEqual(f.audio_restarts, 0)

    def test_three_in_a_row_restarts_coreaudiod_then_the_process(self):
        # Regression, observed live on 2026-08-16: reopening the stream after
        # coreaudiod died leaves PortAudio poisoned (-9986) and the mic dead
        # until the process is replaced. So the remedy must restart, not reopen.
        f = HealerFake()
        f.healer.on_empty()
        f.healer.on_empty()
        self.assertEqual(f.healer.on_empty(), "coreaudiod")
        self.assertEqual(f.audio_restarts, 1)
        self.assertEqual(f.slept, [mic_healer.COREAUDIOD_SETTLE_SECONDS])
        self.assertEqual(f.restarted, 1)
        self.assertEqual(f.reopened, 2)  # only the two mild ones, none after

    def test_user_is_warned_before_the_audio_blip(self):
        f = HealerFake()
        for _ in range(3):
            f.healer.on_empty()
        self.assertTrue(any("riavvio l'audio" in n for n in f.notices))

    def test_missing_permission_is_reported_not_silent(self):
        f = HealerFake(audio_ok=False)
        for _ in range(3):
            f.healer.on_empty()
        self.assertTrue(any("sudoers" in n for n in f.notices))
        self.assertEqual(f.slept, [])  # nothing to settle: it never restarted

    def test_restart_never_blips_system_audio_twice(self):
        # In production the process dies at failure 3, so 4 and 5 only happen if
        # the restart itself failed. Then we must retry the *process*, never the
        # audio daemon again: a second blip costs the user and fixes nothing.
        #
        # Failure 4 used to fall back to a reopen. It no longer does, and the
        # old expectation was the mistake: once coreaudiod has been replaced
        # under a live PortAudio, reopening this process' stream is the one
        # remedy known to be impossible (-9986, seen on 2026-08-16). Retrying
        # the process every time is the only move left that can work.
        f = HealerFake()
        for _ in range(5):
            f.healer.on_empty()
        self.assertEqual(f.audio_restarts, 1)
        self.assertEqual(f.reopened, 2)   # the two before the daemon swap, none after
        self.assertEqual(f.restarted, 3)  # step 3, then every failure after it

    def test_failing_reopen_does_not_break_escalation(self):
        # If reopen throws, the healing thread must survive to escalate.
        f = HealerFake(reopen_raises=True)
        f.healer.on_empty()
        f.healer.on_empty()
        self.assertEqual(f.healer.on_empty(), "coreaudiod")
        self.assertEqual(f.audio_restarts, 1)


class RecorderFake:
    def __init__(self, frames=6000, start_raises=False):
        self.started = 0
        self.stopped = 0
        self._frames = frames
        self._start_raises = start_raises

    def start(self):
        self.started += 1
        if self._start_raises:
            raise RuntimeError("device unavailable")

    def stop(self):
        self.stopped += 1
        return [0.0] * self._frames


class ProbeCaptureTests(unittest.TestCase):
    """The probe borrows the user's one recorder, so it must be a polite guest.

    Every rule here exists to stop the probe from manufacturing the failure it
    is meant to detect: `start()` under a live hold clears the dictation buffer
    and `stop()` ends the recording outright.
    """

    def test_frames_mean_the_mic_is_alive(self):
        rec = RecorderFake(frames=6000)
        self.assertIs(mic_healer.probe_capture(rec, lambda: False, sleep=lambda _s: None), True)

    def test_zero_frames_mean_the_mic_is_wedged(self):
        rec = RecorderFake(frames=0)
        self.assertIs(mic_healer.probe_capture(rec, lambda: False, sleep=lambda _s: None), False)

    def test_it_refuses_while_a_dictation_is_being_held(self):
        rec = RecorderFake()
        self.assertIsNone(mic_healer.probe_capture(rec, lambda: True, sleep=lambda _s: None))
        self.assertEqual(rec.started, 0)  # never touched the stream at all

    def test_a_hold_that_begins_mid_probe_takes_the_stream(self):
        # The dangerous window: we opened the stream, then the user pressed the
        # key. Stopping now would kill their dictation, so we must let go and
        # answer "cannot tell" — a lost diagnosis is cheaper than a lost phrase.
        rec = RecorderFake()
        holds = iter([False, True])
        answer = mic_healer.probe_capture(rec, lambda: next(holds), sleep=lambda _s: None)
        self.assertIsNone(answer)
        self.assertEqual(rec.stopped, 0)

    def test_a_stream_that_will_not_open_is_undecided(self):
        # Deliberately not False: the healer's strong remedy blips system audio
        # for everyone, and a refused open can also mean a transient conflict.
        rec = RecorderFake(start_raises=True)
        self.assertIsNone(mic_healer.probe_capture(rec, lambda: False, sleep=lambda _s: None))

    def test_it_listens_for_the_configured_window(self):
        slept = []
        mic_healer.probe_capture(RecorderFake(), lambda: False, sleep=slept.append)
        self.assertEqual(slept, [mic_healer.PROBE_SECONDS])


class ProbeTests(unittest.TestCase):
    """Asking the mic beats counting failures.

    Counting was only ever a proxy for a question we could not ask: is the
    capture path dead, or did the user just brush the key? Three failures in a
    row was the cheapest available evidence. It cost a real outage on
    2026-08-17: the mic was wedged from the first press, the user stopped after
    two and the remedy never fired. A probe answers directly, so one failure is
    enough — and a stray tap still costs nothing, because the probe clears it.
    """

    def test_wedged_mic_is_healed_on_the_very_first_failure(self):
        # The 2026-08-17 regression: no waiting for a third press that a person
        # who has given up will never make.
        f = HealerFake(probe=False)
        self.assertEqual(f.healer.on_empty(), "coreaudiod")
        self.assertEqual(f.audio_restarts, 1)
        self.assertEqual(f.slept, [mic_healer.COREAUDIOD_SETTLE_SECONDS])
        self.assertEqual(f.restarted, 1)

    def test_reopen_is_still_tried_first_and_the_probe_judges_it(self):
        # Order is the whole point: the cheap remedy runs, then the probe says
        # whether it worked. A probe before the reopen would condemn jams that
        # reopening would have fixed.
        f = HealerFake(probe=False)
        f.healer.on_empty()
        self.assertEqual(f.calls[:2], ["reopen", "probe"])

    def test_healthy_mic_means_it_was_a_stray_tap_and_the_episode_is_dropped(self):
        f = HealerFake(probe=True)
        self.assertEqual(f.healer.on_empty(), "reopen")
        self.assertEqual(f.audio_restarts, 0)
        self.assertEqual(f.restarted, 0)
        # Reset, not merely "not escalated": ten stray taps in a day must not
        # add up to an audio restart when every probe said the mic was fine.
        self.assertEqual(f.healer.consecutive_failures, 0)

    def test_unanswerable_probe_falls_back_to_counting(self):
        # None = "cannot tell right now" (a dictation is in flight and the
        # recorder is not ours to borrow). Unknown must never be read as broken.
        f = HealerFake(probe=None)
        f.healer._probe = f._probe  # probe present, but always undecided
        f.healer.on_empty()
        f.healer.on_empty()
        self.assertEqual(f.healer.on_empty(), "coreaudiod")
        self.assertEqual(f.audio_restarts, 1)

    def test_probe_that_raises_is_undecided_not_broken(self):
        f = HealerFake(probe_raises=True)
        self.assertEqual(f.healer.on_empty(), "reopen")
        self.assertEqual(f.audio_restarts, 0)
        self.assertEqual(f.healer.consecutive_failures, 1)  # counter still armed

    def test_no_probe_configured_keeps_the_old_behaviour(self):
        f = HealerFake()
        self.assertEqual(f.probes, 0)
        f.healer.on_empty()
        f.healer.on_empty()
        self.assertEqual(f.healer.on_empty(), "coreaudiod")

    def test_dead_mic_without_the_sudoers_rule_says_so(self):
        f = HealerFake(probe=False, audio_ok=False)
        f.healer.on_empty()
        self.assertTrue(any("sudoers" in n for n in f.notices))
        self.assertEqual(f.restarted, 0)  # a restart alone would not fix it

    def test_probe_is_not_consulted_once_the_audio_daemon_was_replaced(self):
        # After the coreaudiod step PortAudio is poisoned for the life of this
        # process: every probe would read "dead" and every answer is worthless.
        # Only a fresh process can tell, so stop asking and restart.
        f = HealerFake(probe=False, audio_ok=True)
        f.healer.on_empty()
        probes_after_remedy = f.probes
        self.assertEqual(f.healer.on_empty(), "restart")
        self.assertEqual(f.probes, probes_after_remedy)
        self.assertEqual(f.audio_restarts, 1)  # never blipped twice


if __name__ == "__main__":
    unittest.main()
