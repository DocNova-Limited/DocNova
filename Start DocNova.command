#!/bin/bash
cd "$(dirname "$0")" || exit 1
RUNTIME_PYTHON='/Users/drusmanmacbookpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
if [ ! -x "$RUNTIME_PYTHON" ]; then
  RUNTIME_PYTHON=python3
fi
# Port 4180 is reserved for this copy of the site, so an older preview left running on 4173 can't hide it.
export DOCNOVA_PORT=4180
# Always run the newest version: stop any DocNova server already running on this port first.
OLD_PIDS=$(lsof -ti tcp:4180 -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD_PIDS" ]; then
  echo "Restarting DocNova with the latest changes…"
  kill $OLD_PIDS 2>/dev/null; sleep 1
fi
( sleep 2; open 'http://127.0.0.1:4180/' ) &
exec "$RUNTIME_PYTHON" server.py
