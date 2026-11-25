#!/bin/bash
# Scan a filesystem (metadata only, a few minutes) and rebuild the explorer.
# Usage: ./refresh.sh [root]          default root: /storage
# From cron, e.g. Mondays 03:00:  0 3 * * 1 /path/to/storage-explorer/refresh.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT=${1:-/storage}
mkdir -p data
OUT=data/storage_$(date +%F_%H%M).json.gz
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "Python 3.8+ needed (try: module load Python)"; exit 1; }
nice -n 19 ionice -c3 python3 scan.py "$ROOT" -o "$OUT" -j 16
python3 build.py "$OUT" -o explorer
echo "Scan saved to $OUT"
echo "Open explorer/standalone.html in a browser, or share that one file."
