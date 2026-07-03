#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$DIR/.venv/bin/python" -m main "$@"
