# LocalFlow — Backlog

Ricavato da un audit del 2026-07-14 basato sui log reali (`~/.localflow/localflow.log`).

## Diagnosi del freeze ("ogni tanto si blocca, devo riavviare a mano")

Evidenza:

- `err.log` vuoto (0 byte) e nessun crash report in `~/Library/Logs/DiagnosticReports/`
  → **non è un crash**: è un **hang mentre il processo è vivo**.
- `KeepAlive/SuccessfulExit=false` nel plist riavvia il daemon solo se *esce*. Un
  processo appeso non esce mai → nessun auto-restart → restart manuale.
- Nel log: `streaming fallito ... [Errno 61] Connection refused` **6 volte** →
  il child `whisper-server` (whisper.cpp su GPU Metal) **muore/si impianta** sotto
  il daemon, che resta vivo ma cieco.
- Motore ASR reinizializzato ~86 volte in 10 giorni → daemon che riparte spesso.

Causa probabile: `whisper-server` fragile sotto carico GPU sostenuto (dettature da
40–58s, `beam_size=5`, chunk streaming da 7s). Il recupero esiste ma gira **sotto
il lock globale `_busy`** e può costare fino a ~90s (`_wait_ready`) + 120s (timeout
inferenza) → minuti di silenzio che leggono come "freeze".

## Fatto

- [x] **P0 — faulthandler + SIGUSR1 + watchdog di liveness** (`localflow/watchdog.py`).
  - Al prossimo freeze: `kill -USR1 <pid>` → stack di ogni thread in `~/.localflow/freeze.log`.
  - Se una dettatura resta appesa >60s → dump automatico; >240s → `os._exit(1)` e
    launchd riavvia pulito. Testato end-to-end (`tests/test_watchdog.py`).

## Da fare

### P1 — togliere le chiamate lente da sotto il lock `_busy`
- [ ] Ricostruzione del motore ASR fuori dal `_busy` (`_wait_ready` non deve mai
      bloccare la pipeline di dettatura).
- [ ] Backoff sui restart di `whisper-server`: se crasha 3× in 60s, fermarsi e
      notificare invece di rilanciare in loop.
- [ ] Health-check proattivo di `whisper-server` (ping `/` ogni ~5s + rilancio)
      invece di scoprirlo morto con un timeout da 120s durante una dettatura.

### P2 — ridurre la causa scatenante
- [ ] Valutare `beam_size=3` e/o un modello più leggero come default sotto carico.
- [ ] Stampare il banner di boot anche in modalità `ui` (oggi non appare →
      impossibile contare i restart con precisione).

### P3 — igiene / regressioni
- [ ] Test che simula la morte di `whisper-server` a metà dettatura (path di recupero).
- [ ] Aggiungere `pytest` a `requirements.txt` (dev) o documentare che i test
      girano con `python -m unittest`.
- [ ] Watchdog: `freeze.log` è aperto una volta all'avvio; se il file viene
      cancellato/ruotato mentre il daemon gira, i dump vanno nell'inode
      scollegato. Riaprire il file per-dump in `do_dump` (SIGUSR1 via
      faulthandler resta con l'fd persistente). Priorità bassa: nulla ruota
      quel file.
