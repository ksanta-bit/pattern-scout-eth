from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

import pandas as pd

from .backtester import PatternScoutBacktester
from .config import PatternScoutConfig
from .dashboard import build_dashboard


def main() -> None:
    parser = argparse.ArgumentParser(prog="pattern-scout")
    sub = parser.add_subparsers(dest="command", required=True)

    backtest = sub.add_parser("backtest", help="Run the Pattern Scout backtest on 5-minute OHLCV data.")
    backtest.add_argument("--data", required=True, help="CSV with timestamp, open, high, low, close, volume.")
    backtest.add_argument("--config", default="config.example.json", help="JSON configuration file.")
    backtest.add_argument("--out", default="reports/latest", help="Output directory for reports.")
    backtest.add_argument("--dashboard", action="store_true", help="Also generate dashboard.html in the output directory.")

    sample = sub.add_parser("make-sample", help="Create a tiny synthetic CSV for a smoke test.")
    sample.add_argument("--out", default="data/sample_5m.csv")

    dashboard = sub.add_parser("dashboard", help="Generate an HTML dashboard from a report directory.")
    dashboard.add_argument("--reports", default="reports/latest", help="Directory containing summary/trades/equity reports.")
    dashboard.add_argument("--out", default=None, help="Dashboard HTML output path. Defaults to <reports>/dashboard.html.")

    serve_dashboard = sub.add_parser("serve-dashboard", help="Serve a dashboard report on localhost.")
    serve_dashboard.add_argument("--reports", default="reports/sample", help="Report directory to serve.")
    serve_dashboard.add_argument("--host", default="127.0.0.1")
    serve_dashboard.add_argument("--port", type=int, default=8766)

    check_dashboard = sub.add_parser("check-dashboard", help="Check that the local dashboard server is responding.")
    check_dashboard.add_argument("--host", default="127.0.0.1")
    check_dashboard.add_argument("--port", type=int, default=8766)

    shutdown = sub.add_parser("shutdown", help="Stop local Pattern Scout services started by this CLI.")
    shutdown.add_argument("--force", action="store_true", help="Ignore stale or missing server records.")
    shutdown.add_argument("--all", action="store_true", help="Also stop Pattern Scout dashboard servers found by command name.")

    run_demo = sub.add_parser("run-demo", help="Create sample data, run a backtest, and generate the dashboard.")
    run_demo.add_argument("--data", default="data/sample_5m.csv")
    run_demo.add_argument("--config", default="config.example.json")
    run_demo.add_argument("--out", default="reports/sample")

    paper_replay = sub.add_parser(
        "paper-replay",
        help="Replay a 5-minute CSV bar-by-bar through the live paper engine (offline, no broker).",
    )
    paper_replay.add_argument("--data", required=True, help="5-minute OHLCV CSV.")
    paper_replay.add_argument("--config", default="config.example.json")
    paper_replay.add_argument("--symbol", default="REPLAY")
    paper_replay.add_argument("--out", default="reports/paper", help="Where to write trades/equity/summary + dashboard.")
    paper_replay.add_argument("--state", default="reports/paper/paper_state.json")
    paper_replay.add_argument("--dashboard", action="store_true")
    paper_replay.add_argument("--both", action="store_true",
                              help="Run with the daily filter OFF and ON and build one dashboard with a selector.")

    paper_live = sub.add_parser(
        "paper-live",
        help="Run live paper trading against an Alpaca paper account (needs ALPACA_API_KEY_ID/SECRET).",
    )
    paper_live.add_argument("--symbols", required=True, help="Comma-separated tickers, e.g. GOOGL,AMD,METU.")
    paper_live.add_argument("--config", default="config.paper.json")
    paper_live.add_argument("--poll-seconds", type=int, default=60)
    paper_live.add_argument("--feed", default="iex", help="'iex' (free) or 'sip' (paid data plan).")
    paper_live.add_argument("--state", default="reports/paper_live/paper_state.json")
    paper_live.add_argument("--out", default="reports/paper_live")
    paper_live.add_argument("--once", action="store_true", help="Single polling pass (for testing).")

    paper_crypto = sub.add_parser(
        "paper-crypto",
        help="Paper-live on REAL crypto data (Binance/Bitget public feed) with simulated fills. No API key needed.",
    )
    paper_crypto.add_argument("--symbols", default="ETHUSDT", help="Comma-separated, e.g. ETHUSDT,BTCUSDT.")
    paper_crypto.add_argument("--exchange", default="binance", help="binance | binanceus | bitget.")
    paper_crypto.add_argument("--config", default="config.crypto.json")
    paper_crypto.add_argument("--interval", default="5m")
    paper_crypto.add_argument("--warmup-days", type=int, default=20)
    paper_crypto.add_argument("--state", default="reports/crypto_live/paper_state.json")
    paper_crypto.add_argument("--out", default="docs", help="Output dir for reports + dashboard (docs/ = GitHub Pages).")
    paper_crypto.add_argument("--max-iterations", type=int, default=None, help="Stop after N polls (testing).")
    paper_crypto.add_argument("--ci", action="store_true", help="Single idempotent pass (for GitHub Actions).")
    paper_crypto.add_argument("--cumulative", default="reports/crypto_ci/cumulative.json",
                              help="Cumulative trade log persisted across CI runs.")
    paper_crypto.add_argument("--lookback-days", type=int, default=4, help="CI: sessions to replay each pass.")
    paper_crypto.add_argument("--reset", action="store_true", help="Wipe the cumulative state (restart from starting capital).")
    paper_crypto.add_argument("--daily-filter", choices=["keep", "on", "off"], default="keep",
                              help="Override the daily breakout/retest filter for this run.")

    optimize = sub.add_parser(
        "optimize",
        help="Grid-search the undefined parameters over one or more CSVs and write the best config.",
    )
    optimize.add_argument("--data", required=True, nargs="+", help="One or more 5-minute OHLCV CSVs.")
    optimize.add_argument("--config", default="config.example.json", help="Base config to start from.")
    optimize.add_argument("--out", default="config.optimized.json", help="Where to write the best config.")
    optimize.add_argument("--min-trades", type=int, default=5, help="Reject configs with fewer trades.")
    optimize.add_argument("--report", default=None, help="Optional JSON path for the full ranking.")

    reset = sub.add_parser(
        "reset-dashboard",
        help="Stop the old local server, run a fresh backtest, and start the dashboard in background.",
    )
    reset.add_argument("--data", default="data/sample_5m.csv")
    reset.add_argument("--config", default="config.example.json")
    reset.add_argument("--out", default="reports/sample")
    reset.add_argument("--host", default="127.0.0.1")
    reset.add_argument("--port", type=int, default=8766)
    reset.add_argument("--demo", action="store_true", help="Regenerate synthetic demo data before the backtest.")
    reset.add_argument("--open-browser", action="store_true", help="Open the dashboard in the default browser.")

    args = parser.parse_args()
    if args.command == "backtest":
        data_path = Path(args.data)
        if not data_path.exists():
            print(
                f"Data file not found: {data_path}\n\n"
                "Use a real 5-minute OHLCV CSV, or generate the demo file first:\n\n"
                "  PYTHONPATH=src python3 -m pattern_scout.cli make-sample --out data/sample_5m.csv\n"
                "  PYTHONPATH=src python3 -m pattern_scout.cli backtest "
                "--data data/sample_5m.csv --config config.example.json --out reports/sample\n",
                file=sys.stderr,
            )
            raise SystemExit(2)
        config = PatternScoutConfig.from_json(args.config)
        result = PatternScoutBacktester(config).run_csv(data_path)
        result.write_reports(args.out)
        if args.dashboard:
            dashboard_path = build_dashboard(args.out)
            print(f"Dashboard written to {dashboard_path}")
        print(json.dumps(result.summary(), indent=2))
    elif args.command == "make-sample":
        write_sample(Path(args.out))
        print(f"Sample data written to {args.out}")
    elif args.command == "dashboard":
        dashboard_path = build_dashboard(args.reports, args.out)
        print(f"Dashboard written to {dashboard_path}")
    elif args.command == "serve-dashboard":
        serve_dashboard_forever(Path(args.reports), args.host, args.port)
    elif args.command == "check-dashboard":
        check_dashboard_server(args.host, args.port)
    elif args.command == "shutdown":
        shutdown_services(force=args.force, stop_all=args.all)
    elif args.command == "run-demo":
        data_path = Path(args.data)
        write_sample(data_path)
        config = PatternScoutConfig.from_json(args.config)
        result = PatternScoutBacktester(config).run_csv(data_path)
        result.write_reports(args.out)
        dashboard_path = build_dashboard(args.out)
        print(f"Sample data written to {data_path}")
        print(f"Reports written to {args.out}")
        print(f"Dashboard written to {dashboard_path}")
        print(json.dumps(result.summary(), indent=2))
    elif args.command == "paper-replay":
        run_paper_replay(args)
    elif args.command == "paper-live":
        run_paper_live(args)
    elif args.command == "paper-crypto":
        run_paper_crypto(args)
    elif args.command == "optimize":
        run_optimize(args)
    elif args.command == "reset-dashboard":
        reset_dashboard(args)


def run_optimize(args: argparse.Namespace) -> None:
    from .optimize import grid_search, write_best_config

    for p in args.data:
        if not Path(p).exists():
            print(f"Data file not found: {p}", file=sys.stderr)
            raise SystemExit(2)
    base = PatternScoutConfig.from_json(args.config)
    result = grid_search(args.data, base_config=base, min_trades=args.min_trades)
    out = write_best_config(result, args.out)
    print("\n=== OPTIMIZATION RESULT ===")
    print(f"Combinations tested: {result['combinations']}")
    print("Best params:", json.dumps(result["best_params"], indent=2))
    print("Best summary:", json.dumps(result["best_summary"], indent=2, default=str))
    print(f"Best config written to: {out}")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(result["top"], indent=2, default=str), encoding="utf-8")
        print(f"Full ranking written to: {args.report}")


def run_paper_crypto(args: argparse.Namespace) -> None:
    from .paper import run_crypto_paper, run_crypto_ci

    config = PatternScoutConfig.from_json(args.config)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("No symbols provided.", file=sys.stderr)
        raise SystemExit(2)

    if args.daily_filter != "keep":
        config.daily_context.enabled = (args.daily_filter == "on")
        print(f"Filtro daily forzato: {'ATTIVO' if config.daily_context.enabled else 'DISATTIVO'}")

    if args.reset:
        for pth in [Path(args.cumulative), Path(args.state)]:
            try:
                if pth.exists():
                    pth.unlink()
                    print(f"Reset: removed {pth}")
            except OSError as exc:
                print(f"Could not remove {pth}: {exc}", file=sys.stderr)
        print(f"Capitale ripristinato a {config.risk.account_size:.0f} USDT.")
        if not args.ci:
            return

    if args.ci:
        run_crypto_ci(
            config, symbols, out_dir=args.out, cumulative_path=args.cumulative,
            exchange=args.exchange, interval=args.interval, lookback_days=args.lookback_days,
            on_event=lambda m: print(m, flush=True),
        )
        return

    print(
        f"Paper-live crypto on {', '.join(symbols)} via {args.exchange}. "
        "Real prices, simulated fills. Ctrl-C to stop.\n"
    )
    try:
        trader = run_crypto_paper(
            config,
            symbols,
            exchange=args.exchange,
            interval=args.interval,
            warmup_days=args.warmup_days,
            state_path=Path(args.state),
            on_event=lambda m: print(m, flush=True),
            max_iterations=args.max_iterations,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. State saved.")
        return
    trader.write_reports(args.out)
    print("\n=== PAPER CRYPTO SUMMARY ===")
    print(json.dumps(trader.summary(), indent=2, default=str))


def _equity_records(trades: list) -> list:
    records = []
    cum = 0.0
    for t in trades:
        cum += float(t.pnl or 0.0)
        records.append({"exit_time": t.exit_time, "pnl": float(t.pnl or 0.0), "equity": cum})
    return records


def run_paper_replay(args: argparse.Namespace) -> None:
    from .paper import replay_csv
    from .dashboard import build_compare_dashboard

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"Data file not found: {data_path}", file=sys.stderr)
        raise SystemExit(2)

    if args.both:
        variants = []
        for enabled, name in [(False, "Filtro daily OFF"), (True, "Filtro daily ON")]:
            config = PatternScoutConfig.from_json(args.config)
            config.daily_context.enabled = enabled
            trader = replay_csv(data_path, config, symbol=args.symbol, on_event=lambda m: None)
            s = trader.summary()
            variants.append({
                "name": name,
                "summary": s,
                "trades": [t.to_dict() for t in trader.broker.trades],
                "equity": _equity_records(trader.broker.trades),
                "hint": "Nucleo del video (step 1-3)" if not enabled else "Con breakout+retest daily (esempio live)",
            })
            print(f"{name}: {s['total_trades']} trade, PnL {s['total_pnl']:.2f}, avg R {s['avg_r']:.2f}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        dash = build_compare_dashboard(variants, out / "dashboard.html")
        print(f"Dashboard (filtro selezionabile) scritta in: {dash}")
        return

    config = PatternScoutConfig.from_json(args.config)
    trader = replay_csv(
        data_path,
        config,
        symbol=args.symbol,
        state_path=Path(args.state),
        on_event=lambda m: print(m, flush=True),
    )
    trader.write_reports(args.out)
    if args.dashboard:
        dashboard_path = build_dashboard(args.out)
        print(f"Dashboard written to {dashboard_path}")
    print("\n=== PAPER REPLAY SUMMARY ===")
    print(json.dumps(trader.summary(), indent=2, default=str))
    print(f"State: {args.state}")


def run_paper_live(args: argparse.Namespace) -> None:
    from .paper import run_live

    config = PatternScoutConfig.from_json(args.config)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("No symbols provided.", file=sys.stderr)
        raise SystemExit(2)
    try:
        trader = run_live(
            config,
            symbols,
            poll_seconds=args.poll_seconds,
            state_path=Path(args.state),
            feed=args.feed,
            on_event=lambda m: print(m, flush=True),
            once=args.once,
        )
    except KeyboardInterrupt:
        print("\nInterrupted. State saved.")
        return
    trader.write_reports(args.out)
    print("\n=== PAPER LIVE SUMMARY ===")
    print(json.dumps(trader.summary(), indent=2, default=str))


def state_file() -> Path:
    return Path(".pattern_scout_state.json")


def serve_dashboard_forever(reports_dir: Path, host: str, port: int) -> None:
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    reports_dir = reports_dir.resolve()
    dashboard_path = reports_dir / "dashboard.html"
    if not dashboard_path.exists():
        print(
            f"Dashboard not found: {dashboard_path}\n"
            "Generate it first with:\n\n"
            f"  PYTHONPATH=src python3 -m pattern_scout.cli dashboard --reports {reports_dir}\n",
            file=sys.stderr,
        )
        raise SystemExit(2)

    handler = partial(SimpleHTTPRequestHandler, directory=str(reports_dir))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/dashboard.html"
    state_file().write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "kind": "dashboard",
                "host": host,
                "port": port,
                "reports_dir": str(reports_dir),
                "url": url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Dashboard server running: {url}")
    print("Stop it with: PYTHONPATH=src python3 -m pattern_scout.cli shutdown")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if state_file().exists():
                state_file().unlink()
        except OSError:
            pass


def check_dashboard_server(host: str, port: int) -> None:
    url = f"http://{host}:{port}/dashboard.html"
    try:
        with urlopen(url, timeout=3) as response:
            status = getattr(response, "status", 200)
            body = response.read(200).decode("utf-8", errors="replace")
    except URLError as exc:
        print(f"Dashboard is not responding at {url}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if status != 200 or "Pattern Scout Dashboard" not in body:
        print(f"Dashboard responded, but not as expected: HTTP {status}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Dashboard OK: {url}")


def reset_dashboard(args: argparse.Namespace) -> None:
    shutdown_services(force=True, stop_all=True)

    data_path = Path(args.data)
    if args.demo:
        write_sample(data_path)
    elif not data_path.exists():
        print(
            f"Data file not found: {data_path}\n\n"
            "Use --demo for the synthetic dashboard, or pass a real 5-minute CSV with --data.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    config = PatternScoutConfig.from_json(args.config)
    result = PatternScoutBacktester(config).run_csv(data_path)
    result.write_reports(args.out)
    dashboard_path = build_dashboard(args.out)
    url = start_dashboard_background(Path(args.out), args.host, args.port)
    if args.open_browser:
        webbrowser.open(url)

    print("Pattern Scout reset complete.")
    print(f"Data: {data_path}")
    print(f"Reports: {args.out}")
    print(f"Dashboard file: {dashboard_path}")
    print(f"Dashboard URL: {url}")
    print("Stop command: PYTHONPATH=src python3 -m pattern_scout.cli shutdown --force --all")
    print(json.dumps(result.summary(), indent=2))


def start_dashboard_background(reports_dir: Path, host: str, port: int) -> str:
    reports_dir = reports_dir.resolve()
    if not (reports_dir / "dashboard.html").exists():
        print(f"Dashboard not found in {reports_dir}", file=sys.stderr)
        raise SystemExit(2)

    actual_port = first_available_port(host, port)
    if actual_port != port:
        print(f"Port {port} is busy; using {actual_port}.")

    log_path = Path("reports") / "dashboard_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    src_path = str(Path("src").resolve())
    env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [
        sys.executable,
        "-m",
        "pattern_scout.cli",
        "serve-dashboard",
        "--reports",
        str(reports_dir),
        "--host",
        host,
        "--port",
        str(actual_port),
    ]

    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
        )

    url = f"http://{host}:{actual_port}/dashboard.html"
    for _ in range(30):
        if process.poll() is not None:
            print(f"Dashboard server exited early. Log: {log_path}", file=sys.stderr)
            print(_tail_text(log_path), file=sys.stderr)
            raise SystemExit(1)
        try:
            with urlopen(url, timeout=1) as response:
                if getattr(response, "status", 200) == 200:
                    return url
        except URLError:
            time.sleep(0.2)

    process.terminate()
    print(f"Dashboard did not start in time. Log: {log_path}", file=sys.stderr)
    print(_tail_text(log_path), file=sys.stderr)
    raise SystemExit(1)


def first_available_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 50):
        if port_is_available(host, port):
            return port
    print(f"No free local port found from {preferred_port} to {preferred_port + 49}.", file=sys.stderr)
    raise SystemExit(1)


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def _tail_text(path: Path, limit: int = 3000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def shutdown_services(force: bool = False, stop_all: bool = False) -> None:
    path = state_file()
    if not path.exists():
        message = "No Pattern Scout local services are registered."
        if force:
            print(message)
            if stop_all:
                stop_all_dashboard_servers(force=force)
            return
        print(message, file=sys.stderr)
        raise SystemExit(1)

    state = json.loads(path.read_text(encoding="utf-8"))
    pid = int(state["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"Stale server record removed. Process {pid} was already stopped.")
    except PermissionError as exc:
        if not force:
            print(f"Could not stop process {pid}: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Could not stop process {pid}: {exc}. Continuing because --force is enabled.")
    else:
        print(f"Stopped Pattern Scout {state.get('kind', 'service')} server on PID {pid}.")

    try:
        path.unlink()
    except OSError:
        pass
    if stop_all:
        stop_all_dashboard_servers(force=force)


def stop_all_dashboard_servers(force: bool = False) -> None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "pattern_scout.cli serve-dashboard"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        if not force:
            print(f"Could not search dashboard processes: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Could not search dashboard processes: {exc}.")
        return

    if result.returncode not in (0, 1):
        if not force:
            print(result.stderr.strip() or "Could not search dashboard processes.", file=sys.stderr)
            raise SystemExit(1)
        message = result.stderr.strip() or "Could not search dashboard processes."
        print(f"{message} Continuing because --force is enabled.")
        return

    stopped = 0
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == current_pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            if not force:
                print(f"Could not stop dashboard process {pid}: {exc}", file=sys.stderr)
                raise SystemExit(1)
            print(f"Could not stop dashboard process {pid}: {exc}.")
        else:
            stopped += 1
    if stopped:
        print(f"Stopped {stopped} Pattern Scout dashboard process(es).")


def write_sample(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for day in pd.date_range("2024-12-30", "2025-01-03", freq="D"):
        day = day.strftime("%Y-%m-%d")
        base = 89.0 if day < "2025-01-02" else 100.0
        timestamps = pd.date_range(f"{day} 09:30", periods=78, freq="5min")
        price = base
        for ts in timestamps:
            rows.append(
                {
                    "timestamp": ts,
                    "open": price,
                    "high": price + 0.4,
                    "low": price - 0.4,
                    "close": price + 0.05,
                    "volume": 1000,
                }
            )
            price += 0.01

    sample = pd.DataFrame(rows)
    for day in ["2024-12-30", "2024-12-31", "2025-01-01"]:
        base_mask = sample["timestamp"].dt.strftime("%Y-%m-%d").eq(day)
        sample.loc[base_mask, "high"] = 91.0
        sample.loc[base_mask, "low"] = 88.0
        sample.loc[base_mask, "close"] = 90.0
    breakout_mask = sample["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-02")
    sample.loc[breakout_mask, "open"] = 91.0
    sample.loc[breakout_mask, "high"] = 101.0
    sample.loc[breakout_mask, "low"] = 90.5
    sample.loc[breakout_mask, "close"] = 100.0

    # 2025-01-03: bearish opening manipulation retests the prior base breakout,
    # then a John Wick triggers the reversal back toward the opening range high.
    mask = sample["timestamp"].dt.strftime("%Y-%m-%d").eq("2025-01-03")
    idx = sample.index[mask][:8]
    custom = [
        (100.0, 100.2, 96.8, 97.0),
        (97.0, 97.2, 94.0, 94.2),
        (94.2, 94.9, 91.0, 94.6),
        (94.6, 96.0, 94.5, 95.5),
        (95.5, 98.0, 95.0, 97.8),
        (97.8, 100.5, 97.5, 100.2),
        (100.2, 101.0, 99.8, 100.6),
        (100.6, 100.8, 100.0, 100.1),
    ]
    for row_idx, candle in zip(idx, custom):
        sample.loc[row_idx, ["open", "high", "low", "close"]] = candle
    sample.to_csv(path, index=False)


if __name__ == "__main__":
    main()
