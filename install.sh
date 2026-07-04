#!/bin/bash
# LocalFlow — installer per macOS (Apple Silicon).
# Uso: copia questa cartella sul Mac di destinazione, poi:  ./install.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== LocalFlow: installazione =="

case "$(pwd)" in
  "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*)
    echo "⚠️  Questa cartella è protetta da macOS: l'avvio automatico non funzionerebbe."
    echo "    Spostala prima, es.:  mv \"$(pwd)\" ~/Applications/LocalFlow  — poi rilancia."
    exit 1;;
esac

command -v brew >/dev/null || { echo "Serve Homebrew: installa da https://brew.sh e rilancia."; exit 1; }

echo "-- motore ASR (whisper.cpp, GPU Metal)"
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp

echo "-- ambiente Python"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "-- modello di trascrizione (download con ripresa + verifica sha256)"
.venv/bin/python -m localflow download ggml:large-v3-turbo-q8_0

echo "-- encoder CoreML per l'Apple Neural Engine (~600 MB; il primo avvio poi compila una tantum, ~1 min)"
.venv/bin/python - <<'PY'
import pathlib, subprocess
from localflow.download import _fetch_with_retry
models = pathlib.Path.home() / ".localflow" / "models"
if not (models / "ggml-large-v3-turbo-encoder.mlmodelc").exists():
    dest = models / "ggml-large-v3-turbo-encoder.mlmodelc.zip"
    _fetch_with_retry(
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-encoder.mlmodelc.zip", dest)
    subprocess.run(["unzip", "-q", "-o", str(dest)], cwd=models, check=True)
print("encoder CoreML pronto")
PY

echo "-- avvio al login (LaunchAgent, inattivo finché non lo abiliti dal menu)"
PLIST="$HOME/Library/LaunchAgents/com.localflow.plist"
sed -e "s#__ROOT__#$(pwd)#g" -e "s#__HOME__#$HOME#g" deploy/com.localflow.plist.template > "$PLIST"

echo "-- app di avvio rapido (Spotlight/Launchpad: 'LocalFlow')"
osacompile -o "$HOME/Applications/LocalFlow.app" -e \
  'do shell script "launchctl kickstart gui/$(id -u)/com.localflow 2>/dev/null || launchctl load ~/Library/LaunchAgents/com.localflow.plist"' 2>/dev/null || true

echo
echo "== Fatto! Prossimi passi =="
echo "1. Permessi macOS (Microfono, Accessibilità, Monitoraggio input):"
echo "     .venv/bin/python -m localflow setup"
echo "2. Avvia l'app (icona 🎤 nella barra menu):"
echo "     .venv/bin/python -m localflow ui"
echo "3. (Facoltativo) Polish AI: brew install ollama && brew services start ollama && ollama pull qwen2.5:7b"
echo "4. (Facoltativo) Avvio automatico: attiva 'Avvia al login' dal menu 🎤."
