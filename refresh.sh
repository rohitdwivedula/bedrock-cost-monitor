#!/usr/bin/env bash
# Refresh the Bedrock spend dashboard data. Run from this directory.
set -euo pipefail
cd "$(dirname "$0")"
# rates come from Cost Explorer and change rarely; --fast skips them
if [ "${1:-}" != "--fast" ]; then
  python3 -I rates.py        # $/MTok per model, derived from Cost Explorer
fi
python3 -I collect.py        # sync + parse new invocation logs -> spend.db
# mirror files are already parsed into spend.db and recorded in `seen`,
# so deleting old copies is safe and keeps disk use flat
find mirror -name '*.gz' -mtime +30 -delete 2>/dev/null || true
echo "refreshed $(date -u +%FT%TZ)"
