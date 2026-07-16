"""Merge workflow_dispatch inputs into bot_settings.json (persisted overrides).

Called by the GitHub workflow on manual dispatch. Reads the chosen options from
environment variables (IN_LEV, IN_FILTER, IN_RISK, IN_SESSION, IN_SYMBOL) and writes
them into bot_settings.json, which the bot loads on every run (including cron).
"""
from __future__ import annotations

import json
import os
import pathlib

path = pathlib.Path("bot_settings.json")
settings = json.loads(path.read_text()) if path.exists() else {}

lev = os.environ.get("IN_LEV", "keep")
if lev not in ("", "keep"):
    settings["leverage"] = float(lev)

flt = os.environ.get("IN_FILTER", "keep")
if flt in ("on", "off"):
    settings["daily_filter"] = flt

risk = os.environ.get("IN_RISK", "keep")
if risk not in ("", "keep"):
    settings["risk_fraction"] = float(risk)

session = os.environ.get("IN_SESSION", "keep")
if session not in ("", "keep"):
    settings["session_preset"] = session

symbols = os.environ.get("IN_SYMBOLS", "keep")
if symbols not in ("", "keep"):
    parsed = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if parsed:
        settings["symbols"] = parsed

path.write_text(json.dumps(settings, indent=2))
print("bot_settings.json:", settings)
