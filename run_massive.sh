#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/run_overlay.py --no-marketaux --no-reuters-default-feed --limit 30 "$@"
