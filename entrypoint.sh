#!/bin/sh
set -eu

mkdir -p /downloads /app/data
if [ "${1:-}" = "serve" ]; then
  shift
  exec uvicorn app:app --host 0.0.0.0 --port 8080
fi

exec python /app/download.py "$@"
