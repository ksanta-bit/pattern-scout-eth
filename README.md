# Pattern Scout Bot

> **Novità — paper trading operativo su ETH (dati reali, soldi finti).**
> Vedi **[PAPER_TRADING.md](PAPER_TRADING.md)** per il paper live su Binance/Bitget (leva 20x,
> fee Bitget, capitale 100 USDT, dashboard con reset e log operazioni aperte) e
> **[DEPLOY_GITHUB.md](DEPLOY_GITHUB.md)** per farlo girare da solo su GitHub con dashboard pubblica.
> Comando rapido: `PYTHONPATH=src python3 -m pattern_scout.cli paper-crypto --symbols ETHUSDT --config config.crypto.json`

Bot di ricerca, in stile Freqtrade, per testare in modo riproducibile la strategia del video:

1. Aspetta la chiusura della prima candela da 15 minuti.
2. Disegna l'opening range tra massimo e minimo di quella candela.
3. Misura l'ATR giornaliero.
4. Considera la prima candela una manipulation candle se il suo range vale almeno il 20% dell'ATR daily.
5. Preferisce setup estremi, circa 70-80% dell'ATR daily.
6. Richiede un contesto daily meccanico: breakout o breakdown recente e retest del livello.
7. Scende su 5 minuti.
8. Entra solo dopo conferma John Wick o Power of Tower.
9. Usa come target principale il ritorno al lato opposto dell'opening range.

## Perche non ho copiato Freqtrade pari pari

Freqtrade e ottimo, ma nasce soprattutto per crypto/exchange via CCXT. Questa teoria invece dipende da:

- apertura ufficiale di sessione,
- prima ora di mercato,
- ATR daily calcolato senza usare dati futuri,
- trigger intrabar su rottura di massimo/minimo,
- stop sotto/sopra la wick,
- target sull'opening range.
- filtro daily su breakout/retest o breakdown/retest.

Per testarla fedelmente ho quindi creato un motore autonomo, piu preciso per questa strategia. Dentro trovi comunque anche un adattatore Freqtrade in:

```text
freqtrade_user_data/strategies/PatternScoutFreqtradeStrategy.py
```

## Installazione

Da questa cartella:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Se vuoi usare l'ambiente Python gia presente, puoi anche evitare la virtualenv e lanciare con:

```bash
PYTHONPATH=src python3 -m pattern_scout.cli --help
```

## Dati richiesti

Usa CSV a 5 minuti con colonne:

```text
timestamp,open,high,low,close,volume
```

Metti i file in `data/`.

## Comandi chiari

### Comando unico: reset + avvio dashboard

Questo spegne la vecchia dashboard Pattern Scout, rigenera il backtest demo, avvia il nuovo server dashboard e apre il browser.

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
./avvia.command
```

### Spegnere tutto

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
./spegni.command
```

### 1. Demo completa

Questo crea dati demo, fa il backtest e genera la dashboard.

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli run-demo
```

Dashboard:

```text
reports/sample/dashboard.html
```

### 1b. Aprire la dashboard in locale

Avvia il server dashboard:

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli serve-dashboard --reports reports/sample
```

Poi apri:

```text
http://127.0.0.1:8766/dashboard.html
```

Controlla che stia rispondendo:

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli check-dashboard
```

Spegni tutto quello che il CLI ha avviato:

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli shutdown
```

Versione aggressiva, usata da `spegni.command`, per chiudere tutte le dashboard Pattern Scout locali:

```bash
PYTHONPATH=src python3 -m pattern_scout.cli shutdown --force --all
```

### 2. Backtest su un tuo CSV

Sostituisci `data/mio_file_5m.csv` con un file reale.

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli backtest \
  --data data/mio_file_5m.csv \
  --config config.example.json \
  --out reports/test_reale \
  --dashboard
```

Dashboard:

```text
reports/test_reale/dashboard.html
```

### 3. Generare solo la dashboard da un report gia fatto

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli dashboard --reports reports/test_reale
```

### 4. Creare solo dati demo

```bash
cd "/Users/macgian/Documents/Scalp theory 1/pattern_scout_bot"
PYTHONPATH=src python3 -m pattern_scout.cli make-sample --out data/sample_5m.csv
```

## Smoke test manuale

Crea un piccolo CSV sintetico:

```bash
PYTHONPATH=src python3 -m pattern_scout.cli make-sample --out data/sample_5m.csv
```

Lancia il backtest:

```bash
PYTHONPATH=src python3 -m pattern_scout.cli backtest --data data/sample_5m.csv --config config.example.json --out reports/sample
```

Output principali:

```text
reports/sample/summary.json
reports/sample/trades.csv
reports/sample/equity_curve.csv
reports/sample/annotated_candles.csv
```

## Regole codificate

### Opening range

Default:

- timeframe base: 5m
- opening range: primi 15 minuti, quindi prime 3 candele
- direzione down -> si cercano long reversal
- direzione up -> si cercano short reversal

### Manipulation candle

Default:

```text
opening_range >= 0.20 * ATR_daily
```

In piu, per tradurre il concetto del video "fast, aggressive, one direction", il bot richiede che il body della prima candela 15m sia almeno il 55% del suo range.

Puoi disattivare questa severita mettendo:

```json
"opening_body_fraction_min": 0.0
```

### Daily context

Default attivo.

Il filtro trasforma la lettura discrezionale del video in una regola meccanica:

- Long: nei giorni precedenti deve esserci stato un breakout sopra il massimo di una base daily.
- Il livello rotto diventa supporto candidato.
- Il flush dell'apertura deve retestare quel livello entro una tolleranza.
- Short: stessa logica invertita, breakdown sotto una base e retest come resistenza.

I parametri principali sono in `daily_context`:

```json
{
  "enabled": true,
  "lookback_sessions": 20,
  "min_base_sessions": 3,
  "breakout_recent_sessions": 5,
  "retest_tolerance_atr_fraction": 0.15,
  "retest_tolerance_pct": 0.003
}
```

La tolleranza e il massimo tra una frazione dell'ATR daily e una frazione percentuale del livello.

### John Wick

Long:

- lower wick grande rispetto al body,
- chiusura nella meta alta della candela,
- entrata quando una candela successiva rompe il massimo del John Wick,
- stop sotto la wick,
- target opening range high.

Short:

- upper wick grande rispetto al body,
- chiusura nella meta bassa della candela,
- entrata quando una candela successiva rompe il minimo del John Wick,
- stop sopra la wick,
- target opening range low.

### Power of Tower

Long:

- candela precedente rossa, grande e direzionale,
- entrata quando il prezzo recupera il 50% della candela precedente,
- stop sul minimo della giornata fino a quel momento,
- target opening range high.

Short:

- candela precedente verde, grande e direzionale,
- entrata quando il prezzo perde il 50% della candela precedente,
- stop sul massimo della giornata fino a quel momento,
- target opening range low.

## Limiti importanti

Questo e un bot di ricerca, non un invito a tradare live.

Il video non definisce numericamente:

- quanto deve essere lunga una wick,
- quale periodo ATR usare,
- cosa fare con news/earnings,
- slippage reale all'apertura,
- spread e liquidita,
- gestione di gap enormi,
- supporti/resistenze daily molto discrezionali non esprimibili come breakout/retest.

Per questo quei punti sono parametri nel file `config.example.json`.

## Nota Freqtrade

L'adattatore Freqtrade usa la struttura ufficiale delle strategie: `populate_indicators`, `populate_entry_trend`, `custom_stoploss`, `custom_exit`, `enter_long` e `enter_short`.

Pero Freqtrade processa segnali su candele chiuse e normalmente entra alla candela successiva. Il motore autonomo incluso qui e piu fedele al video perche puo simulare il trigger intrabar sul prezzo esatto.
