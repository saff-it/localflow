# LocalFlow — private, fully-local Wispr Flow alternative (design)

Date: 2026-07-02 · Status: approved-by-default (autonomous session; assumptions listed below are the answers to the questions I would have asked interactively — flag any you disagree with and the affected module can be swapped in isolation).

## 1. What Wispr Flow actually is (research summary)

Wispr Flow is hold-a-key dictation: hold `fn`, speak, release → polished text appears in whatever app has focus. Under the hood it is **cloud software**:

- **Client** (macOS/Windows Electron + native helpers, mobile keyboards): global hotkey capture, mic capture, text insertion into any app, on-device capture of user corrections, local context gathering (active app / recipient).
- **Cloud ASR**: context-conditioned, personalized speech models (OpenAI listed as a subprocessor). Budget: E2E ASR inference < 200 ms.
- **Cloud LLM cleanup**: fine-tuned **Llama** models ("token-level formatting control") remove fillers, apply self-corrections ("...no wait, X"), fix punctuation, and match tone to the destination app. Runs on Baseten with TensorRT-LLM; 100+ tokens in < 250 ms.
- **Latency contract**: < 700 ms p99 end-to-end from release-of-key to text-in-field, incl. a 200 ms networking budget.
- **Privacy**: "Privacy Mode" = zero retention, but there is **no offline mode** — every utterance leaves the machine. That is the gap this project closes.

Sources: [wisprflow.ai/post/technical-challenges](https://wisprflow.ai/post/technical-challenges), [Baseten case study](https://www.baseten.co/resources/customers/wispr-flow/), [wisprflow.ai/data-controls](https://wisprflow.ai/data-controls), [offline review](https://weesperneonflow.ai/en/blog/2026-02-09-wispr-flow-review-cloud-dictation-2026/).

## 2. Goal / non-goals

**Goal**: the same interaction — hold a key, speak (Italian, English or Spanish), release, polished text lands in the focused app — with **zero bytes leaving the Mac**. Models are downloaded once, then everything runs offline.

**Non-goals (v1)**: Windows/mobile, streaming partial transcripts while speaking, learning from corrections, menu-bar GUI, sub-700 ms latency parity (local target: "feels immediate", ~1–3 s for a typical utterance on Apple Silicon CPU).

## 3. Assumptions (in lieu of interactive Q&A)

| Question I'd have asked | Assumed answer | Why |
|---|---|---|
| Platform? | macOS only | It's "my machine" — an M-series Mac (verified arm64, macOS 26.5) |
| Languages? | Italian + English + Spanish, auto-detect | User works in all three (the landing site itself is IT/EN/ES/PT); Whisper auto-detects per utterance, `asr.language` can force one |
| Hotkey UX? | Hold-to-talk, default **right Option** | Wispr's core interaction; `fn` can't be captured without a native event tap — documented limitation |
| Stack? | Python 3.9-compatible package | Only system Python 3.9.6 present; no Homebrew Python install forced on the user |
| ASR engine? | **faster-whisper** (CTranslate2, CPU int8) | pip-only install (no compile), multilingual, `small` default / `large-v3-turbo` documented upgrade |
| Cleanup LLM? | **Ollama + llama3.2:3b, optional** | Same "Llama cleans the transcript" role as Wispr; Ollama is NOT installed on this machine, so it auto-detects and degrades gracefully to raw Whisper output |
| Text insertion? | clipboard + synthetic ⌘V, then restore clipboard | Most reliable app-agnostic method without a native AX-API binary |
| Project home? | `~/Documents/progetti-siti/localflow`, own git repo | Sibling of other projects, unrelated to localmind-lab |

## 4. Approaches considered

1. **Python daemon (chosen)** — pynput + sounddevice + faster-whisper + Ollama-over-HTTP + pbcopy/osascript. One `pip install`, every stage swappable, works on stock Python 3.9. Trade-off: can't capture `fn`, ~2× slower than native, no menu-bar icon.
2. Native Swift menu-bar app (WhisperKit/CoreML) — best UX and latency, `fn` capture possible; but a full Xcode project the user would have to build/sign. Right v2, wrong v1.
3. Electron (like Wispr) — replicates their client faithfully but heaviest of the three with no upside locally; whisper.cpp bindings in Node are the weakest link.

## 5. Architecture

```
hold key ──▶ hotkey.py (pynput HoldToTalk)
                │ press                 │ release
                ▼                       ▼
          audio.py Recorder ──▶ float32 16 kHz mono numpy clip
                                        │
                                        ▼
                      transcriber.py (faster-whisper, VAD,
                      glossary bias via initial_prompt)      [Wispr: cloud ASR]
                                        │
                                        ▼
                      formatter.py (Ollama /api/chat, optional,
                      app-aware tone, fail-open to raw text)  [Wispr: fine-tuned Llama]
                                        │
                                        ▼
                      textproc.py (tidy + hard dictionary replacements)
                                        │
                                        ▼
                      inject.py (pbcopy → ⌘V keystroke → restore clipboard)
```

- **Config**: `~/.localflow/config.toml`, created with a commented default on first run. Hotkey, model, language, Ollama, dictionary (bias `terms` + hard `replacements`), output behavior.
- **Personal dictionary** maps to Wispr's: `terms` bias Whisper via `initial_prompt`; `replacements` are deterministic post-fixes.
- **App-aware tone** maps to Wispr's context awareness: frontmost app name (via System Events) is added to the LLM system prompt.
- **Error handling**: every stage fails open — no Ollama → raw transcript; <0.3 s audio → ignored (also kills silence hallucinations, alongside Whisper VAD); paste failure leaves text on the clipboard.
- **Concurrency**: transcription runs on a worker thread behind a lock so the key listener never blocks and utterances can't interleave.

## 6. Testing

- Unit tests (unittest): `textproc` tidy + dictionary semantics.
- Offline end-to-end ASR check without a mic: synthesize speech with macOS `say` (EN + IT voices), convert with `afconvert`, run `localflow transcribe file.wav` — exercises config → transcriber → textproc exactly as the daemon does.
- Not machine-verifiable in a headless session: mic capture, global hotkey, ⌘V injection (all need TCC permissions granted in System Settings — checklist in `localflow setup` and README).

## 7. Roadmap (not built)

Streaming partial transcripts (chunked decode while key held) · native Swift/WhisperKit port with `fn` capture and AX-API insertion · correction learning (diff pasted text vs. what user edits) · menu-bar indicator.
