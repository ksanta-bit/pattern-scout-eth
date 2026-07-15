#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH=src

echo "Pattern Scout: reset vecchio processo, nuovo backtest, nuova dashboard..."

if [[ "${PATTERN_SCOUT_OPEN_BROWSER:-1}" == "1" ]]; then
  python3 -m pattern_scout.cli reset-dashboard --demo --open-browser
else
  python3 -m pattern_scout.cli reset-dashboard --demo
fi

echo
echo "Dashboard pronta."
echo "Per spegnere tutto: ./spegni.command"
