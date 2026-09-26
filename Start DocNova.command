#!/bin/bash
cd "$(dirname "$0")" || exit 1
RUNTIME_PYTHON='/Users/drusmanmacbookpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
if [ ! -x "$RUNTIME_PYTHON" ]; then
  RUNTIME_PYTHON=python3
fi
open 'http://127.0.0.1:4173/'
exec "$RUNTIME_PYTHON" server.py
