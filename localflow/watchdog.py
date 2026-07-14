"""Freeze diagnostics & self-healing.

An intermittent hang is the hardest bug to fix because it leaves no evidence:
`err.log` stays empty (nothing crashed) and launchd's `KeepAlive` never fires
(it reacts to *exits*, and a wedged-but-alive process never exits). So the user
is stuck restarting by hand, blind to *where* it hung.

This module closes both gaps, without touching the dictation logic:

  * `faulthandler` on a fatal native fault  -> C-level traceback to stderr
    (which launchd routes to localflow.err.log).
  * SIGUSR1 -> dump EVERY thread's stack on demand. This is the universal
    tool: it works for any freeze — a wedged CoreAudio call, a dead pynput
    listener, a stuck lock. Next time it hangs, before restarting, run:
        kill -USR1 <pid>
    and read ~/.localflow/freeze.log.
  * a liveness watchdog: the daemon marks when a dictation goes "in flight"
    and clears it on completion. If one stays in flight past `dump_after` we
    dump all stacks (early evidence); past `exit_after` we exit non-zero so
    launchd restarts us clean — turning a manual restart into an automatic one.

The thresholds are generous on purpose: the worst *legitimate* dictation (a
dead-engine retry: 120s inference timeout + 90s server re-warm + another
inference) can run several minutes. `exit_after` sits above that, so we only
kill work that is genuinely wedged, never merely slow.
"""
import os
import threading
import time

# Tunables. dump_after: when to capture stacks (cheap, non-fatal). exit_after:
# when to give up and let launchd restart us. See the module docstring.
DUMP_AFTER = 60.0
EXIT_AFTER = 240.0
POLL_INTERVAL = 5.0


def _elapsed(state, now):
    """Seconds the current work item has been in flight (0.0 if idle).

    `state` is None when idle, or a (monotonic_start, label) tuple while a
    dictation/assistant/engine-rebuild is running. Never returns negative:
    a backwards clock must not read as "idle" and hide a real freeze.
    """
    if not state:
        return 0.0
    started, _label = state
    return max(0.0, now - started)


def _check(elapsed, dump_after, exit_after, already_dumped):
    """Pure decision step. Returns (action, already_dumped).

    action is None | "dump" | "exit". The dump is one-shot per episode: once
    fired it stays quiet until the work clears (elapsed back to 0), which
    re-arms it. Exit always wins, even if a slow poll skipped the dump window.
    """
    if elapsed >= exit_after:
        return "exit", already_dumped
    if elapsed >= dump_after and not already_dumped:
        return "dump", True
    if elapsed == 0.0:
        return None, False  # idle: re-arm the one-shot dump for the next episode
    return None, already_dumped


def _loop(get_state, dump_after, exit_after, interval, do_dump, do_exit):
    already = False
    while True:
        time.sleep(interval)
        elapsed = _elapsed(get_state(), time.monotonic())
        action, already = _check(elapsed, dump_after, exit_after, already)
        if action == "dump":
            do_dump(elapsed, get_state())
        elif action == "exit":
            do_dump(elapsed, get_state())
            do_exit()


def install(get_state, log_dir, dump_after=DUMP_AFTER, exit_after=EXIT_AFTER,
            interval=POLL_INTERVAL):
    """Wire up faulthandler, the SIGUSR1 dumper, and the liveness watchdog.

    `get_state` is a zero-arg callable returning the daemon's in-flight marker
    (None or (monotonic_start, label)). Safe to call from any thread: SIGUSR1
    registration is best-effort and degrades to a no-op where unsupported.
    """
    import faulthandler
    import signal

    log_dir = _ensure_dir(log_dir)
    dump_file = open(log_dir / "freeze.log", "a", buffering=1, encoding="utf-8")

    faulthandler.enable()  # fatal native faults -> stderr -> localflow.err.log
    try:  # SIGUSR1: on-demand stack dump. faulthandler uses sigaction(), so
        # unlike signal.signal() it works even off the main thread (ui mode
        # boots the daemon in a background thread). chain=False: dump but do
        # NOT fall through to SIGUSR1's default action (terminate) — the point
        # is to inspect the daemon while it's still alive and wedged.
        faulthandler.register(signal.SIGUSR1, file=dump_file, all_threads=True, chain=False)
    except (AttributeError, ValueError, RuntimeError):
        pass  # no SIGUSR1 on this platform, or registration refused

    def do_dump(elapsed, state):
        label = state[1] if state else "?"
        dump_file.write("\n===== LocalFlow freeze watchdog: '%s' stuck %.0fs @ %s =====\n"
                        % (label, elapsed, time.strftime("%Y-%m-%d %H:%M:%S")))
        faulthandler.dump_traceback(file=dump_file, all_threads=True)
        dump_file.flush()

    def do_exit():
        # os._exit, not sys.exit: every thread is wedged, so a SystemExit in
        # this one wouldn't bring the process down. Non-zero => launchd's
        # KeepAlive/SuccessfulExit=false restarts us with a fresh engine.
        os._exit(1)

    t = threading.Thread(
        target=_loop,
        args=(get_state, dump_after, exit_after, interval, do_dump, do_exit),
        daemon=True,
        name="localflow-watchdog",
    )
    t.start()
    return t


def _ensure_dir(log_dir):
    import pathlib

    path = pathlib.Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path
