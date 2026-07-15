# Regole estratte dal video

Fonte: analisi locale del video (autore: Doug Rumer / "Doug Squad"), rivisto in due parti
(WhatsApp Video 22.31.48 ~19,5 min + 22.32.03 ~5,4 min) fotogramma per fotogramma.

## Verifica del bot vs video (luglio 2026)

Confronto tra le regole viste a schermo e l'implementazione del bot: **corrispondenza piena**,
con **una sola correzione** applicata.

- ✅ Opening range = prima candela 15m (box su massimo/minimo). Confermato (GOOGL 15m, 03:00).
- ✅ ATR daily. A schermo (18:20, part2 00:15): **"ATR (14, WILDERS)"**. Il bot usava una media
  semplice del True Range → **corretto in Wilder** (`atr_method: "wilder"`, default). È l'unica
  discrepanza trovata.
- ✅ Manipulation se range candela ≥ 20% ATR daily. A schermo (07:40): **"$10 × 20% = $2"**;
  l'ATR di GOOGL era $10, la candela reale $8,82 (~88% ATR, "preferred").
- ✅ Passaggio a 5m per l'ingresso. Confermato (08:40 in poi).
- ✅ John Wick (hammer): ingresso alla rottura dell'estremo, stop oltre la wick (09:40–10:40).
- ✅ Power of Tower (engulfing): "Engulfing Candle" a schermo (09:20), ingresso al recupero 50%.
- ✅ Target = lato opposto dell'opening range (12:40–14:20 su GOOGL; part2 su METU, ETF legato a Meta).

Fonte originale della prima estrazione: `This 15-Minute Scalping Strategy Shouldnt Work... But It Makes Me $2,392Day.mp4`.

## Timeline

- 00:00-01:24: promessa della strategia sulla prima ora.
- 01:24-03:16: step 1, opening range candle.
- 03:16-08:30: step 2, manipulation candle.
- 08:30-14:48: step 3, entry su 5m.
- 14:48-16:28: variante con manipulation candles consecutive.
- 16:28-23:33: esempio live su strumento legato a Meta.

## Nucleo operativo

- Timeframe iniziale: 15m.
- Si aspetta la chiusura completa della prima candela 15m.
- Si boxano high e low della prima candela.
- Si controlla l'ATR sul daily.
- Se il range della candela 15m supera il 20% dell'ATR daily, il video la classifica come manipulation candle.
- L'autore preferisce flush/spike molto piu grandi, intorno al 70-80% dell'ATR daily.
- Nel live trade aggiunge un filtro di contesto daily: vecchia resistenza diventata supporto.
- Poi si passa al grafico 5m.
- Non si entra finche non appare John Wick o Power of Tower.

## John Wick

Nome usato nel video per hammer/inverted hammer.

- Long: dopo manipulation ribassista, compra alla rottura del massimo del John Wick.
- Short: dopo manipulation rialzista, vende alla rottura del minimo del John Wick.
- Lo stop va oltre l'estremo della wick.

## Power of Tower

Nome usato nel video per engulfing candle.

- Se la candela opposta e molto grande, l'autore non aspetta l'engulf completo.
- Entra quando la candela opposta recupera/perde circa il 50% della candela precedente.
- Stop sull'estremo precedente o sul low/high della giornata.

## Target

Il target primario dichiarato e il ritorno al lato opposto dell'opening range.

## Contesto daily implementato

Nel live trade l'autore usa anche un contesto daily:

- area di breakout precedente,
- vecchia resistenza diventata supporto,
- buyer atteso vicino a livello chiave.

Il bot lo traduce cosi:

- Long: cerca un breakout recente sopra il massimo di una base daily.
- Il massimo della base diventa il livello di supporto candidato.
- Il minimo dell'opening range deve retestare quel livello entro una tolleranza ATR/percentuale.
- Short: cerca un breakdown recente sotto il minimo di una base daily.
- Il minimo della base diventa resistenza candidata.
- Il massimo dell'opening range deve retestare quel livello entro una tolleranza ATR/percentuale.

Questa e una formalizzazione fedele allo spirito del video, non una citazione letterale: il video non fornisce numeri precisi per definire "base", "vicino al livello" o "supporto buono".
