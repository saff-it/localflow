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
.venv/bin/python -m localflow            # menu-bar app (🎤 icon) — the normal way
.venv/bin/python -m localflow run        # same daemon, headless in the terminal
```

Hold **right Option (⌥)**, speak, release. Pop = recording, Glass = text pasted. The previous clipboard content is restored after pasting. From the 🎤 menu you can switch language on the fly (Italiano/Auto/Español/English), toggle the AI polish, enable start-at-login, and open the config file. Dictations longer than ~28s are split at your quietest pause so Whisper's 30s window seam never garbles words.

Other commands:

```bash
.venv/bin/python -m localflow mic-test --seconds 4     # mic + ASR check
.venv/bin/python -m localflow transcribe audio.wav     # pipeline check, no mic/permissions needed
.venv/bin/python -m localflow setup                    # permissions checklist
```

## ASR engines

Two interchangeable engines, picked automatically (`asr.engine = "auto"`):

1. **whisper.cpp (Metal GPU)** — preferred. Needs `brew install whisper-cpp` plus a ggml model: `localflow download ggml:large-v3-turbo-q5_0`. The daemon keeps the model resident via `whisper-server`; one-shot CLI commands use `whisper-cli`. Flash attention on.
2. **faster-whisper (CPU)** — fallback when whisper.cpp isn't available. Fine for `small`, too slow for the large models.

On this machine (M4): turbo on GPU ≈ 2.5s of encode per utterance + ~2s if language auto-detection is on; on CPU the same model took 21s.

## Configuration — `~/.localflow/config.toml`

Created with commented defaults on first run. The interesting knobs:

- `asr.engine` / `asr.whispercpp_model` — see ASR engines above.
- `asr.language` — `""` auto-detects per utterance but costs an extra ~2s pass; set `"it"` if you mostly dictate one language.
- `asr.model` — faster-whisper fallback model: `small` (default, fast) → `large-v3-turbo` (accurate but CPU-only = slow).
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

## Start at login (optional)

Toggle **"Avvia al login"** from the 🎤 menu (the LaunchAgent at `~/Library/LaunchAgents/com.localflow.plist` is written by `install.sh`). Logs land in `~/.localflow/localflow.log` / `.err.log`.

Important: launchd runs LocalFlow as its own process, so macOS asks for **new permission grants** attributed to the Python binary (`.venv/bin/python`) instead of your terminal — approve the Microphone popup, and add/enable that binary under Accessibility and Input Monitoring if dictation stays silent.

## Install on another Mac

Copy this folder (without `.venv/`) — zip, AirDrop, or a private git repo — then on the target Mac:

```bash
cd localflow && ./install.sh
```

The script installs whisper-cpp via Homebrew, builds the venv, downloads the model (resumable + sha256-verified) and prepares the LaunchAgent. Then grant the three permissions (`localflow setup` prints the checklist). Requirements on the target: Apple Silicon, Homebrew, Python 3.9+.

## Troubleshooting

- **Nothing pastes, but the text is on the clipboard** → Accessibility permission missing.
- **Hotkey does nothing** → Input Monitoring permission missing (and restart the terminal).
- **Empty transcripts** → Microphone permission missing, or utterance under 0.3 s.
- **Wrong language detected on short phrases** → set `asr.language = "it"` (or `"en"`, `"es"`).
- **Slow on long dictations** → drop to `small`, or upgrade quality/speed later via the native roadmap.

## Privacy

No telemetry, no accounts, no network calls at runtime. The only downloads ever: pip packages, the Whisper model (once, from Hugging Face), and optionally an Ollama model (once). Airplane mode works.
