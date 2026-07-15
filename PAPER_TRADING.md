# Paper trading — guida operativa

Questa guida copre le funzioni aggiunte per rendere il bot **operativo in paper trading**
(prezzi reali, soldi finti) e per farlo girare **da remoto su GitHub**.

## Cosa fa ora

- **Verifica fedeltà al video**: ATR daily calcolato con **Wilder (ATR 14, WILDERS)** esattamente
  come mostrato nel video (prima usava una media semplice). Vedi `VIDEO_RULES.md`.
- **Motore paper live**: applica la strategia barra-per-barra (candele 5m chiuse), gestisce
  ingresso, stop, target e chiusura di sessione, con esecuzione **simulata**.
- **Dati reali crypto**: feed pubblico Binance (primario) o Bitget — **nessuna API key** per i dati.
- **Leva e fee**: leva configurabile (default 20x su crypto), prezzo di **liquidazione** simulato,
  **PnL netto** per operazione con le **fee Bitget** (taker 0,06% / maker 0,02%).
- **Capitale 100 USDT** con **pulsante di ripristino** nella dashboard.
- **Log operazioni aperte** con **profitto corrente** (unrealized) + log operazioni chiuse con PnL netto.
- **Deploy su GitHub Actions** (ogni 5 minuti) con dashboard pubblicata su GitHub Pages.

## Da dove arrivano i dati

| Modalità | Fonte dati | Serve una key? |
|---|---|---|
| `backtest`, `paper-replay` | CSV 5m che fornisci tu (`data/…csv`) | No (offline) |
| `paper-crypto` | Binance/Bitget, candele pubbliche 5m + daily per l'ATR | No |
| `paper-live` | Alpaca (azioni USA) | Sì (paper key Alpaca) |

L'ATR daily è calcolato internamente aggregando le candele in candele giornaliere: non serve un
feed separato. Nel config crypto la "sessione" è il giorno UTC (apertura 00:00), quindi l'opening
range è la prima candela da 15m del giorno e l'ATR è quello giornaliero reale a 24h.

## Comando principale: paper live su ETH (Binance, dati reali)

```bash
cd pattern_scout_bot
PYTHONPATH=src python3 -m pattern_scout.cli paper-crypto \
  --symbols ETHUSDT --exchange binance --config config.crypto.json
```

Gira 24/7, si sveglia pochi secondi dopo la chiusura di ogni candela 5m, e scrive la dashboard
in `docs/dashboard.html`. Ferma con Ctrl-C (lo stato viene salvato).

### Ripristinare il capitale a 100 USDT

```bash
PYTHONPATH=src python3 -m pattern_scout.cli paper-crypto --reset \
  --symbols ETHUSDT --config config.crypto.json
```

Nella dashboard c'è anche il pulsante **↺ Ripristina 100 USDT** (azzera la vista da quel momento).

## Prova offline immediata (senza rete)

Fai scorrere un CSV 5m nel motore paper, con dashboard a filtro daily selezionabile ON/OFF:

```bash
PYTHONPATH=src python3 -m pattern_scout.cli paper-replay \
  --data data/sample_5m.csv --config config.paper.json --symbol ETH --both --out reports/paper_both
```

## Parametri chiave (config.crypto.json)

| Campo | Valore | Significato |
|---|---|---|
| `risk.account_size` | 100 | capitale iniziale in USDT |
| `risk.leverage` | 20 | leva massima (perp) |
| `risk.sizing_mode` | `risk` | dimensiona per rischio 2% (la leva è un tetto). Metti `leverage` per usare sempre il notional pieno a 20x |
| `risk.risk_fraction` | 0.02 | rischio per trade = 2% del capitale |
| `execution.taker_fee_pct` | 0.0006 | fee taker Bitget (0,06%) |
| `execution.maker_fee_pct` | 0.0002 | fee maker Bitget (0,02%) |
| `daily_context.enabled` | false | filtro "breakout+retest" del video (solo esempio live). Attivalo per meno trade ma più selettivi |

### Gestione uscita: stop morbido → break-even → trailing → TP libero

Nel config crypto la sezione `exit_management` è in modalità `trailing`:

| Campo | Valore | Significato |
|---|---|---|
| `mode` | `trailing` | gestione dinamica (metti `fixed` per lo stop/target del video) |
| `initial_stop_atr_fraction` | 0.25 | stop iniziale **morbido**: allargato di 0,25×ATR oltre la wick, così l'entrata non fallisce sul rumore |
| `breakeven_trigger_r` | 1.0 | a **+1R** lo stop va a break-even (entrata): rischio azzerato |
| `trail_trigger_r` | 1.0 | da +1R parte il **trailing** |
| `trail_atr_fraction` | 0.6 | il trailing segue a 0,6×ATR sotto il massimo (long) / sopra il minimo (short) |
| `use_fixed_target` | false | **nessun target fisso**: il take profit è lasciato correre, si esce sul trailing (o a fine sessione) |

In pratica: entri, lo stop è largo per non essere buttato fuori subito; appena il trade va +1R lo
stop sale a pareggio (non rischi più nulla); poi lo stop insegue il prezzo per bloccare il profitto
e lasciare correre i movimenti forti. Il grafico mostra la linea di stop **attuale** (già spostata).

### Come viene calcolato il PnL netto per operazione

```
notional      = quantità × prezzo
PnL lordo     = (uscita − ingresso) × quantità        (invertito per gli short)
fee ingresso  = notional_ingresso × 0,06%  (taker, ordine a mercato)
fee uscita    = notional_uscita × 0,02% se target (maker) altrimenti 0,06% (taker)
PnL netto     = PnL lordo − fee ingresso − fee uscita
```

Con leva 20x il **prezzo di liquidazione** isolato è circa `entry × (1 − 1/20 + 0,5%)` per i long.
Se lo stop della strategia è più vicino della liquidazione (caso normale con stop stretti), protegge
prima la liquidazione. La dashboard mostra sempre il prezzo di liquidazione di ogni posizione aperta.

## Verso il live reale

Restiamo **sempre in paper** finché non decidi tu. Per passare al live serve aggiungere un broker
che piazza ordini reali (Binance/Bitget con API key): la parte dati e la logica non cambiano, si
sostituisce solo l'esecuzione simulata con quella reale. Chiedi quando vuoi farlo.
