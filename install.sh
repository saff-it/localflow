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
.venv/bin/python -m localflow download ggml:large-v3-turbo-q5_0

echo "-- avvio al login (LaunchAgent, inattivo finché non lo abiliti dal menu)"
PLIST="$HOME/Library/LaunchAgents/com.localflow.plist"
sed -e "s#__ROOT__#$(pwd)#g" -e "s#__HOME__#$HOME#g" deploy/com.localflow.plist.template > "$PLIST"

echo "-- app di avvio rapido (Spotlight: 'Avvia LocalFlow')"
osacompile -o "$HOME/Applications/Avvia LocalFlow.app" -e \
  'do shell script "launchctl kickstart gui/$(id -u)/com.localflow 2>/dev/null || launchctl load ~/Library/LaunchAgents/com.localflow.plist"' 2>/dev/null || true

echo
echo "== Fatto! Prossimi passi =="
echo "1. Permessi macOS (Microfono, Accessibilità, Monitoraggio input):"
echo "     .venv/bin/python -m localflow setup"
echo "2. Avvia l'app (icona 🎤 nella barra menu):"
echo "     .venv/bin/python -m localflow ui"
echo "3. (Facoltativo) Polish AI: brew install ollama && brew services start ollama && ollama pull qwen2.5:7b"
echo "4. (Facoltativo) Avvio automatico: attiva 'Avvia al login' dal menu 🎤."
