"""Self-healing for a wedged microphone.

A dictation that comes back with *zero* frames means the key was seen, the
recorder was collecting, and CoreAudio still delivered nothing: the capture
path is wedged. `app.py` already reacts by calling `recorder.reopen()`, but on
2026-08-16 that fired 15 times in a row and never helped, because the wedge was
not in our stream at all — it was in `coreaudiod`, a *system* daemon outside
this process. Reopening our own handle cannot fix a jam one level down.

So the remedy escalates, cheapest first, and only ever moves up when the step
below has already failed:

  1. reopen  — our own stream (what app.py does today). Fixes light jams.
  2. coreaudiod — restart the system audio daemon. Fixes the 2026-08-16 case.
                  Needs the sudoers rule in /etc/sudoers.d/localflow, which
                  permits exactly one command and nothing else.
  3. restart — exit non-zero so launchd rebuilds us clean. Last resort.

The counter only escalates on *consecutive* failures: any dictation that
produces audio proves the mic works and resets everything. Without that reset a
handful of accidental short taps spread over a week would eventually add up and
restart the audio daemon for no reason.

The counter, though, is only a *proxy* for the question that matters: is the
capture path dead, or did the user merely brush the key? Both look identical in
the log (0.00s of audio), so the first version bought certainty with patience —
three failures in a row. On 2026-08-17 that bill came due: the mic was wedged
from the very first press, the user stopped after two and the remedy never
fired, so the app sat broken until it was repaired by hand.

So we ask instead of counting. `probe` records a fraction of a second and
reports whether CoreAudio handed over *any* frames — the honest discriminator,
because a healthy mic in a silent room still delivers frames (RMS ~0.0019),
while a wedged one delivers exactly none. With an answer available, one failure
is enough to act, and a stray tap costs nothing at all: the probe clears the
episode instead of banking it. The counter stays as the fallback for the moments
the probe cannot answer (a dictation is in flight and the recorder is busy),
which is also why `None` must never be read as "broken".
"""
import subprocess
import time

# Failures in a row before each remedy. Reopen is cheap and runs every time;
# the coreaudiod step is deliberately rare because it blips system audio.
REOPEN_AFTER = 1
COREAUDIOD_AFTER = 3
# Past the coreaudiod step, restart the process alone: the mic is wedged but
# the system daemon was already replaced, so blipping everyone's audio a second
# time would cost the user something and fix nothing.
RESTART_AFTER = 5

# A restarted coreaudiod needs a moment before it accepts clients again;
# reopening the stream any sooner just fails and burns an escalation step.
COREAUDIOD_SETTLE_SECONDS = 2.0

# How long the probe listens. Long enough that a working device has certainly
# delivered a block (they arrive every few tens of ms), short enough that the
# window in which a real dictation could collide with it stays negligible.
PROBE_SECONDS = 0.4


def next_remedy(consecutive_failures,
                reopen_after=REOPEN_AFTER,
                coreaudiod_after=COREAUDIOD_AFTER,
                restart_after=RESTART_AFTER):
    """Pure decision step: how many empty dictations in a row -> what to do.

    Returns None | "reopen" | "coreaudiod" | "restart". Checked strongest-first
    so a threshold that is passed (not merely hit) still escalates: if a burst
    of failures arrives faster than we act, we must not fall back to the mild
    remedy that has already been proven useless in this episode.

    `restart` is >= rather than == on purpose. It is the terminal state: once
    there, staying there is correct — launchd's restart is what clears it.
    """
    if consecutive_failures >= restart_after:
        return "restart"
    if consecutive_failures == coreaudiod_after:
        return "coreaudiod"
    if consecutive_failures >= reopen_after:
        return "reopen"
    return None


def restart_coreaudiod(run=subprocess.run, timeout=10.0):
    """Restart macOS' audio daemon. Returns True when the command succeeded.

    `sudo -n` never prompts: without the sudoers rule this fails immediately
    and reports False, instead of hanging forever on a password prompt that
    nobody is there to answer. That is the whole reason the rule exists.

    macOS relaunches coreaudiod by itself within a second or so; killing it is
    the supported way to clear a jam. Every failure mode is reported, never
    raised: this runs on a background healing thread, and a crash there would
    take out the retry path that is trying to rescue the app.
    """
    try:
        done = run(["/usr/bin/sudo", "-n", "/usr/bin/killall", "coreaudiod"],
                   capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def probe_capture(recorder, dictation_in_flight, sleep=time.sleep,
                  seconds=PROBE_SECONDS):
    """Record a moment of nothing; report whether CoreAudio answered.

    True = frames arrived, so the mic is alive (a silent room still has
    samples). False = zero frames, so the capture path is wedged. None = this
    was not a fair moment to ask, and unknown must never be mistaken for dead.

    The care here is all about ownership: there is one recorder and the user
    dictates with it, while this runs on a healing thread. Calling `start()`
    under a live hold would clear their buffer and `stop()` would end their
    recording, so the probe would produce the very silence it went looking for.
    Hence the check on both sides of the listen, and hence walking away from a
    stream that became someone else's without stopping it.
    """
    if dictation_in_flight():
        return None
    try:
        recorder.start()
    except Exception:
        # A refused open is ambiguous (transient conflict, device switching),
        # and the remedy it would trigger costs everyone their audio. Ambiguity
        # goes to the counter, not to the loudest button.
        return None
    sleep(seconds)
    if dictation_in_flight():
        return None
    return len(recorder.stop()) > 0


class MicHealer:
    """Tracks consecutive empty dictations and applies the escalating remedy.

    Owns no audio state of its own: it is handed the recorder's `reopen` and a
    `restart` callable, so the policy stays testable with fakes and the app
    keeps the single source of truth for the stream itself.
    """

    def __init__(self, reopen, restart, notify=None,
                 restart_audio=restart_coreaudiod, sleep=time.sleep, probe=None):
        self._reopen = reopen
        self._restart = restart
        self._notify = notify or (lambda _msg: None)
        self._restart_audio = restart_audio
        self._sleep = sleep
        self._probe = probe
        self.consecutive_failures = 0
        self._audio_replaced = False

    def on_success(self):
        """A dictation produced audio: the mic works, forget the whole episode."""
        self.consecutive_failures = 0

    def on_empty(self):
        """A dictation came back with zero frames. Returns the remedy applied."""
        self.consecutive_failures += 1

        if self._audio_replaced:
            # The daemon was already swapped under this process, so PortAudio
            # here is poisoned for good: a probe would read "dead" whatever the
            # true state of the hardware, and a reopen cannot succeed. Nothing
            # left to learn and nothing cheaper to try — only a fresh process.
            return self._apply("restart")

        if self._probe is None:
            return self._apply(next_remedy(self.consecutive_failures))

        # Cheapest remedy first, then ask the mic whether it actually worked.
        # Probing before the reopen would condemn light jams that reopening
        # fixes, and those are the common case.
        self._safely(self._reopen)
        healthy = self._ask_probe()
        if healthy is True:
            # A stray tap, or a jam the reopen just cleared. Either way the mic
            # is proven alive, so the episode is over: nothing to bank.
            self.consecutive_failures = 0
            return "reopen"
        if healthy is None:
            return self._apply(next_remedy(self.consecutive_failures), reopened=True)
        return self._apply("coreaudiod")

    def _ask_probe(self):
        """True = mic delivers frames, False = wedged, None = cannot tell now."""
        try:
            return self._probe()
        except Exception:
            # An unreachable probe is an unanswered question, not a diagnosis.
            return None

    def _apply(self, remedy, reopened=False):
        if remedy == "reopen":
            if not reopened:
                self._safely(self._reopen)
        elif remedy == "coreaudiod":
            # Tell the user *before* the audio drops out, so the blip reads as
            # us fixing something rather than as one more thing going wrong.
            self._notify("Microfono bloccato: riavvio l'audio di sistema.")
            if self._restart_audio():
                self._audio_replaced = True
                # Restart the process, do NOT merely reopen the stream. Pulling
                # coreaudiod out from under a live PortAudio leaves it poisoned
                # for the lifetime of the process: every later open fails with
                # -9986 and the app is worse off than before the remedy (seen
                # for real on 2026-08-16). Only a fresh process clears it, and
                # launchd gives us one in ~2s.
                self._sleep(COREAUDIOD_SETTLE_SECONDS)
                self._restart()
            else:
                self._notify("Non riesco a riavviare l'audio: manca il permesso "
                             "(/etc/sudoers.d/localflow).")
        elif remedy == "restart":
            self._notify("Microfono ancora bloccato: riavvio LocalFlow.")
            self._restart()

        return remedy

    def _safely(self, fn):
        # A failing remedy must not kill the healing thread: the next press
        # escalates to the stronger step, which is exactly the design.
        try:
            fn()
        except Exception:
            pass
