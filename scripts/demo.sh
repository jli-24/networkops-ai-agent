#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    PYTHON=python
fi

cd "$REPO_ROOT"
exec "$PYTHON" -m demo
