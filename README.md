# LocalFlow

Private, fully-local voice dictation for macOS — a Wispr Flow-style workflow where **nothing ever leaves your Mac**. Hold a key, speak (Italian, English, Spanish — anything Whisper knows), release: polished text is pasted into whatever app has focus.

Wispr Flow sends every utterance to cloud ASR + a fine-tuned Llama on Baseten. LocalFlow runs the same two-stage pipeline on-device: **faster-whisper** for speech recognition and (optionally) a small **Llama via Ollama** for Wispr-style cleanup — fillers removed, self-corrections applied, tone matched to the app you're pasting into. The design rationale lives in [docs/superpowers/specs/2026-07-02-localflow-design.md](docs/superpowers/specs/2026-07-02-localflow-design.md).

## Install

```bash
cd localflow
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

First run downloads the Whisper model once (to `~/.cache/huggingface`); after that it's 100% offline.

On flaky connections the automatic download can stall — use the built-in resumable downloader instead (Range-resume, retries, sha256 verification; lands in `~/.localflow/models/`, which takes priority over the HF cache):

```bash
.venv/bin/python -m localflow download small          # or large-v3-turbo, etc.
```

## macOS permissions (one-time)

Grant to the app you run LocalFlow from (Terminal, iTerm, ...), then restart the terminal:

| Permission | Why |
|---|---|
| Privacy & Security → **Microphone** | hear you |
| Privacy & Security → **Accessibility** | paste with a synthetic ⌘V |
| Privacy & Security → **Input Monitoring** | see the global hold-to-talk key |

`.venv/bin/python -m localflow setup` prints this checklist.

## Use

```bash
.venv/bin/python -m localflow            # start the daemon
```

Hold **right Option (⌥)**, speak, release. Pop = recording, Glass = text pasted. The previous clipboard content is restored after pasting.

Other commands:

```bash
.venv/bin/python -m localflow mic-test --seconds 4     # mic + ASR check
.venv/bin/python -m localflow transcribe audio.wav     # pipeline check, no mic/permissions needed
.venv/bin/python -m localflow setup                    # permissions checklist
```

## Configuration — `~/.localflow/config.toml`

Created with commented defaults on first run. The interesting knobs:

- `asr.model` — `small` (default, fast) → `large-v3-turbo` (Wispr-level accuracy, ~1.5 GB download, still fine on Apple Silicon).
- `asr.language` — empty = auto-detect per utterance; set `"it"`, `"en"` or `"es"` to force one (useful for short phrases, where Italian and Spanish can be confused).
- `hotkey.key` — `alt_r` default. (`fn` like Wispr needs a native event tap — not possible from Python; roadmap.)
- `dictionary.terms` — names/jargon Whisper should get right (biases the model, like Wispr's personal dictionary).
- `dictionary.replacements` — hard "wrong" = "right" corrections applied after transcription.
- `output.paste = false` — copy-only mode, no synthetic keystroke needed.

## Optional: AI cleanup (Wispr-style formatting, still 100% local)

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b
```

LocalFlow auto-detects Ollama at startup. With it on, transcripts get filler removal, self-correction handling ("...anzi, facciamo giovedì" keeps only giovedì), punctuation, and app-aware tone (the LLM is told which app you're pasting into). Without it, you get raw Whisper output — which already punctuates decently. Change `format.ollama_model` to any model you've pulled.

## Start at login (optional — do this AFTER the terminal flow works)

A ready-to-use agent lives at `~/Library/LaunchAgents/com.localflow.plist` (copy in [deploy/com.localflow.plist](deploy/com.localflow.plist)). It is inert until loaded:

```bash
launchctl load ~/Library/LaunchAgents/com.localflow.plist    # activate now + at every login
launchctl unload ~/Library/LaunchAgents/com.localflow.plist  # deactivate
tail -f ~/.localflow/localflow.log                           # watch it (no terminal window when run this way)
```

Important: launchd runs LocalFlow as its own process, so macOS asks for **new permission grants** attributed to the Python binary (`.venv/bin/python`) instead of your terminal — approve the Microphone popup, and add/enable that binary under Accessibility and Input Monitoring if dictation stays silent. Errors land in `~/.localflow/localflow.err.log`.

## Troubleshooting

- **Nothing pastes, but the text is on the clipboard** → Accessibility permission missing.
- **Hotkey does nothing** → Input Monitoring permission missing (and restart the terminal).
- **Empty transcripts** → Microphone permission missing, or utterance under 0.3 s.
- **Wrong language detected on short phrases** → set `asr.language = "it"` (or `"en"`, `"es"`).
- **Slow on long dictations** → drop to `small`, or upgrade quality/speed later via the native roadmap.

## Privacy

No telemetry, no accounts, no network calls at runtime. The only downloads ever: pip packages, the Whisper model (once, from Hugging Face), and optionally an Ollama model (once). Airplane mode works.
