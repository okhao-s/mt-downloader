#!/bin/sh
set -eu

mkdir -p /downloads /app/data

# 从 config.json 读取代理设置并导出为环境变量（容器内所有进程生效）
CONFIG_FILE="/app/data/config.json"
if [ -f "$CONFIG_FILE" ]; then
  HTTP_PROXY_VAL=$(python3 -c "import json; c=json.load(open('$CONFIG_FILE')); print(c.get('http_proxy','') or '')" 2>/dev/null || true)
  HTTPS_PROXY_VAL=$(python3 -c "import json; c=json.load(open('$CONFIG_FILE')); print(c.get('https_proxy','') or '')" 2>/dev/null || true)
  NO_PROXY_VAL=$(python3 -c "import json; c=json.load(open('$CONFIG_FILE')); print(c.get('no_proxy','') or '')" 2>/dev/null || true)
  [ -n "$HTTP_PROXY_VAL" ] && export HTTP_PROXY="$HTTP_PROXY_VAL" && export http_proxy="$HTTP_PROXY_VAL"
  [ -n "$HTTPS_PROXY_VAL" ] && export HTTPS_PROXY="$HTTPS_PROXY_VAL" && export https_proxy="$HTTPS_PROXY_VAL"
  [ -n "$NO_PROXY_VAL" ] && export NO_PROXY="$NO_PROXY_VAL" && export no_proxy="$NO_PROXY_VAL"
fi

if [ "${1:-}" = "serve" ]; then
  shift
  exec uvicorn app:app --host 0.0.0.0 --port 8080
fi

exec python /app/download.py "$@"
