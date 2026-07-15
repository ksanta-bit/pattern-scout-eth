#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH=src

python3 -m pattern_scout.cli shutdown --force --all
echo "Pattern Scout spento."
