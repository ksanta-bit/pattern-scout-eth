from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


def build_dashboard(reports_dir: str | Path, output_path: str | Path | None = None) -> Path:
    reports = Path(reports_dir)
    if output_path is None:
        output = reports / "dashboard.html"
    else:
        output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    summary = _read_json(reports / "summary.json")
    trades = _read_csv_records(reports / "trades.csv")
    equity = _read_csv_records(reports / "equity_curve.csv")
    annotated = _read_annotated_sample(reports / "annotated_candles.csv")

    payload = {
        "meta": {
            "reports_dir": str(reports),
            "is_demo": reports.name == "sample",
        },
        "summary": summary,
        "trades": trades,
        "equity": equity,
        "annotated": annotated,
    }
    output.write_text(_render_dashboard(payload), encoding="utf-8")
    return output


def build_compare_dashboard(variants: list[dict], output_path: str | Path,
                            title: str = "Pattern Scout — Paper (filtro daily selezionabile)") -> Path:
    """Build a single dashboard that lets the user switch between strategy
    variants (e.g. daily-context filter OFF vs ON) from a dropdown.

    ``variants`` = list of {"name": str, "summary": dict, "trades": list, "equity": list}.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"title": title, "variants": [_json_safe(v) for v in variants]}
    output.write_text(_render_compare(payload), encoding="utf-8")
    return output


def variant_from_reports(name: str, reports_dir: str | Path) -> dict:
    reports = Path(reports_dir)
    return {
        "name": name,
        "summary": _read_json(reports / "summary.json"),
        "trades": _read_csv_records(reports / "trades.csv"),
        "equity": _read_csv_records(reports / "equity_curve.csv"),
    }


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv_records(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    frame = pd.read_csv(path)
    return frame.replace({pd.NA: None}).where(pd.notna(frame), None).to_dict("records")


def _read_annotated_sample(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    cols = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "session",
        "is_manipulation_session",
        "daily_context_valid",
        "daily_context_level",
        "daily_context_kind",
        "atr_fraction",
        "opening_high",
        "opening_low",
    ]
    frame = pd.read_csv(path, usecols=lambda col: col in cols)
    if frame.empty:
        return []
    manip = frame["is_manipulation_session"].astype(bool) if "is_manipulation_session" in frame.columns \
        else pd.Series(False, index=frame.index)
    ctx = frame["daily_context_valid"].astype(bool) if "daily_context_valid" in frame.columns \
        else pd.Series(False, index=frame.index)
    interesting = frame[manip | ctx].copy()
    if interesting.empty:
        interesting = frame.tail(300).copy()
    elif len(interesting) > 600:
        interesting = interesting.tail(600).copy()
    return interesting.replace({pd.NA: None}).where(pd.notna(interesting), None).to_dict("records")


def _render_dashboard(payload: dict) -> str:
    data = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False)
    html = """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pattern Scout Dashboard</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: Canvas;
      --fg: CanvasText;
      --muted: color-mix(in srgb, CanvasText 62%, Canvas 38%);
      --card: color-mix(in srgb, Canvas 94%, CanvasText 6%);
      --border: color-mix(in srgb, CanvasText 18%, Canvas 82%);
      --accent: #0a7cff;
      --good: #0a8f55;
      --bad: #c23b32;
      --warn: #b77900;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--fg);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.35;
    }
    main {
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 24px 0 40px;
    }
    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 600;
      letter-spacing: 0;
    }
    .subtitle {
      margin-top: 4px;
      color: var(--muted);
      font-size: 14px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--card);
      padding: 14px;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .value {
      margin-top: 6px;
      font-size: 26px;
      font-weight: 600;
      letter-spacing: 0;
    }
    .value.good { color: var(--good); }
    .value.bad { color: var(--bad); }
    .section {
      margin-top: 16px;
    }
    .notice {
      display: none;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: color-mix(in srgb, var(--warn) 12%, var(--card) 88%);
      padding: 12px 14px;
      margin-bottom: 16px;
      color: var(--fg);
      font-size: 14px;
    }
    .notice.is-visible {
      display: block;
    }
    .section h2 {
      margin: 0 0 10px;
      font-size: 16px;
      font-weight: 600;
    }
    .chart-wrap {
      height: 280px;
      padding: 10px;
    }
    svg {
      display: block;
      width: 100%;
      height: 100%;
    }
    .axis, .grid-line {
      stroke: var(--border);
      stroke-width: 1;
    }
    .line {
      fill: none;
      stroke: var(--accent);
      stroke-width: 2;
    }
    .zero {
      stroke: var(--muted);
      stroke-width: 1;
      stroke-dasharray: 4 4;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
    }
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }
    table {
      width: 100%;
      min-width: 860px;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      white-space: nowrap;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      background: color-mix(in srgb, var(--card) 80%, var(--bg) 20%);
    }
    tr:last-child td { border-bottom: 0; }
    .pill {
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
    }
    .pill.target { color: var(--good); }
    .pill.stop { color: var(--bad); }
    .pill.session_close { color: var(--warn); }
    .split {
      display: grid;
      grid-template-columns: 1.3fr .7fr;
      gap: 12px;
      align-items: start;
    }
    .mini-list {
      display: grid;
      gap: 8px;
    }
    .mini-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 8px;
      font-size: 13px;
    }
    .mini-row:last-child { border-bottom: 0; padding-bottom: 0; }
    @media (max-width: 760px) {
      main { width: min(100% - 20px, 1180px); padding-top: 16px; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr; }
      .value { font-size: 22px; }
    }
    @media (max-width: 460px) {
      .grid { grid-template-columns: 1fr; }
      h1 { font-size: 20px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Pattern Scout Dashboard</h1>
        <div class="subtitle" id="subtitle">Report backtest</div>
      </div>
    </header>

    <section class="notice" id="demoNotice">
      Stai guardando la demo sintetica. Serve solo a verificare che bot, report e dashboard funzionino; non misura la strategia su dati reali.
    </section>

    <section class="grid" aria-label="metriche principali">
      <div class="card"><div class="label">Trade</div><div class="value" id="totalTrades">0</div></div>
      <div class="card"><div class="label">Win rate</div><div class="value" id="winRate">0%</div></div>
      <div class="card"><div class="label">PnL totale</div><div class="value" id="totalPnl">0</div></div>
      <div class="card"><div class="label">Avg R</div><div class="value" id="avgR">0</div></div>
    </section>

    <section class="split">
      <div class="section card chart-wrap">
        <h2>Equity curve</h2>
        <svg id="equityChart" role="img" aria-label="equity curve"></svg>
      </div>
      <div class="section card">
        <h2>Diagnostica setup</h2>
        <div class="mini-list" id="diagnostics"></div>
      </div>
    </section>

    <section class="section">
      <h2>Trade</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Sessione</th>
              <th>Lato</th>
              <th>Segnale</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>R</th>
              <th>PnL</th>
              <th>Uscita</th>
              <th>Contesto daily</th>
            </tr>
          </thead>
          <tbody id="tradeRows"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script id="payload" type="application/json">__PATTERN_SCOUT_PAYLOAD__</script>
  <script>
    const payload = JSON.parse(document.getElementById('payload').textContent);
    const summary = payload.summary || {};
    const trades = payload.trades || [];
    const equity = payload.equity || [];
    const annotated = payload.annotated || [];
    const meta = payload.meta || {};

    const fmt = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2 });
    const money = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
    const pct = (v) => `${fmt.format((Number(v) || 0) * 100)}%`;
    const num = (v) => fmt.format(Number(v) || 0);

    document.getElementById('totalTrades').textContent = summary.total_trades ?? trades.length;
    document.getElementById('winRate').textContent = pct(summary.win_rate);
    document.getElementById('totalPnl').textContent = money.format(Number(summary.total_pnl) || 0);
    document.getElementById('avgR').textContent = num(summary.avg_r);
    document.getElementById('totalPnl').classList.toggle('good', Number(summary.total_pnl) > 0);
    document.getElementById('totalPnl').classList.toggle('bad', Number(summary.total_pnl) < 0);
    document.getElementById('subtitle').textContent = meta.reports_dir ? `Report: ${meta.reports_dir}` : 'Report backtest';
    document.getElementById('demoNotice').classList.toggle('is-visible', Boolean(meta.is_demo));

    const sessions = new Set(trades.map(t => t.session));
    const contextKinds = new Set(trades.map(t => t.daily_context_kind).filter(Boolean));
    const manipulationSessions = new Set(
      annotated
        .filter(r => String(r.is_manipulation_session).toLowerCase() === 'true')
        .map(r => r.session)
        .filter(Boolean)
    );
    const dailyContextSessions = new Set(
      annotated
        .filter(r => String(r.daily_context_valid).toLowerCase() === 'true')
        .map(r => r.session)
        .filter(Boolean)
    );
    const exits = trades.reduce((acc, t) => {
      const key = t.exit_reason || 'unknown';
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    const diagnostics = [
      ['Sessioni tradate', sessions.size],
      ['Pattern daily', contextKinds.size ? Array.from(contextKinds).join(', ') : 'nessuno'],
      ['Sessioni manipulation', manipulationSessions.size],
      ['Sessioni con contesto daily', dailyContextSessions.size],
      ['Exit target', exits.target || 0],
      ['Exit stop', exits.stop || 0],
    ];
    document.getElementById('diagnostics').innerHTML = diagnostics.map(([label, value]) =>
      `<div class="mini-row"><span>${label}</span><strong>${value}</strong></div>`
    ).join('');

    const rows = trades.map(t => `
      <tr>
        <td>${t.session ?? ''}</td>
        <td>${t.side ?? ''}</td>
        <td>${t.signal_type ?? ''}</td>
        <td>${money.format(Number(t.entry_price) || 0)}</td>
        <td>${money.format(Number(t.exit_price) || 0)}</td>
        <td>${num(t.r_multiple)}</td>
        <td>${money.format(Number(t.pnl) || 0)}</td>
        <td><span class="pill ${t.exit_reason ?? ''}">${t.exit_reason ?? ''}</span></td>
        <td>${t.daily_context_kind ? `${t.daily_context_kind} @ ${num(t.daily_context_level)}` : ''}</td>
      </tr>
    `).join('');
    document.getElementById('tradeRows').innerHTML = rows || '<tr><td colspan="9" class="empty">Nessun trade nel report.</td></tr>';

    function drawEquity() {
      const svg = document.getElementById('equityChart');
      svg.innerHTML = '';
      const w = svg.clientWidth || 700;
      const h = svg.clientHeight || 220;
      const pad = { left: 48, right: 12, top: 18, bottom: 32 };
      const values = equity.map((d, i) => ({ x: i, y: Number(d.equity ?? d.pnl ?? 0) })).filter(d => Number.isFinite(d.y));
      if (!values.length) {
        svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="currentColor">Nessuna equity curve disponibile</text>';
        return;
      }
      const minY = Math.min(0, ...values.map(d => d.y));
      const maxY = Math.max(0, ...values.map(d => d.y));
      const spanY = maxY - minY || 1;
      const xScale = (x) => values.length === 1
        ? pad.left + (w - pad.left - pad.right) / 2
        : pad.left + (x / Math.max(1, values.length - 1)) * (w - pad.left - pad.right);
      const yScale = (y) => pad.top + (1 - ((y - minY) / spanY)) * (h - pad.top - pad.bottom);
      const path = values.map((d, i) => `${i ? 'L' : 'M'}${xScale(d.x).toFixed(1)},${yScale(d.y).toFixed(1)}`).join(' ');
      const points = values.map(d => `<circle cx="${xScale(d.x).toFixed(1)}" cy="${yScale(d.y).toFixed(1)}" r="4" fill="var(--accent)"></circle>`).join('');
      const zeroY = yScale(0);
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      svg.innerHTML = `
        <line class="axis" x1="${pad.left}" y1="${h - pad.bottom}" x2="${w - pad.right}" y2="${h - pad.bottom}"></line>
        <line class="axis" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${h - pad.bottom}"></line>
        <line class="zero" x1="${pad.left}" y1="${zeroY}" x2="${w - pad.right}" y2="${zeroY}"></line>
        <path class="line" d="${path}"></path>
        ${points}
        <text x="${pad.left}" y="${pad.top + 2}" fill="currentColor" font-size="12">${money.format(maxY)}</text>
        <text x="${pad.left}" y="${h - 8}" fill="currentColor" font-size="12">${money.format(minY)}</text>
        <text x="${w - pad.right}" y="${h - 8}" text-anchor="end" fill="currentColor" font-size="12">${values.length} trade</text>
      `;
    }
    drawEquity();
    window.addEventListener('resize', drawEquity);
  </script>
</body>
</html>
"""
    return html.replace("__PATTERN_SCOUT_PAYLOAD__", data)


def build_crypto_dashboard(output_path: str | Path, starting_capital: float,
                           variants: dict, default_variant: str = "off",
                           chart_symbol: str | None = None,
                           chart_candles: list | None = None, bot_log: list | None = None) -> Path:
    """Interactive paper dashboard with a toggle button that switches between the
    two strategy variants (daily filter OFF / ON): 1-minute candlestick chart with
    entry/stop/target segments, reset button, open/closed logs and a bot log."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    import datetime as _dt
    payload = _json_safe({
        "starting_capital": starting_capital,
        "variants": variants,             # {"off": {...}, "on": {...}}
        "default_variant": default_variant,
        "chart_symbol": chart_symbol or "",
        "candles": chart_candles or [],
        "bot_log": bot_log or [],
        "updated": _dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    })
    output.write_text(_render_crypto(payload), encoding="utf-8")
    return output


def _render_crypto(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    html = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pattern Scout — Paper ETH</title>
<style>
 :root{color-scheme:light dark;--bg:Canvas;--fg:CanvasText;
  --muted:color-mix(in srgb,CanvasText 62%,Canvas 38%);
  --card:color-mix(in srgb,Canvas 94%,CanvasText 6%);
  --border:color-mix(in srgb,CanvasText 18%,Canvas 82%);
  --accent:#0a7cff;--good:#0a8f55;--bad:#c23b32;--warn:#b77900;}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.35}
 main{width:min(1100px,calc(100% - 32px));margin:0 auto;padding:22px 0 40px}
 header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:6px}
 h1{margin:0;font-size:22px;font-weight:600}
 .sub{color:var(--muted);font-size:13px;margin-bottom:16px}
 button{font-size:14px;padding:9px 14px;border-radius:8px;border:1px solid var(--border);
  background:var(--accent);color:#fff;cursor:pointer;font-weight:600}
 button.secondary{background:var(--card);color:var(--fg)}
 .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
 .card{border:1px solid var(--border);border-radius:10px;background:var(--card);padding:14px}
 .label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
 .value{margin-top:6px;font-size:24px;font-weight:700}
 .value.good{color:var(--good)}.value.bad{color:var(--bad)}
 h2{font-size:15px;margin:20px 0 8px}
 .table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:10px}
 table{width:100%;min-width:720px;border-collapse:collapse;font-size:13px}
 th,td{padding:9px 11px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
 th{color:var(--muted);font-weight:600;background:color-mix(in srgb,var(--card) 80%,var(--bg) 20%)}
 tr:last-child td{border-bottom:0}
 .pill{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:2px 8px;font-size:12px}
 .pill.target,.g{color:var(--good)}.pill.stop,.pill.liquidation,.b{color:var(--bad)}
 .pill.session_close{color:var(--warn)}
 .empty{color:var(--muted);padding:16px;text-align:center}
 .live{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:6px;
  animation:pulse 1.6s infinite}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
 #chart{width:100%;height:420px;border:1px solid var(--border);border-radius:10px;position:relative}
 .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:8px 2px 0}
 .legend b{color:var(--fg)}
 .dot{display:inline-block;width:10px;height:2px;vertical-align:middle;margin-right:4px}
 .side-tag{font-weight:700;padding:2px 8px;border-radius:6px;font-size:12px}
 .side-tag.long{background:color-mix(in srgb,var(--good) 22%,var(--card));color:var(--good)}
 .side-tag.short{background:color-mix(in srgb,var(--bad) 22%,var(--card));color:var(--bad)}
 @media(max-width:720px){.grid{grid-template-columns:repeat(2,1fr)}#chart{height:320px}}
</style>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
</head><body><main>
 <header>
   <div><h1><span class="live"></span>Pattern Scout — Paper ETH</h1>
     <div class="sub" id="filterStatus"></div></div>
   <div style="display:flex;gap:8px;flex-wrap:wrap">
     <button id="filterToggle" title="Attiva/disattiva il filtro daily (breakout+retest)">Filtro daily: —</button>
     <button id="resetBtn" class="secondary" title="Riporta il capitale visualizzato a 100 USDT">↺ Ripristina 100 USDT</button>
   </div>
 </header>
 <div class="sub" id="updated"></div>
 <section class="grid">
   <div class="card"><div class="label">Capitale iniziale</div><div class="value" id="cap">100</div></div>
   <div class="card"><div class="label">Equity (realizzato)</div><div class="value" id="eq">100</div></div>
   <div class="card"><div class="label">Equity + aperte</div><div class="value" id="eqU">100</div></div>
   <div class="card"><div class="label">PnL totale</div><div class="value" id="pnl">0</div></div>
 </section>
 <h2 id="chartTitle">Grafico 1 minuto</h2>
 <div id="chart"></div>
 <div class="legend" id="legend">
   <span><span class="dot" style="background:#2962ff"></span>Entry</span>
   <span><span class="dot" style="background:#e5393b"></span>Stop loss</span>
   <span><span class="dot" style="background:#26a65b"></span>Take profit</span>
 </div>
 <h2>Operazioni aperte <span class="sub" id="openCount"></span></h2>
 <div class="table-wrap"><table><thead><tr>
   <th>Simbolo</th><th>Lato</th><th>Entry</th><th>Prezzo ora</th><th>Qty</th><th>Leva</th>
   <th>Liquidazione</th><th>Profitto ora</th><th>R</th></tr></thead>
   <tbody id="openRows"></tbody></table></div>
 <h2>Operazioni chiuse</h2>
 <div class="table-wrap"><table><thead><tr>
   <th>Sessione</th><th>Simbolo</th><th>Segnale</th><th>Entry</th><th>Exit</th>
   <th>Lordo</th><th>Fee</th><th>PnL netto</th><th>R</th><th>Uscita</th></tr></thead>
   <tbody id="closedRows"></tbody></table></div>
 <h2>Log del bot <span class="sub">(ultimo giro)</span></h2>
 <pre id="botLog" style="background:var(--card);border:1px solid var(--border);border-radius:10px;
   padding:12px;overflow:auto;max-height:220px;font-size:12px;white-space:pre-wrap;margin:0"></pre>
 <div class="sub" style="margin-top:14px">
   Il pulsante ripristina il capitale <em>visualizzato</em> a 100 USDT da questo momento (baseline locale).
   Per azzerare davvero lo storico sul server: esegui <code>paper-crypto --reset</code> in locale
   oppure lancia il workflow GitHub con l'opzione <code>reset</code>.
   Il <strong>filtro daily</strong> (breakout+retest) si attiva/disattiva col workflow (input
   <code>daily_filter</code>) o cambiando <code>daily_context.enabled</code> in <code>config.crypto.json</code>.
 </div>
</main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
 const p=JSON.parse(document.getElementById('payload').textContent);
 const money=new Intl.NumberFormat('it-IT',{maximumFractionDigits:2,minimumFractionDigits:2});
 const num=new Intl.NumberFormat('it-IT',{maximumFractionDigits:2});
 const q4=new Intl.NumberFormat('it-IT',{maximumFractionDigits:4});
 const cap=Number(p.starting_capital)||100;
 const V=p.variants||{off:{},on:{}};
 let cur=localStorage.getItem('ps_variant')||(p.default_variant||'off');
 if(!V[cur])cur=(V.off?'off':'on');
 let closed=[],opens=[];
 function loadVariant(){const d=V[cur]||{};closed=(d.closed||[]);opens=(d.open||[]);}
 loadVariant();
 document.getElementById('updated').textContent='Ultimo aggiornamento: '+(p.updated||new Date().toLocaleString('it-IT'));
 function syncFilterUI(){
   const on=(cur==='on');
   const btn=document.getElementById('filterToggle');
   if(btn){btn.textContent='Filtro daily: '+(on?'ATTIVO':'DISATTIVO');btn.style.background=on?'var(--good)':'var(--card)';btn.style.color=on?'#fff':'var(--fg)';}
   const fs=document.getElementById('filterStatus');
   if(fs)fs.innerHTML='Vista: <strong>'+(on?'CON filtro daily (breakout+retest)':'SENZA filtro daily — nucleo del video')+'</strong>';
 }
 // Bot log (what the strategy decided on the last run)
 (function(){const bl=document.getElementById('botLog');if(bl){
   const lines=(p.bot_log||[]);
   bl.textContent=lines.length?lines.join('\\n'):'Nessun evento nell\\'ultimo giro (in attesa di dati o di un setup).';}})();

 function baseKey(){return 'psbase_'+cur;}
 function baseline(){const v=localStorage.getItem(baseKey());return v?JSON.parse(v):{count:0,capital:cap};}
 function applyBaseline(){
   const b=baseline();
   const shown=closed.slice(b.count);            // trade dopo il reset
   const realized=shown.reduce((a,t)=>a+(Number(t.pnl)||0),0);
   const unreal=opens.reduce((a,t)=>a+(Number(t.unrealized_pnl)||0),0);
   const eq=b.capital+realized;
   document.getElementById('cap').textContent=money.format(b.capital);
   document.getElementById('eq').textContent=money.format(eq);
   const eqU=document.getElementById('eqU');eqU.textContent=money.format(eq+unreal);
   eqU.className='value '+((eq+unreal)>=b.capital?'good':'bad');
   const tp=document.getElementById('pnl');tp.textContent=money.format(realized);
   tp.className='value '+(realized>0?'good':realized<0?'bad':'');
   document.getElementById('openCount').textContent=opens.length?`(${opens.length} live)`:'';
   document.getElementById('openRows').innerHTML=opens.map(t=>{
     const u=Number(t.unrealized_pnl)||0;
     return `<tr><td>${t.symbol||''}</td><td>${t.side||''}</td>
      <td>${money.format(Number(t.entry_price)||0)}</td>
      <td>${t.current_price!=null?money.format(t.current_price):'—'}</td>
      <td>${q4.format(Number(t.quantity)||0)}</td><td>${num.format(Number(t.leverage)||1)}x</td>
      <td class="b">${t.liquidation_price!=null?money.format(t.liquidation_price):'—'}</td>
      <td class="${u>=0?'g':'b'}">${money.format(u)}</td>
      <td>${num.format(Number(t.unrealized_r)||0)}</td></tr>`;}).join('')
      ||'<tr><td colspan="9" class="empty">Nessuna operazione aperta.</td></tr>';
   const list=shown.slice().reverse();
   document.getElementById('closedRows').innerHTML=list.map(t=>`<tr>
      <td>${t.session||''}</td><td>${t.symbol||''}</td><td>${t.signal_type||''}</td>
      <td>${money.format(Number(t.entry_price)||0)}</td><td>${money.format(Number(t.exit_price)||0)}</td>
      <td>${money.format(Number(t.gross_pnl)||0)}</td><td class="b">${money.format(Number(t.fees)||0)}</td>
      <td class="${(Number(t.pnl)||0)>=0?'g':'b'}">${money.format(Number(t.pnl)||0)}</td>
      <td>${num.format(Number(t.r_multiple)||0)}</td>
      <td><span class="pill ${t.exit_reason||''}">${t.exit_reason||''}</span></td></tr>`).join('')
      ||'<tr><td colspan="10" class="empty">Nessuna operazione chiusa dal reset.</td></tr>';
 }
 document.getElementById('resetBtn').addEventListener('click',()=>{
   localStorage.setItem(baseKey(),JSON.stringify({count:closed.length,capital:cap}));
   applyBaseline();
 });
 document.getElementById('filterToggle').addEventListener('click',()=>{
   cur=(cur==='on')?'off':'on';
   localStorage.setItem('ps_variant',cur);
   loadVariant();syncFilterUI();applyBaseline();
   if(window.__redrawPositions)window.__redrawPositions();
 });
 syncFilterUI();
 applyBaseline();

 // ---- Candlestick chart (1 minute): Bitget source, Italian time, SL/TP as
 //      time-bounded segments, daily high/low reference lines ----
 (function(){
   let candles=(p.candles||[]);
   const sym=p.chart_symbol||'ETHUSDT';
   const el=document.getElementById('chart');
   document.getElementById('chartTitle').textContent='Grafico 1 minuto — '+sym+' (Bitget · ora italiana)';
   if(!window.LightweightCharts){
     el.innerHTML='<div class="empty" style="padding:40px">Libreria grafico non caricata (riprova con la rete attiva).</div>';
     return;
   }
   // Italian time (Europe/Rome) for the axis and the crosshair label.
   const tHM=new Intl.DateTimeFormat('it-IT',{timeZone:'Europe/Rome',hour:'2-digit',minute:'2-digit'});
   const tFull=new Intl.DateTimeFormat('it-IT',{timeZone:'Europe/Rome',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
   const dark=matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches;
   const chart=LightweightCharts.createChart(el,{
     layout:{background:{color:'transparent'},textColor:dark?'#d0d0d0':'#333'},
     grid:{vertLines:{color:dark?'#222':'#eee'},horzLines:{color:dark?'#222':'#eee'}},
     timeScale:{timeVisible:true,secondsVisible:false,borderColor:dark?'#333':'#ccc',
       tickMarkFormatter:(t)=>tHM.format(t*1000)},
     localization:{locale:'it-IT',timeFormatter:(t)=>tFull.format(t*1000)},
     rightPriceScale:{borderColor:dark?'#333':'#ccc'},
     crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
     autoSize:true,
   });
   const series=chart.addCandlestickSeries({
     upColor:'#26a65b',downColor:'#e5393b',borderVisible:false,
     wickUpColor:'#26a65b',wickDownColor:'#e5393b',
   });
   let seeded=candles.length>0;
   let allBars=candles.slice();
   let lastTime=candles.length?candles[candles.length-1].time:0;
   if(seeded)series.setData(candles);
   else el.insertAdjacentHTML('afterbegin','<div id="chartWait" style="position:absolute;top:8px;left:12px;font-size:12px;color:var(--muted)">Carico le candele da Bitget…</div>');

   // --- Positions: SL/TP/entry as segments spanning ONLY entry->exit (open: entry->now) ---
   let openLines=[];        // {series, et, price} to grow while the position is open
   let posSeriesAll=[];     // all position line series, so we can clear on variant switch
   function clearPositions(){posSeriesAll.forEach(s=>{try{chart.removeSeries(s);}catch(e){}});posSeriesAll=[];openLines=[];series.setMarkers([]);}
   window.__redrawPositions=function(){clearPositions();if(seeded)drawPositions();};
   function drawPositions(){
     const markers=[];
     const pos=[].concat(
       opens.filter(t=>(t.symbol||'')===sym).map(t=>Object.assign({},t,{isOpen:true})),
       closed.filter(t=>(t.symbol||'')===sym).map(t=>Object.assign({},t,{isOpen:false}))
     );
     pos.forEach(t=>{
       const isLong=(t.side==='long');
       const et=Math.floor(Date.parse(t.entry_time)/1000);
       if(!isFinite(et))return;
       let xt=t.exit_time?Math.floor(Date.parse(t.exit_time)/1000):(lastTime||et+60);
       if(xt<=et)xt=et+60;
       [['#2962ff',+t.entry_price,2,LightweightCharts.LineStyle.Solid],      // entry
        ['#e5393b',+t.stop_price,2,LightweightCharts.LineStyle.Dashed],       // SL
        [t.target_price!=null?'#26a65b':null,t.target_price!=null?+t.target_price:null,2,LightweightCharts.LineStyle.Dashed], // TP
        ['#b26a00',t.liquidation_price!=null?+t.liquidation_price:null,1,LightweightCharts.LineStyle.Dotted] // liq
       ].forEach(([color,price,lw,style],idx)=>{
         if(color==null||price==null||!isFinite(price))return;
         const ls=chart.addLineSeries({color:color,lineWidth:lw,lineStyle:style,
           priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
         ls.setData([{time:et,value:price},{time:xt,value:price}]);
         posSeriesAll.push(ls);
         if(t.isOpen&&idx<3)openLines.push({series:ls,et:et,price:price});
       });
       markers.push({time:et,position:isLong?'belowBar':'aboveBar',
         color:isLong?'#26a65b':'#e5393b',shape:isLong?'arrowUp':'arrowDown',
         text:(isLong?'LONG':'SHORT')+' @'+(+t.entry_price).toFixed(2)});
       if(t.exit_time)markers.push({time:xt,position:isLong?'aboveBar':'belowBar',
         color:'#888',shape:'circle',text:'EXIT'});
     });
     if(markers.length)series.setMarkers(markers.sort((a,b)=>a.time-b.time));
   }
   function growOpenLines(){
     openLines.forEach(o=>o.series.setData([{time:o.et,value:o.price},{time:lastTime,value:o.price}]));
   }

   // --- Daily high / low (current UTC day) reference lines ---
   let dhi=null,dlo=null;
   function drawDailyHL(){
     if(!allBars.length)return;
     const day=(t)=>Math.floor(t/86400);
     const d=day(allBars[allBars.length-1].time);
     const db=allBars.filter(b=>day(b.time)===d);
     if(!db.length)return;
     const hi=Math.max.apply(null,db.map(b=>b.high));
     const lo=Math.min.apply(null,db.map(b=>b.low));
     const t0=allBars[0].time,t1=allBars[allBars.length-1].time;
     if(!dhi)dhi=chart.addLineSeries({color:'#9aa0a6',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dotted,priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false});
     if(!dlo)dlo=chart.addLineSeries({color:'#9aa0a6',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dotted,priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false});
     dhi.setData([{time:t0,value:hi},{time:t1,value:hi}]);
     dlo.setData([{time:t0,value:lo},{time:t1,value:lo}]);
   }

   const lg2=document.getElementById('legend');
   if(lg2)lg2.insertAdjacentHTML('beforeend','<span><span class="dot" style="background:#9aa0a6"></span>Max/Min giorno</span>');

   // positions + daily lines are drawn by the first tick() (avoids duplicates)
   new ResizeObserver(()=>chart.timeScale().fitContent()).observe(el);

   // --- Live data: Bitget first (your trading venue), Binance.vision as fallback ---
   // We always redraw a CONTIGUOUS series (deduped + sorted) so there are never gaps,
   // and on a long gap (browser reopened next day) we reload a fresh window.
   function mergeBars(bars){
     const m=new Map(allBars.map(b=>[b.time,b]));
     bars.forEach(b=>m.set(b.time,b));
     allBars=Array.from(m.values()).sort((a,b)=>a.time-b.time);
     if(allBars.length>720)allBars=allBars.slice(allBars.length-720);
   }
   async function fetchBars(limit){
     try{
       const r=await fetch(`https://api.bitget.com/api/v2/spot/market/candles?symbol=${sym}&granularity=1min&limit=${limit}`,{cache:'no-store'});
       if(r.ok){const j=await r.json();const d=(j&&j.data)||[];
         if(d.length)return d.map(k=>({time:Math.floor(+k[0]/1000),open:+k[1],high:+k[2],low:+k[3],close:+k[4]})).sort((a,b)=>a.time-b.time);}
     }catch(e){}
     for(const h of ['https://data-api.binance.vision','https://api.binance.com']){
       try{const r=await fetch(`${h}/api/v3/klines?symbol=${sym}&interval=1m&limit=${limit}`,{cache:'no-store'});
         if(r.ok){const arr=await r.json();return arr.map(k=>({time:Math.floor(k[0]/1000),open:+k[1],high:+k[2],low:+k[3],close:+k[4]}));}
       }catch(e){}
     }
     return null;
   }
   let posDrawn=false;
   async function tick(){
     const nowS=Math.floor(Date.now()/1000);
     const gap=seeded?(nowS-lastTime):1e9;
     const bigGap=gap>3600;                                   // > 1h -> reload fresh
     const need=(!seeded||bigGap)?400:Math.min(1000,Math.max(3,Math.ceil(gap/60)+3));
     const bars=await fetchBars(need);
     if(!bars||!bars.length)return;
     if(!seeded||bigGap)allBars=bars.slice(); else mergeBars(bars);
     series.setData(allBars);                                 // contiguous, no fragments
     lastTime=allBars[allBars.length-1].time;
     if(!seeded){seeded=true;const w=document.getElementById('chartWait');if(w)w.remove();chart.timeScale().fitContent();}
     if(!posDrawn){drawPositions();posDrawn=true;}
     growOpenLines();drawDailyHL();
     const last=allBars[allBars.length-1];
     document.getElementById('updated').textContent=
       'Prezzo live '+sym+' (Bitget): '+last.close.toLocaleString('it-IT',{maximumFractionDigits:2})+
       ' · '+tHM.format(Date.now());
   }
   tick(); setInterval(tick,60000);
 })();
</script>
</body></html>
"""
    return html.replace("__PAYLOAD__", data)


def _render_compare(payload: dict) -> str:
    data = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False)
    html = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pattern Scout — Paper</title>
<style>
  :root{color-scheme:light dark;--bg:Canvas;--fg:CanvasText;
    --muted:color-mix(in srgb,CanvasText 62%,Canvas 38%);
    --card:color-mix(in srgb,Canvas 94%,CanvasText 6%);
    --border:color-mix(in srgb,CanvasText 18%,Canvas 82%);
    --accent:#0a7cff;--good:#0a8f55;--bad:#c23b32;--warn:#b77900;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.35}
  main{width:min(1180px,calc(100% - 32px));margin:0 auto;padding:24px 0 40px}
  header{display:flex;align-items:end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
  h1{margin:0;font-size:24px;font-weight:600}
  .subtitle{margin-top:4px;color:var(--muted);font-size:14px}
  .controls{display:flex;align-items:center;gap:10px;margin:6px 0 18px}
  select{font-size:15px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);
    background:var(--card);color:var(--fg)}
  label.ctl{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.04em}
  .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}
  .card{border:1px solid var(--border);border-radius:8px;background:var(--card);padding:14px}
  .label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  .value{margin-top:6px;font-size:26px;font-weight:600}
  .value.good{color:var(--good)}.value.bad{color:var(--bad)}
  .split{display:grid;grid-template-columns:1.3fr .7fr;gap:12px;align-items:start}
  .section{margin-top:16px}.section h2{margin:0 0 10px;font-size:16px;font-weight:600}
  .chart-wrap{height:280px;padding:10px}
  svg{display:block;width:100%;height:100%}
  .axis{stroke:var(--border);stroke-width:1}.line{fill:none;stroke:var(--accent);stroke-width:2}
  .zero{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4}
  .mini-list{display:grid;gap:8px}
  .mini-row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--border);
    padding-bottom:8px;font-size:13px}.mini-row:last-child{border-bottom:0;padding-bottom:0}
  .table-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:8px}
  table{width:100%;min-width:820px;border-collapse:collapse;font-size:13px}
  th,td{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
  th{color:var(--muted);font-weight:600;background:color-mix(in srgb,var(--card) 80%,var(--bg) 20%)}
  .pill{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:2px 8px;font-size:12px}
  .pill.target{color:var(--good)}.pill.stop{color:var(--bad)}.pill.session_close{color:var(--warn)}
  .empty{color:var(--muted);padding:18px;text-align:center}
  @media (max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}.split{grid-template-columns:1fr}}
</style>
</head>
<body>
<main>
  <header>
    <div><h1>Pattern Scout — Paper</h1>
    <div class="subtitle" id="subtitle"></div></div>
  </header>
  <div class="controls">
    <label class="ctl" for="variant">Filtro daily</label>
    <select id="variant"></select>
    <span class="subtitle" id="variantHint"></span>
  </div>
  <section class="grid">
    <div class="card"><div class="label">Trade</div><div class="value" id="totalTrades">0</div></div>
    <div class="card"><div class="label">Win rate</div><div class="value" id="winRate">0%</div></div>
    <div class="card"><div class="label">PnL totale</div><div class="value" id="totalPnl">0</div></div>
    <div class="card"><div class="label">Avg R</div><div class="value" id="avgR">0</div></div>
  </section>
  <section class="split">
    <div class="section card chart-wrap"><h2>Equity curve</h2>
      <svg id="equityChart" role="img" aria-label="equity"></svg></div>
    <div class="section card"><h2>Metriche</h2><div class="mini-list" id="diagnostics"></div></div>
  </section>
  <section class="section"><h2>Trade</h2>
    <div class="table-wrap"><table><thead><tr>
      <th>Sessione</th><th>Simbolo</th><th>Lato</th><th>Segnale</th><th>Entry</th>
      <th>Exit</th><th>R</th><th>PnL</th><th>Uscita</th></tr></thead>
      <tbody id="tradeRows"></tbody></table></div>
  </section>
</main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
  const payload=JSON.parse(document.getElementById('payload').textContent);
  const variants=payload.variants||[];
  document.getElementById('subtitle').textContent=payload.title||'';
  const fmt=new Intl.NumberFormat('it-IT',{maximumFractionDigits:2});
  const money=new Intl.NumberFormat('it-IT',{maximumFractionDigits:2,minimumFractionDigits:2});
  const pct=v=>`${fmt.format((Number(v)||0)*100)}%`;
  const num=v=>fmt.format(Number(v)||0);
  const sel=document.getElementById('variant');
  variants.forEach((v,i)=>{const o=document.createElement('option');o.value=i;o.textContent=v.name;sel.appendChild(o);});

  function render(idx){
    const v=variants[idx]||{};const s=v.summary||{};const trades=v.trades||[];const equity=v.equity||[];
    document.getElementById('totalTrades').textContent=s.total_trades??trades.length;
    document.getElementById('winRate').textContent=pct(s.win_rate);
    const tp=document.getElementById('totalPnl');
    tp.textContent=money.format(Number(s.total_pnl)||0);
    tp.classList.toggle('good',Number(s.total_pnl)>0);tp.classList.toggle('bad',Number(s.total_pnl)<0);
    document.getElementById('avgR').textContent=num(s.avg_r);
    document.getElementById('variantHint').textContent=v.hint||'';
    const exits=trades.reduce((a,t)=>{const k=t.exit_reason||'—';a[k]=(a[k]||0)+1;return a;},{});
    const pf=s.profit_factor; const pfTxt=(pf===null||pf===undefined)?'—':(isFinite(pf)?num(pf):'∞');
    const diag=[['Profit factor',pfTxt],['Wins',s.wins||0],['Losses',s.losses||0],
      ['Exit target',exits.target||0],['Exit stop',exits.stop||0],
      ['Exit fine sessione',exits.session_close||0]];
    document.getElementById('diagnostics').innerHTML=diag.map(([l,x])=>
      `<div class="mini-row"><span>${l}</span><strong>${x}</strong></div>`).join('');
    const rows=trades.map(t=>`<tr>
      <td>${t.session??''}</td><td>${t.symbol??''}</td><td>${t.side??''}</td>
      <td>${t.signal_type??''}</td><td>${money.format(Number(t.entry_price)||0)}</td>
      <td>${money.format(Number(t.exit_price)||0)}</td><td>${num(t.r_multiple)}</td>
      <td>${money.format(Number(t.pnl)||0)}</td>
      <td><span class="pill ${t.exit_reason??''}">${t.exit_reason??''}</span></td></tr>`).join('');
    document.getElementById('tradeRows').innerHTML=rows||'<tr><td colspan="9" class="empty">Nessun trade.</td></tr>';
    drawEquity(equity);
  }
  function drawEquity(equity){
    const svg=document.getElementById('equityChart');svg.innerHTML='';
    const w=svg.clientWidth||700,h=svg.clientHeight||220;
    const pad={left:52,right:12,top:18,bottom:28};
    const vals=equity.map((d,i)=>({x:i,y:Number(d.equity??d.pnl??0)})).filter(d=>Number.isFinite(d.y));
    if(!vals.length){svg.innerHTML='<text x="50%" y="50%" text-anchor="middle" fill="currentColor">Nessuna equity</text>';return;}
    const minY=Math.min(0,...vals.map(d=>d.y)),maxY=Math.max(0,...vals.map(d=>d.y));const span=maxY-minY||1;
    const xs=x=>vals.length===1?pad.left+(w-pad.left-pad.right)/2:pad.left+(x/(vals.length-1))*(w-pad.left-pad.right);
    const ys=y=>pad.top+(1-((y-minY)/span))*(h-pad.top-pad.bottom);
    const path=vals.map((d,i)=>`${i?'L':'M'}${xs(d.x).toFixed(1)},${ys(d.y).toFixed(1)}`).join(' ');
    const pts=vals.map(d=>`<circle cx="${xs(d.x).toFixed(1)}" cy="${ys(d.y).toFixed(1)}" r="3.5" fill="var(--accent)"></circle>`).join('');
    svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
    svg.innerHTML=`<line class="axis" x1="${pad.left}" y1="${h-pad.bottom}" x2="${w-pad.right}" y2="${h-pad.bottom}"></line>
      <line class="axis" x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${h-pad.bottom}"></line>
      <line class="zero" x1="${pad.left}" y1="${ys(0)}" x2="${w-pad.right}" y2="${ys(0)}"></line>
      <path class="line" d="${path}"></path>${pts}
      <text x="${pad.left}" y="${pad.top+2}" fill="currentColor" font-size="12">${money.format(maxY)}</text>
      <text x="${pad.left}" y="${h-6}" fill="currentColor" font-size="12">${money.format(minY)}</text>`;
  }
  sel.addEventListener('change',()=>render(Number(sel.value)));
  render(0);window.addEventListener('resize',()=>render(Number(sel.value)));
</script>
</body></html>
"""
    return html.replace("__PAYLOAD__", data)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
