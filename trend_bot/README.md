# Trend Bot — paper (long-only, BTC + ETH)

Bot **trend/regime** che replica la logica validata sul backtest 2020-2026:
breakout **40/20** (Donchian su chiusura) con **filtro di regime SMA200**, **solo long**,
**spot**, con **compounding** (l'equity realizzata viene reinvestita nelle posizioni successive).

Gira in **paper** (nessun ordine reale): piazza posizioni simulate usando i prezzi reali
di Bitget (fallback Binance) e mostra rendimento, posizioni aperte e storico su una
**dashboard** consultabile dal telefono.

## Cosa fa a ogni giro
1. Scarica le candele giornaliere di BTCUSDT ed ETHUSDT (nessuna chiave richiesta).
2. Su ogni **nuova chiusura giornaliera**: se il prezzo rompe il massimo dei 40 giorni
   precedenti **ed** è sopra la SMA200 → entra long (paper). Se scende sotto il minimo dei
   20 giorni **oppure** sotto la SMA200 → esce.
3. Ogni simbolo ha il suo "sleeve" di capitale (metà del totale) e compone su sé stesso.
4. Aggiorna `state.json` e la dashboard `index.html`.

## Deploy su QNAP (Container Station)

Hai due strade. La più semplice è **Docker Compose**.

### Opzione A — Container Station → "Create Application" (Compose)
1. Copia la cartella `trend_bot/` sul NAS (es. in una cartella condivisa `Container/trend_bot`).
2. Apri **Container Station** → **Applications** → **Create** → incolla il contenuto di
   `docker-compose.yml`. Assicurati che il *build context* punti alla cartella `trend_bot`.
3. Avvia. La prima volta costruisce l'immagine (~30 s).
4. Apri la dashboard: **http://<indirizzo-IP-del-NAS>:8080**

### Opzione B — riga di comando (SSH sul NAS)
```
cd /share/Container/trend_bot
docker compose up -d --build
```

### Vederla dal telefono
- In rete locale: `http://<ip-del-NAS>:8080`.
- Da fuori casa: usa **myQNAPcloud** (port forwarding sulla 8080) oppure **Tailscale**
  (consigliato: niente porte aperte su internet).

## Configurazione — `config.json`
| campo | default | note |
|---|---|---|
| `symbols` | BTCUSDT, ETHUSDT | i mercati seguiti |
| `entry_n` / `exit_m` | 40 / 20 | breakout di ingresso / uscita |
| `regime_period` | 200 | SMA del filtro di regime |
| `starting_capital` | 100 | capitale paper totale (diviso tra i simboli) |
| `compound` | true | reinveste l'equity realizzata |
| `taker_fee_pct` | 0.001 | commissione spot Bitget (0,1%) |
| `poll_seconds` | 3600 | ogni quanto controlla (1 ora è più che sufficiente per il daily) |

Lo stato vive in `data/state.json` (volume montato) e sopravvive ai riavvii.
`data/index.html` è la dashboard. Endpoint dati grezzi: `http://<nas>:8080/state.json`.

## Test rapido senza Docker
```
python3 trend_bot.py --once     # un singolo giro
python3 trend_bot.py            # loop + dashboard su :8080
```

## Verso il live su Bitget (in futuro)
Questo bot è **paper**. Per il live servirebbe aggiungere l'esecuzione ordini via API
Bitget (chiavi in variabili d'ambiente, **mai** nel codice) e operare in **spot**.
Da valutare solo dopo un periodo di paper con numeri convincenti — e l'attivazione la fai tu.
