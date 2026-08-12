# Struttura del testo: liste e paragrafi senza modello

Data: 12 agosto 2026
Stato: approvato, da implementare

## Il problema

La trascrizione e' affidabile (l'utente la stima al 90-95%) ma esce sempre come un
blocco unico. Chi detta una lista della spesa in WhatsApp riceve un muro di testo con
le virgole, non un elenco.

La funzione per formattare esisteva gia' (`formatter.format_paragraphs`), ma gira su
`qwen2.5:7b`: 4,7 GB di RAM e 20 secondi di attesa misurati il 12 agosto su una
dettatura di 49 secondi, con il Mac a 70 MB di memoria libera. Costo inaccettabile,
quindi era di fatto spenta (`paragraphs = "never"` dal 12 agosto).

## La decisione

Struttura **a regole, zero AI**. Le due strutture che servono davvero, liste e
paragrafi. Niente numerazione automatica, niente grassetto, niente titoli: ogni
struttura in piu' e' un'occasione in piu' di formattare dove non si voleva, e il
grassetto non ha una sintassi comune fra WhatsApp, Mail e gli editor.

## Architettura

Un modulo nuovo e isolato, `localflow/structure.py`. Funzioni pure: testo in, testo
fuori. Nessuna rete, nessun modello, nessuno stato. Si puo' leggere, testare e
cambiare senza toccare il resto dell'app.

```
trascrizione -> textproc.tidy -> dizionario -> structure.apply() -> incolla
```

`structure.apply(text, enabled)` ritorna il testo invariato quando la funzione e'
spenta, senza nemmeno analizzarlo.

### Regola 1: lista

Scatta quando esistono entrambe le cose:

1. un annuncio di elenco nella frase precedente o nella stessa: "la lista", "mi
   servono", "mi serve", "le cose sono", "ti do", "dobbiamo comprare", "serve";
2. almeno **tre** elementi separati da virgola (l'ultimo puo' essere introdotto da
   "e"), ciascuno lungo al massimo **quattro parole**.

Risultato: la frase che annuncia resta sopra e finisce con i due punti, ogni elemento
va su una riga preceduta da "- ". Il vincolo sui tre elementi corti evita di spezzare
una frase normale piena di virgole ("sono andato al mare, poi ho mangiato, poi sono
tornato": gli elementi sono lunghi e non c'e' annuncio).

### Regola 2: paragrafi

Solo sopra i **250 caratteri** e solo se il testo non contiene gia' a capo. Inserisce
una riga vuota prima di una frase che comincia con uno stacco reale: "poi", "inoltre",
"per quanto riguarda", "un'altra cosa", "infine", "in piu'", "detto questo",
"per il resto". Sono i connettivi misurati nel log dell'utente, non una lista generica.

Mai piu' di un a capo consecutivo, mai un a capo all'inizio o alla fine.

### La garanzia

`structure.apply()` puo' aggiungere soltanto spazi bianchi e il marcatore "- ".
Prima di restituire il testo lo verifica con `formatter._structural_only(originale,
risultato)`, il controllo gia' scritto e testato per il percorso AI: se una sola
parola risulta aggiunta, cambiata o spostata, il risultato viene scartato e vince il
testo originale. La garanzia e' un controllo che gira a ogni dettatura, non una
promessa nel commento.

## Interruttore

- Chiave di configurazione `[format] structure = true` (default: acceso).
- Voce nel menu 🎤 "Struttura: liste e paragrafi", con la spunta, accanto agli altri
  interruttori; scrive la chiave con `config.set_key` come fanno gia' lingua,
  streaming e Polish.
- Da spenta il testo non passa dal modulo: costo esattamente zero, la velocita' di oggi.

## Test

`tests/test_structure.py`, unittest come il resto del progetto. Casi obbligatori:

- la lista della spesa reale dettata il 12 agosto (latte, detersivo, spazzolino
  dentifricio, profilattici) diventa quattro righe con il trattino;
- una frase con virgole ma senza annuncio resta intatta;
- una lista di due soli elementi resta intatta;
- un testo lungo con "poi" e "per quanto riguarda" prende le righe vuote;
- un testo corto con "poi" resta intatto;
- un testo che contiene gia' a capo non viene ritoccato;
- con l'interruttore spento il testo torna identico, byte per byte;
- il controllo anti-riscrittura scarta un risultato manomesso.

## Fuori perimetro

Numerazione ("primo, secondo, terzo"), grassetto, titoli, e qualunque uso di un
modello AI per la struttura. Il percorso `format_paragraphs` con qwen resta nel
codice, spento, per chi voglia riaccenderlo a mano.
