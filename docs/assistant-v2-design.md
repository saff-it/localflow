# LocalFlow — Assistente v2 "Copilota" (design)

Data: 7 lug 2026 · Livello: alto · Vincolo #1: **il core della dettatura non si tocca mai.**

## Obiettivo
Un assistente digitale a tutto tondo, sempre pronto: risponde, e quando lo richiami
**vede lo schermo** che stai guardando e ti aiuta nel contesto. Ricorda tutto in un
file di testo, conserva le immagini viste max 24h. Consigliere, non pilota automatico.

## Principio architetturale
L'assistente è un **sottosistema isolato**. Il core dettatura (recorder, whisper
streaming, incolla/copia) è CONGELATO. L'assistente ha moduli, tasto, stato e
disciplina-GPU propri. Se l'assistente esplode, la dettatura non se ne accorge.
Interruttore hard nel config: `[assistant] enabled=false` spegne tutto all'istante.

## Tasti (interazione)
- **⌘ destro tenuto** → detta e incolla (invariato, latenza zero, contratto 100%).
- **⌘ destro tap-poi-tieni** → copia. Il primo tap breve (<0.3s) è scartato dal
  filtro min-durata; il tenuto successivo entro ~400ms = modalità copia. Così il
  tenuto singolo resta dettatura istantanea: nessun ritardo sul caso comune.
- **⌥ destro** → assistente (liberato dalla copia).
- Tasti mutuamente esclusivi (già implementato).

## Cervello: ibrido locale + cloud (con conferma)
- **Locale (default)**: qwen2.5 per domande di testo veloci. Privato, gratis.
- **Cloud (Claude)**: per domande difficili e per TUTTE le domande sullo schermo.
  **Sempre conferma esplicita prima di inviare** (dialogo Sì/No). Chiave in
  `~/.localflow/secrets` (chmod 600, gitignored). Lui può *suggerire* "vuoi che
  chieda a Claude?"; l'invio parte solo col tuo sì.

## Vista sullo schermo (cloud)
- Cattura via `screencapture` (nativo macOS), finestra/schermo attivo, PNG ridotto.
- Salvato in `~/.localflow/vision/` con timestamp; purge >24h a ogni uso.
- La domanda-schermo È una "domanda difficile" → passa dal canale cloud+conferma.
- v1 = consigliere: legge/guarda e suggerisce in notifica. NON clicca, NON scrive.

## Risposta
- **Notifica macOS** = canale principale (leggi). Risposta completa anche negli appunti.
- **Voce** opzionale, accendi/spegni da tasto funzione / menu (streaming frase-per-frase, già fatto).

## Memoria persistente
- `~/.localflow/assistant-memory.md` (testo puro, non pesa): append di ogni scambio
  + nota "visto screenshot X". 
- Caricata in modo intelligente: finestra recente + riassunto progressivo (token
  limitati), non tutto il file ogni volta. Questo è il "ricorda tutto".
- Immagini: solo 24h, poi cancellate; la memoria testuale resta.
- "Nuova conversazione" nel menu per ripartire pulito.

## Convivenza con la dettatura
- Dettatura SEMPRE prioritaria sulla GPU. Il modello dell'assistente sale a bordo
  su richiesta, scende dopo 5 min. La prima domanda dopo pausa paga il caricamento;
  la dettatura non rallenta mai.

## Piano a fasi (ogni fase testabile, dettatura verificata dopo ciascuna)
- **Fase 1 — Tasti**: tap-poi-tieni per copia su ⌘dx, assistente su ⌥dx. Collaudo
  esaustivo dell'affidabilità dettatura PRIMA di aggiungere cervelli. Alto rischio
  per la dettatura → si fa per primo e in isolamento.
- **Fase 2 — Assistente v2 locale**: rewrite pulito, notifiche macOS, voce toggle,
  memoria .md (append + contesto intelligente), nuova conversazione. Tutto offline.
- **Fase 3 — Cervello cloud (Claude, testo)**: `cloud.py` + dialogo conferma +
  storage segreto. Domande difficili → offri cloud. Serve API key.
- **Fase 4 — Vista**: `screen.py` cattura+purge; domande-schermo → conferma →
  Claude vision → risposta. Il gioiello.

Fermandosi anche solo alla Fase 2: assistente locale solido con memoria e notifiche.
Cloud+vista si posano sopra senza rischio.
