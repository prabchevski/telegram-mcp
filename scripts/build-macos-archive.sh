#!/bin/bash
# Build from an explicit source allowlist; never copy a development environment.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="${1:-$PROJECT_DIR/dist}"
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  BUILD_PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  BUILD_PYTHON="$(command -v python3)"
fi
exec "$BUILD_PYTHON" -I "$PROJECT_DIR/scripts/release.py" build \
  --source "$PROJECT_DIR" --output "$OUTPUT_DIR"
