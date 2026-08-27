#!/usr/bin/env python3
"""Backtest of the video's thesis: long-only breakout gated by a bull-regime filter.
Daily BTC (proxy for the video's 4h), close-based (data feed only gives close).
Realistic costs: commission 0.05%/side + slippage. Compared vs buy&hold."""
import json, glob, math, os

HERE = os.path.dirname(os.path.abspath(__file__))
COMMISSION = 0.0005   # 0.05% per side (video's assumption)
SLIPPAGE   = 0.0005   # 0.05% per side
ROUNDTRIP_COST = 2 * (COMMISSION + SLIPPAGE)  # applied on entry+exit

# ---- load & sanity-check ----
rows = {}
for f in glob.glob(os.path.join(HERE, "btc_*.json")):
    for r in json.load(open(f)):
        rows[r["date"]] = float(r["price"])
data = sorted(rows.items())                       # [(date, close), ...] ascending
dates = [d for d, _ in data]
close = [c for _, c in data]
n = len(close)

# integrity checks
from datetime import date
def pdate(s): y,m,d = map(int, s.split("-")); return date(y,m,d)
gaps = [ (pdate(dates[i]) - pdate(dates[i-1])).days for i in range(1, n) ]
assert all(g >= 1 for g in gaps), "date non ordinate"
maxgap = max(gaps)
assert all(c > 0 for c in close), "prezzo non positivo"
maxjump = max(abs(close[i]/close[i-1]-1) for i in range(1, n))
print(f"Dati: {n} giorni  {dates[0]} -> {dates[-1]}  | gap max {maxgap}g | salto giornaliero max {maxjump*100:.1f}%")

def sma(series, i, p):
    if i+1 < p: return None
    return sum(series[i-p+1:i+1]) / p

def run(entry_n=20, exit_m=10, regime_p=200, regime_type="sma"):
    """Long-only. Enter when close > max(prev entry_n closes) AND close > MA(regime_p).
       Exit when close < min(prev exit_m closes) OR close < MA(regime_p)."""
    equity = 1.0
    in_pos = False
    eq_curve = []
    trades = []            # realized returns per trade (net of costs)
    entry_price = None
    daily_rets = []
    for i in range(n):
        # mark-to-market first (return from yesterday to today if in position)
        if in_pos and i > 0:
            r = close[i]/close[i-1] - 1
            equity *= (1 + r)
            daily_rets.append(r)
        else:
            daily_rets.append(0.0)
        eq_curve.append(equity)

        ma = sma(close, i, regime_p)
        if ma is None:
            continue
        # signals use only info up to today's close
        prior_high = max(close[i-entry_n:i]) if i >= entry_n else None
        prior_low  = min(close[i-exit_m:i])  if i >= exit_m  else None
        bull = close[i] > ma

        if not in_pos:
            if prior_high is not None and close[i] > prior_high and bull:
                in_pos = True
                entry_price = close[i]
                equity *= (1 - (COMMISSION + SLIPPAGE))   # entry cost
        else:
            exit_sig = (prior_low is not None and close[i] < prior_low) or (not bull)
            if exit_sig:
                equity *= (1 - (COMMISSION + SLIPPAGE))   # exit cost
                in_pos = False
                gross = close[i]/entry_price - 1
                net = (close[i]/entry_price) * (1 - ROUNDTRIP_COST) - 1
                trades.append(net)
                entry_price = None

    # if still open at end, close at last price
    if in_pos:
        equity *= (1 - (COMMISSION + SLIPPAGE))
        net = (close[-1]/entry_price) * (1 - ROUNDTRIP_COST) - 1
        trades.append(net)

    # metrics
    total_ret = equity - 1
    days = n
    cagr = equity**(365.0/days) - 1
    peak = -1; maxdd = 0
    for e in eq_curve:
        peak = max(peak, e)
        maxdd = max(maxdd, (peak - e)/peak)
    wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float('inf')
    win_rate = len(wins)/len(trades) if trades else 0
    mu = sum(daily_rets)/len(daily_rets)
    sd = (sum((r-mu)**2 for r in daily_rets)/len(daily_rets))**0.5
    sharpe = (mu/sd*math.sqrt(365)) if sd > 0 else 0
    time_in = sum(1 for r in daily_rets if r != 0)/len(daily_rets)
    return dict(total=total_ret, cagr=cagr, maxdd=maxdd, pf=pf, trades=len(trades),
                win=win_rate, sharpe=sharpe, tim=time_in)

# buy & hold
bh_total = close[-1]/close[0] - 1
bh_cagr = (close[-1]/close[0])**(365.0/n) - 1
peak=-1; bh_dd=0
for c in close:
    peak=max(peak,c); bh_dd=max(bh_dd,(peak-c)/peak)
print(f"\nBUY & HOLD: ritorno {bh_total*100:,.0f}%  | CAGR {bh_cagr*100:.0f}%  | maxDD {bh_dd*100:.0f}%")

print("\nStrategia video (breakout long-only + gate regime), varianti:")
print(f"{'entry/exit/gate':<22}{'ritorno':>10}{'CAGR':>8}{'maxDD':>8}{'PF':>7}{'trade':>7}{'win%':>7}{'Sharpe':>8}{'inMkt%':>8}")
for en, ex, gp, gt in [(20,10,200,'sma'),(20,10,100,'sma'),(40,20,200,'sma'),(55,20,200,'sma'),(20,10,50,'sma')]:
    r = run(en, ex, gp, gt)
    label = f"{en}/{ex}/{gt}{gp}"
    pf = "inf" if r['pf']==float('inf') else f"{r['pf']:.2f}"
    print(f"{label:<22}{r['total']*100:>9.0f}%{r['cagr']*100:>7.0f}%{r['maxdd']*100:>7.0f}%{pf:>7}{r['trades']:>7}{r['win']*100:>6.0f}%{r['sharpe']:>8.2f}{r['tim']*100:>7.0f}%")

# ---- robustness grid: quanti settaggi restano profittevoli? ----
grid = []
for en in (15,20,30,40,55):
    for ex in (10,20):
        for gp in (100,200):
            r = run(en, ex, gp, 'sma')
            grid.append(r)
prof = sum(1 for r in grid if r['total']>0)
pf_ok = sum(1 for r in grid if r['pf']>1.3)
dd_ok = sum(1 for r in grid if r['maxdd']<0.55)
import statistics as st
print(f"\nRobustezza su {len(grid)} combinazioni di parametri:")
print(f"  profittevoli: {prof}/{len(grid)} | PF>1.3: {pf_ok}/{len(grid)} | maxDD<55%: {dd_ok}/{len(grid)}")
print(f"  ritorno mediano {st.median(r['total'] for r in grid)*100:.0f}% | Sharpe mediano {st.median(r['sharpe'] for r in grid):.2f} | maxDD mediano {st.median(r['maxdd'] for r in grid)*100:.0f}%")

# ---- comportamento anno per anno (config 40/20/sma200) vs buy&hold ----
def run_year(year, entry_n=40, exit_m=20, regime_p=200):
    # slice with 200d warmup before Jan 1
    idx = [i for i,d in enumerate(dates) if d[:4]==str(year)]
    if not idx: return None
    s = max(0, idx[0]-regime_p)
    sub_close = close[s:idx[-1]+1]
    # local run on the slice
    equity=1.0; in_pos=False; entry=None
    for i in range(len(sub_close)):
        if in_pos and i>0: equity*= sub_close[i]/sub_close[i-1]
        if i+1<regime_p: continue
        ma=sum(sub_close[i-regime_p+1:i+1])/regime_p
        ph=max(sub_close[i-entry_n:i]) if i>=entry_n else None
        pl=min(sub_close[i-exit_m:i]) if i>=exit_m else None
        bull=sub_close[i]>ma
        # only count P&L within the target year
        if not in_pos and ph and sub_close[i]>ph and bull:
            in_pos=True; equity*= (1-(COMMISSION+SLIPPAGE))
        elif in_pos and ((pl and sub_close[i]<pl) or not bull):
            in_pos=False; equity*= (1-(COMMISSION+SLIPPAGE))
    # restrict equity to calendar year: recompute only over year indices
    y0=idx[0];
    yeq=1.0; inp=False
    for i in range(idx[0], idx[-1]+1):
        if inp and i>0: yeq*= close[i]/close[i-1]
        ma=sma(close,i,regime_p)
        if ma is None: continue
        ph=max(close[i-entry_n:i]) if i>=entry_n else None
        pl=min(close[i-exit_m:i]) if i>=exit_m else None
        bull=close[i]>ma
        if not inp and ph and close[i]>ph and bull:
            inp=True; yeq*=(1-(COMMISSION+SLIPPAGE))
        elif inp and ((pl and close[i]<pl) or not bull):
            inp=False; yeq*=(1-(COMMISSION+SLIPPAGE))
    bh = close[idx[-1]]/close[idx[0]-1] - 1 if idx[0]>0 else close[idx[-1]]/close[idx[0]]-1
    return yeq-1, bh

print("\nAnno per anno (strategia 40/20/SMA200 vs buy&hold):")
print(f"{'anno':<8}{'strategia':>12}{'buy&hold':>12}")
for y in range(2020,2027):
    res=run_year(y)
    if res: print(f"{y:<8}{res[0]*100:>11.0f}%{res[1]*100:>11.0f}%")
