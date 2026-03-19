#!/usr/bin/env bash
# BorgorTube – Full stack startup
# Usage: ./run.sh [--no-deno] [--port 8000]

set -euo pipefail

PORT="${BORGORTUBE_PORT:-8000}"
DENO_PORT="${BORGORTUBE_DENO_PORT:-8001}"
RUN_DENO=true

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-deno)   RUN_DENO=false; shift ;;
    --port)      PORT="$2"; shift 2 ;;
    *)           shift ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
DENO_DIR="$SCRIPT_DIR/deno"

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│           BorgorTube Web Edition        │"
echo "└─────────────────────────────────────────┘"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "✗ python3 not found. Install Python 3.10+."
  exit 1
fi

# Check mpv
if ! command -v mpv &>/dev/null; then
  echo "⚠  mpv not found. MPV pop-out will not work."
  echo "   Install: sudo apt install mpv  (or brew install mpv)"
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "⚠  ffmpeg not found. HLS in-browser HD streaming will not work."
  echo "   Install: sudo apt install ffmpeg  (or brew install ffmpeg)"
fi

# Install Python deps if needed
echo "→ Checking Python dependencies…"
pip install -q -r "$SCRIPT_DIR/requirements.txt" --no-deps 2>/dev/null || \
pip install -r "$SCRIPT_DIR/requirements.txt" 2>&1 | tail -3

# Start Deno bridge (optional)
DENO_PID=""
if $RUN_DENO; then
  if command -v deno &>/dev/null; then
    echo "→ Starting Deno MPV bridge on port $DENO_PORT…"
    WS_PORT="$DENO_PORT" deno run \
      --allow-net --allow-read --allow-write --allow-env \
      "$DENO_DIR/ws_bridge.ts" &
    DENO_PID=$!
    echo "  PID $DENO_PID"
  else
    echo "⚠  deno not found. MPV real-time sync will not work."
    echo "   Install: curl -fsSL https://deno.land/install.sh | sh"
  fi
fi

# Cleanup on exit
cleanup() {
  echo ""
  echo "→ Shutting down…"
  [[ -n "$DENO_PID" ]] && kill "$DENO_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# Start FastAPI backend
echo ""
echo "→ Starting FastAPI backend on http://localhost:$PORT"
echo "  Frontend: http://localhost:$PORT/static/index.html"
echo ""
cd "$BACKEND_DIR"
exec python3 -m uvicorn main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload \
  --reload-dir "$BACKEND_DIR"
