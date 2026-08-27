#!/usr/bin/env python3
"""
Trend/Regime paper bot — long-only, BTC+ETH, daily breakout gated by SMA200.
Strategy validated by backtest (2020-2026): Donchian 40/20 breakout, SMA200 bull
gate, long-only. This runs it forward in PAPER (no real orders), with compounding
(realized equity is reinvested), and serves a mobile dashboard.

Stdlib only (urllib/json/http.server) so the Docker image is tiny and needs no pip.
Data: Bitget public candles (fallback Binance). No API key required for paper.

Usage:
  python3 trend_bot.py            # daemon: poll loop + dashboard on :PORT
  python3 trend_bot.py --once     # single pass (for cron), then exit
"""
import json, os, sys, time, threading, urllib.request, urllib.error
from datetime import datetime, timezone, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
# state lives in a mounted volume on the NAS (env override), else next to the code
STATE_DIR = os.environ.get("STATE_DIR", HERE)
STATE_PATH = os.path.join(STATE_DIR, CONFIG.get("state_file", "state.json"))
DASH_PATH = os.path.join(STATE_DIR, "index.html")
LOCK = threading.Lock()

def log(*a):
    print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), *a, flush=True)

# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "trend-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def fetch_daily(symbol, limit=400):
    """Return list of (date_str, close) ascending for CLOSED daily candles + the
    latest (possibly forming) price separately."""
    # Bitget v2 spot
    try:
        gran = {"1day": "1day", "4h": "4H", "1h": "1H"}.get(CONFIG["timeframe"], "1day")
        j = _get(f"https://api.bitget.com/api/v2/spot/market/candles?symbol={symbol}&granularity={gran}&limit={limit}")
        d = (j or {}).get("data") or []
        if d:
            rows = [(int(k[0]), float(k[4])) for k in d]           # (ms, close)
            rows.sort()
            return rows
    except Exception as e:
        log(f"[{symbol}] Bitget fetch error: {e}")
    # Binance fallback
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            itv = {"1day": "1d", "4h": "4h", "1h": "1h"}.get(CONFIG["timeframe"], "1d")
            arr = _get(f"{host}/api/v3/klines?symbol={symbol}&interval={itv}&limit={limit}")
            rows = [(int(k[0]), float(k[4])) for k in arr]
            rows.sort()
            return rows
        except Exception as e:
            log(f"[{symbol}] Binance fetch error ({host}): {e}")
    return []

# --------------------------------------------------------------------------- #
# strategy
# --------------------------------------------------------------------------- #
def decide(closes):
    """closes: ascending list of CLOSED-candle closes. Returns dict with signals
    computed on the last closed candle."""
    n = len(closes)
    p = CONFIG["regime_period"]; en = CONFIG["entry_n"]; ex = CONFIG["exit_m"]
    if n < max(p, en, ex) + 1:
        return None
    ma = sum(closes[-p:]) / p
    prior_high = max(closes[-en-1:-1])
    prior_low = min(closes[-ex-1:-1])
    last = closes[-1]
    return {"close": last, "ma": ma, "bull": last > ma,
            "prior_high": prior_high, "prior_low": prior_low}

# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def default_state():
    n = len(CONFIG["symbols"])
    per = CONFIG["starting_capital"] / n
    return {
        "starting_capital": CONFIG["starting_capital"],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated": None,
        "sleeves": {s: {"equity": per, "position": None, "last_acted": None,
                        "last_close": None, "bull": None}
                    for s in CONFIG["symbols"]},
        "trades": [],
    }

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            st = json.load(open(STATE_PATH))
            for s in CONFIG["symbols"]:
                st["sleeves"].setdefault(s, {"equity": CONFIG["starting_capital"]/len(CONFIG["symbols"]),
                                             "position": None, "last_acted": None,
                                             "last_close": None, "bull": None})
            return st
        except Exception as e:
            log("state load error, starting fresh:", e)
    return default_state()

def save_state(st):
    st["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE_PATH)

# --------------------------------------------------------------------------- #
# one processing pass
# --------------------------------------------------------------------------- #
def process(st):
    cost = CONFIG["taker_fee_pct"] + CONFIG["slippage_pct"]
    for sym in CONFIG["symbols"]:
        rows = fetch_daily(sym)
        if not rows:
            log(f"[{sym}] no data this pass"); continue
        today = datetime.now(timezone.utc).date()
        # closed candles = those whose day < today (UTC); last row may be forming
        closed = [(ms, c) for ms, c in rows
                  if datetime.fromtimestamp(ms/1000, timezone.utc).date() < today]
        if len(closed) < 5:
            closed = rows[:-1] if len(rows) > 1 else rows
        closes = [c for _, c in closed]
        last_ms = closed[-1][0]
        last_day = datetime.fromtimestamp(last_ms/1000, timezone.utc).date().isoformat()
        cur_price = rows[-1][1]                              # latest (forming) price
        sig = decide(closes)
        sl = st["sleeves"][sym]
        sl["last_close"] = cur_price
        if sig:
            sl["bull"] = sig["bull"]

        # mark-to-market current value for display
        pos = sl["position"]
        if pos:
            pos["current_price"] = cur_price
            pos["value"] = pos["qty"] * cur_price
            pos["unrealized_pct"] = (cur_price / pos["entry_price"] - 1) * 100

        # act only once per new daily close
        if not sig or sl.get("last_acted") == last_day:
            continue

        if pos is None:
            if sig["close"] > sig["prior_high"] and sig["bull"]:
                entry = sig["close"] * (1 + CONFIG["slippage_pct"])
                cap = sl["equity"] if CONFIG["compound"] else CONFIG["starting_capital"]/len(CONFIG["symbols"])
                qty = cap * (1 - cost) / entry
                sl["position"] = {"symbol": sym, "entry_price": entry, "qty": qty,
                                  "entry_time": last_day, "current_price": cur_price,
                                  "value": qty*cur_price, "unrealized_pct": 0.0,
                                  "cap_at_entry": cap}
                log(f"[{sym}] BUY @ {entry:.2f} qty {qty:.6f} (cap {cap:.2f})")
        else:
            if sig["close"] < sig["prior_low"] or not sig["bull"]:
                exitp = sig["close"] * (1 - CONFIG["slippage_pct"])
                proceeds = pos["qty"] * exitp * (1 - CONFIG["taker_fee_pct"])
                pnl = proceeds - pos["cap_at_entry"]
                st["trades"].append({
                    "symbol": sym, "side": "long", "entry_time": pos["entry_time"],
                    "exit_time": last_day, "entry_price": round(pos["entry_price"], 2),
                    "exit_price": round(exitp, 2), "qty": pos["qty"],
                    "pnl": round(pnl, 4), "return_pct": round(exitp/pos["entry_price"]*100-100, 2),
                    "reason": "stop/donchian" if sig["close"] < sig["prior_low"] else "regime_off",
                })
                sl["equity"] = proceeds
                sl["position"] = None
                log(f"[{sym}] SELL @ {exitp:.2f}  pnl {pnl:+.2f}  new sleeve equity {proceeds:.2f}")
        sl["last_acted"] = last_day
    save_state(st)
    render_dashboard(st)

# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
def render_dashboard(st):
    realized = sum(sl["equity"] for sl in st["sleeves"].values())
    open_val = sum((sl["position"]["value"] if sl["position"] else 0) for sl in st["sleeves"].values())
    # equity+open counts sleeve equity that is CURRENTLY invested via the position value
    total_now = 0.0
    for sl in st["sleeves"].values():
        total_now += sl["position"]["value"] if sl["position"] else sl["equity"]
    start = st["starting_capital"]
    pnl = total_now - start
    pnlpct = pnl / start * 100 if start else 0
    def rows_open():
        out = ""
        for sym, sl in st["sleeves"].items():
            p = sl["position"]
            if not p:
                reg = "rialzo" if sl.get("bull") else "ribasso"
                out += f"<tr><td>{sym}</td><td colspan=5 class=muted>flat · regime {reg} · sleeve {sl['equity']:.2f}$</td></tr>"
            else:
                cls = "g" if p["unrealized_pct"]>=0 else "b"
                out += (f"<tr><td>{sym}</td><td>{p['entry_price']:.2f}</td>"
                        f"<td>{p.get('current_price',0):.2f}</td><td>{p['qty']:.5f}</td>"
                        f"<td class={cls}>{p['unrealized_pct']:+.1f}%</td>"
                        f"<td>{p['value']:.2f}$</td></tr>")
        return out
    def rows_closed():
        out = ""
        for t in reversed(st["trades"][-100:]):
            cls = "g" if t["pnl"]>=0 else "b"
            out += (f"<tr><td>{t['symbol']}</td><td>{t['entry_time']}</td><td>{t['exit_time']}</td>"
                    f"<td>{t['entry_price']:.2f}</td><td>{t['exit_price']:.2f}</td>"
                    f"<td class={cls}>{t['pnl']:+.2f}$</td><td class={cls}>{t['return_pct']:+.1f}%</td>"
                    f"<td class=muted>{t['reason']}</td></tr>")
        return out or "<tr><td colspan=8 class=muted>Nessuna operazione chiusa ancora.</td></tr>"
    wins = [t for t in st["trades"] if t["pnl"]>0]
    wr = len(wins)/len(st["trades"])*100 if st["trades"] else 0
    html = f"""<!doctype html><html lang=it><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60>
<title>Trend Bot — Paper</title><style>
:root{{color-scheme:light dark}}*{{box-sizing:border-box}}
body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;padding:16px;max-width:900px;margin:auto}}
h1{{font-size:20px;margin:0 0 2px}}.sub{{color:#888;font-size:13px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
.card{{border:1px solid #8884;border-radius:12px;padding:12px}}
.label{{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em}}
.val{{font-size:22px;font-weight:800;margin-top:6px;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:18px}}
th,td{{padding:8px 10px;border-bottom:1px solid #8883;text-align:left;white-space:nowrap}}
th{{color:#888;font-weight:600}}.muted{{color:#888}}.g{{color:#0a8f55}}.b{{color:#c23b32}}
@media(max-width:640px){{.grid{{grid-template-columns:repeat(2,1fr)}}table{{display:block;overflow-x:auto}}}}
</style></head><body>
<h1>🟢 Trend Bot — Paper (long-only, BTC+ETH)</h1>
<div class=sub>Breakout {CONFIG['entry_n']}/{CONFIG['exit_m']} · gate SMA{CONFIG['regime_period']} · compounding {'ON' if CONFIG['compound'] else 'OFF'} · agg. {st.get('updated','')}</div>
<div class=grid>
  <div class=card><div class=label>Capitale iniziale</div><div class=val>{start:.0f}$</div></div>
  <div class=card><div class=label>Equity attuale</div><div class=val>{total_now:.2f}$</div></div>
  <div class=card><div class=label>PnL totale</div><div class="val {'g' if pnl>=0 else 'b'}">{pnl:+.2f}$ <small>({pnlpct:+.1f}%)</small></div></div>
  <div class=card><div class=label>Win rate</div><div class=val>{wr:.0f}% <small>({len(wins)}/{len(st['trades'])})</small></div></div>
</div>
<h3>Posizioni aperte</h3>
<table><thead><tr><th>Simbolo</th><th>Entry</th><th>Prezzo ora</th><th>Qty</th><th>Non realizzato</th><th>Valore</th></tr></thead>
<tbody>{rows_open()}</tbody></table>
<h3>Operazioni chiuse</h3>
<table><thead><tr><th>Simbolo</th><th>Entry</th><th>Exit</th><th>Prezzo entry</th><th>Prezzo exit</th><th>PnL</th><th>Rend.</th><th>Uscita</th></tr></thead>
<tbody>{rows_closed()}</tbody></table>
<div class=sub>Bot paper — nessun ordine reale. Strategia trend/regime validata su backtest 2020-2026.</div>
</body></html>"""
    tmp = DASH_PATH + ".tmp"
    open(tmp, "w").write(html)
    os.replace(tmp, DASH_PATH)

# --------------------------------------------------------------------------- #
# http server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path in ("/state.json", "/state"):
            body = open(STATE_PATH, "rb").read() if os.path.exists(STATE_PATH) else b"{}"
            ctype = "application/json"
        else:
            body = open(DASH_PATH, "rb").read() if os.path.exists(DASH_PATH) else b"<h1>avvio...</h1>"
            ctype = "text/html; charset=utf-8"
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

def serve():
    port = int(os.environ.get("HTTP_PORT", CONFIG.get("http_port", 8080)))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()

# --------------------------------------------------------------------------- #
def main():
    once = "--once" in sys.argv
    st = load_state()
    if not os.path.exists(DASH_PATH):
        render_dashboard(st)
    if once:
        with LOCK:
            process(st)
        return
    threading.Thread(target=serve, daemon=True).start()
    log(f"dashboard su http://0.0.0.0:{CONFIG.get('http_port',8080)}  | poll ogni {CONFIG['poll_seconds']}s")
    while True:
        try:
            with LOCK:
                st = load_state(); process(st)
        except Exception as e:
            log("pass error:", e)
        time.sleep(CONFIG["poll_seconds"])

if __name__ == "__main__":
    main()
