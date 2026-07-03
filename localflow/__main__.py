import argparse
import os
import subprocess
import sys


def _other_instances():
    result = subprocess.run(["pgrep", "-f", "python.* -m localflow"], capture_output=True, text=True)
    return [p for p in result.stdout.split() if p != str(os.getpid())]

SETUP_TEXT = """\
LocalFlow — macOS permissions checklist
=======================================
Grant these to the app you run LocalFlow from (Terminal, iTerm, ...):

1. System Settings -> Privacy & Security -> Microphone        -> enable your terminal
2. System Settings -> Privacy & Security -> Accessibility     -> enable your terminal
3. System Settings -> Privacy & Security -> Input Monitoring  -> enable your terminal

(1) lets LocalFlow hear you, (2) lets it paste with a synthetic Cmd+V,
(3) lets it see the global hold-to-talk key. Restart the terminal after granting.

Optional AI cleanup (Wispr-style formatting) — still 100% local:
    brew install ollama
    brew services start ollama
    ollama pull llama3.2:3b

Quick checks without permissions:
    localflow transcribe some_audio.wav      # pipeline test, no mic needed
    localflow mic-test --seconds 4           # mic + ASR test (needs permission 1)
"""


def cmd_run(_args):
    others = _other_instances()
    if others:
        sys.exit("LocalFlow è già in esecuzione (pid %s) — chiudi quella istanza prima." % ", ".join(others))
    from .app import main as run_daemon

    run_daemon()


def cmd_ui(_args):
    others = _other_instances()
    if others:
        sys.exit("LocalFlow è già in esecuzione (pid %s) — chiudi quella istanza prima." % ", ".join(others))
    from .menubar import main as run_ui

    run_ui()


def cmd_transcribe(args):
    from . import config, formatter, textproc
    from .app import _glossary_prompt
    from .transcriber import Transcriber, create_transcriber

    cfg = config.load()
    language = args.language if args.language is not None else cfg.language
    if args.model:  # explicit --model forces the faster-whisper engine with that model
        transcriber = Transcriber(args.model, cfg.compute_type, language, _glossary_prompt(cfg), cfg.beam_size)
    else:
        transcriber = create_transcriber(cfg, language=language, initial_prompt=_glossary_prompt(cfg))
    text, lang = transcriber.transcribe(args.file)
    text = textproc.tidy(text)
    if args.format:
        text = formatter.cleanup(text, cfg.ollama_url, args.ollama_model or cfg.ollama_model)
    text = textproc.apply_dictionary(text, cfg.replacements)
    print("[%s] %s" % (lang, text))


def cmd_mic_test(args):
    import time

    from . import config, textproc
    from .audio import Recorder
    from .transcriber import Transcriber, create_transcriber

    cfg = config.load()
    if args.model:
        transcriber = Transcriber(args.model, cfg.compute_type, cfg.language, "", cfg.beam_size)
    else:
        transcriber = create_transcriber(cfg)
    recorder = Recorder(cfg.sample_rate, cfg.device or None)
    print("Recording %ds — speak now..." % args.seconds)
    recorder.start()
    time.sleep(args.seconds)
    clip = recorder.stop()
    text, lang = transcriber.transcribe(clip)
    print("[%s] %s" % (lang, textproc.tidy(text)))


def cmd_setup(_args):
    print(SETUP_TEXT)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="localflow",
        description="Private, fully-local voice dictation (Wispr Flow style): hold a key, speak, release.",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the hold-to-talk dictation daemon in the terminal")
    sub.add_parser("ui", help="start LocalFlow as a menu-bar app (default)")

    p_tr = sub.add_parser("transcribe", help="transcribe an audio file (pipeline test, no mic needed)")
    p_tr.add_argument("file")
    p_tr.add_argument("--model", help="override the configured Whisper model")
    p_tr.add_argument("--language", help="force a language instead of the configured one")
    p_tr.add_argument("--format", action="store_true", help="also run the Ollama cleanup step")
    p_tr.add_argument("--ollama-model", help="override the configured Ollama model")

    p_mic = sub.add_parser("mic-test", help="record N seconds from the mic and print the transcript")
    p_mic.add_argument("--seconds", type=int, default=4)
    p_mic.add_argument("--model", help="override the configured Whisper model")

    p_dl = sub.add_parser("download", help="download a Whisper model (resumable + sha256-verified)")
    p_dl.add_argument("model", help="tiny | base | small | medium | large-v3 | large-v3-turbo")

    sub.add_parser("setup", help="print the macOS permissions checklist")

    args = parser.parse_args(argv)
    if args.cmd is None or args.cmd == "ui":
        cmd_ui(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "transcribe":
        cmd_transcribe(args)
    elif args.cmd == "mic-test":
        cmd_mic_test(args)
    elif args.cmd == "download":
        from .download import download

        download(args.model)
    elif args.cmd == "setup":
        cmd_setup(args)


if __name__ == "__main__":
    main()
