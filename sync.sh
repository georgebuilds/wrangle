#!/usr/bin/env bash
# Refresh the packaged skill from its canonical copy.
# Source of truth is ~/.agents/skills/wrangle; this repo is the distribution shell.
set -euo pipefail

SRC="${WRANGLE_SRC:-$HOME/.agents/skills/wrangle}"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/wrangle"

if [[ ! -d "$SRC" ]]; then
  echo "sync: no skill at $SRC" >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -R "$SRC" "$DEST"
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "sync: $SRC -> $DEST"
if command -v claude >/dev/null 2>&1; then
  claude plugin validate "$(dirname "$(dirname "$DEST")")" --strict
fi
