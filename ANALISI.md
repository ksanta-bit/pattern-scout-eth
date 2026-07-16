# Analisi del bot e proposte di ottimizzazione

Documento onesto sullo stato del bot, sul perché non è ancora partito nessun trade, e sulle
ottimizzazioni possibili in ordine di impatto. Scritto dopo aver ispezionato il codice e verificato
i comportamenti con test riproducibili.

## 1. Perché non è partito nessun trade

### Causa meccanica (bug bloccante) — RISOLTA
Il workflow girava con `--lookback-days 3`. Con soli 3 giorni di storico, l'**ATR giornaliero
dell'ultimo giorno risulta `NaN`** (l'ATR usa media di Wilder con `min_periods=3` e uno shift di 1
giorno per non guardare al futuro: servono almeno 4 giorni perché oggi abbia un valore). Se l'ATR è
`NaN`, la condizione "candela di manipulation ≥ 20% dell'ATR" non è mai valutabile e **nessun trade
è matematicamente possibile**. Verificato empiricamente: con 3 giorni `is_manipulation = False`
sempre; con 4+ giorni la logica torna a funzionare. Corretto portando il lookback a 20 giorni e
usando `binance.vision` come fonte con storico profondo (Bitget limita a ~1000 candele = 3,5 giorni).

### Cause strutturali (di merito) — da valutare
Anche col bug risolto, i trade restano rari **by design**, per tre motivi:

1. **Ancoraggio a 00:00 UTC.** La strategia del video nasce per l'**apertura di sessione azionaria**
   (il flush di liquidità della prima ora a Wall Street). Su crypto, che è aperto 24/7, alle 00:00
   UTC non c'è un evento strutturale equivalente forte. Esistono i **funding dei perpetui** (00:00 /
   08:00 / 16:00 UTC) che creano un po' di attività, ma non un "flush" garantito come all'open di NY.
2. **Finestra di un'ora al giorno.** I segnali sono cercati solo nei primi 60 minuti dopo l'apertura
   (`signal_cutoff_minutes=60`), con `max_trades_per_session=1`. Quindi al massimo un'occasione al
   giorno, in una fascia che per ETH è spesso poco volatile.
3. **Filtri selettivi.** Serve che la prima candela 15m abbia range ≥ 20% dell'ATR giornaliero **e**
   corpo ≥ 55% del range **e** direzionale, seguita da un John Wick (hammer) o Power of Tower
   (engulfing) con criteri precisi sulle ombre. In un'ora tranquilla capita di rado.

Conclusione onesta: 0 trade in un paio di giorni **non è un guasto**, è la combinazione di una
strategia molto selettiva applicata a un orario poco significativo per le crypto.

## 2. Il filtro daily
Il filtro "daily context" (breakout di un livello + retest) è la parte **più discrezionale** del
video: l'autore lo usa solo nell'esempio live. Nel bot è meccanizzato ma resta molto restrittivo:
nei test rifiuta quasi tutti i setup che il nucleo della strategia accetterebbe. Ora è **DISATTIVO**
di default e c'è un **pulsante nella dashboard** per confrontare dal vivo le due varianti (con e
senza filtro): entrambe girano ad ogni ciclo, così vedi quale rende meglio nel tempo.

## 3. Proposte di ottimizzazione (in ordine di impatto)

**P1 — Ancoraggio della sessione (impatto alto).**
Spostare/aggiungere l'apertura dove c'è davvero volatilità:
- ancorare all'**apertura US (13:30 UTC / 09:30 ET)**, dove ETH reagisce al mercato azionario, oppure
- gestire **più sessioni al giorno** (00:00 / 08:00 / 13:30 UTC) per moltiplicare le occasioni.
È un cambio di configurazione (per l'opzione singola) o una piccola estensione del motore (per il
multi-sessione).

**P2 — Tuning su dati reali (impatto alto, è il vero "ottimizzare").**
Oggi i parametri (soglia 20%, corpo 55%, ombre, trailing) sono ragionevoli ma "a occhio". La cosa
seria è: scaricare **mesi di ETH a 5m**, lanciare il comando `optimize` già incluso, e misurare
win rate, profit factor ed expectancy su dati veri, scegliendo i parametri con criterio (evitando
l'overfitting). Senza questo passo, qualsiasi numero è aneddotico.

**P3 — Più strumenti (impatto medio, facile).**
Aggiungere BTCUSDT, SOLUSDT, ecc.: più occasioni e diversificazione. Il bot già accetta più simboli
(`--symbols ETHUSDT,BTCUSDT`).

**P4 — Soglie leggermente meno rigide (impatto medio).**
Es. manipulation al 15% dell'ATR e corpo al 50%: più segnali, con un compromesso sulla qualità. Da
decidere con i dati (vedi P2), non a naso.

**P5 — Rischio e leva (impatto alto sulla sopravvivenza).**
Con leva 20x, valutare un rischio/operazione più basso (1% invece di 2%) per reggere le serie
negative; il trailing già aiuta a proteggere. Verificare sempre che lo stop scatti prima della
liquidazione (nel modello attuale lo stop è più vicino, quindi protegge).

**P6 — Validazione statistica (impatto alto sull'affidabilità).**
Prima di fidarsi dei risultati servono **almeno 30–50 trade** su dati reali, idealmente in
walk-forward. Un paper con pochi trade non dice quasi nulla.

## 4. Avvertenza
Il video mostra esempi selezionati (cherry-picking): non è una prova che la strategia sia
profittevole, tantomeno su crypto. Il valore di questo progetto è avere un **ambiente di test
rigoroso, trasparente e riproducibile** per capirlo con i tuoi dati — non una promessa di guadagno.

## 5. Cosa consiglio come prossimo passo
1. Deployare le correzioni attuali (bug ATR + pulsante filtro) e lasciar girare qualche giorno per
   vedere i primi trade reali e il confronto ON/OFF dal pulsante.
2. In parallelo, decidere l'ancoraggio sessione (P1) — è la leva che più cambia le cose per le crypto.
3. Poi, tuning su dati storici reali (P2) prima di dare peso ai numeri.
