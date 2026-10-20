#!/bin/sh
# Regenerate latest.html / latest.json / detail_cache.json.
# The report server reads those files on each request, so it does not need a restart.
set -eu
cd "$(dirname "$0")"
exec python3 generate_report.py "$@"
