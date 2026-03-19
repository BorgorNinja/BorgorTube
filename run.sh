#!/usr/bin/env bash
# BorgorTube – Full stack startup (Linux / macOS)
# Usage: ./run.sh [--no-deno] [--port 8000]
#
# On every run: detects any existing BorgorTube instance via PID file and
# port check, kills it, then starts fresh. Only one instance runs at a time.

set -euo pipefail

PORT="${BORGORTUBE_PORT:-8000}"
DENO_PORT="${BORGORTUBE_DENO_PORT:-8001}"
RUN_DENO=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-deno) RUN_DENO=false; shift ;;
    --port)    PORT="$2"; shift 2 ;;
    *)         shift ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
DENO_DIR="$SCRIPT_DIR/deno"
PID_FILE="/tmp/borgortube_api.pid"
DENO_PID_FILE="/tmp/borgortube_deno.pid"

# ── Helpers ───────────────────────────────────────────────────────────────
ok()   { echo "[OK]    $*"; }
info() { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│           BorgorTube Web Edition        │"
echo "└─────────────────────────────────────────┘"
echo ""

# ── Kill existing instance ────────────────────────────────────────────────
kill_existing() {
  local label="$1"
  local pid_file="$2"
  local check_port="${3:-}"

  # Method 1: PID file
  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid=$(cat "$pid_file" 2>/dev/null || echo "")
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      info "Found existing $label (PID $old_pid) — killing..."
      kill "$old_pid" 2>/dev/null || true
      # Wait up to 3s for graceful shutdown
      local i=0
      while kill -0 "$old_pid" 2>/dev/null && [[ $i -lt 15 ]]; do
        sleep 0.2; ((i++))
      done
      # Force kill if still alive
      if kill -0 "$old_pid" 2>/dev/null; then
        warn "Process $old_pid did not exit gracefully — force killing..."
        kill -9 "$old_pid" 2>/dev/null || true
      fi
      ok "$label (PID $old_pid) stopped."
    fi
    rm -f "$pid_file"
  fi

  # Method 2: port check (catches instances started without this script)
  if [[ -n "$check_port" ]]; then
    local port_pid=""
    # lsof is most reliable cross-platform
    if command -v lsof &>/dev/null; then
      port_pid=$(lsof -ti tcp:"$check_port" 2>/dev/null | head -1 || echo "")
    elif command -v ss &>/dev/null; then
      port_pid=$(ss -tlnp "sport = :$check_port" 2>/dev/null \
        | grep -oP 'pid=\K[0-9]+' | head -1 || echo "")
    fi

    if [[ -n "$port_pid" ]]; then
      info "Port $check_port in use by PID $port_pid — killing..."
      kill "$port_pid" 2>/dev/null || true
      sleep 0.5
      kill -0 "$port_pid" 2>/dev/null && kill -9 "$port_pid" 2>/dev/null || true
      ok "Process on port $check_port (PID $port_pid) stopped."
    fi
  fi
}

kill_existing "BorgorTube API"  "$PID_FILE"      "$PORT"
kill_existing "BorgorTube Deno" "$DENO_PID_FILE" "$DENO_PORT"

# ── Checks ────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "[ERROR] python3 not found. Install Python 3.10+."; exit 1
fi
command -v mpv    &>/dev/null || warn "mpv not found. MPV pop-out will not work."
command -v ffmpeg &>/dev/null || warn "ffmpeg not found. HLS streaming will not work."

# ── Python deps ───────────────────────────────────────────────────────────
info "Checking Python dependencies…"
pip install -q -r "$SCRIPT_DIR/requirements.txt" --no-deps 2>/dev/null || \
pip install -q -r "$SCRIPT_DIR/requirements.txt" 2>&1 | tail -3

# ── Deno bridge ───────────────────────────────────────────────────────────
DENO_PID=""
if $RUN_DENO && command -v deno &>/dev/null; then
  info "Starting Deno MPV bridge on port $DENO_PORT…"
  WS_PORT="$DENO_PORT" deno run \
    --allow-net --allow-read --allow-write --allow-env \
    "$DENO_DIR/ws_bridge.ts" &
  DENO_PID=$!
  echo "$DENO_PID" > "$DENO_PID_FILE"
  ok "Deno bridge started (PID $DENO_PID)"
elif $RUN_DENO; then
  warn "deno not found. MPV real-time sync will not work."
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "Shutting down…"
  [[ -n "$DENO_PID" ]] && kill "$DENO_PID" 2>/dev/null || true
  rm -f "$PID_FILE" "$DENO_PID_FILE"
  exit 0
}
trap cleanup INT TERM EXIT

# ── Start FastAPI ─────────────────────────────────────────────────────────
WORKERS="${BORGORTUBE_UVICORN_WORKERS:-$(python3 -c "import os; print(max(2, os.cpu_count()))")}"
echo ""
info "Starting FastAPI backend on http://localhost:$PORT"
echo "  Frontend: http://localhost:$PORT/static/index.html"
echo "  Workers:  $WORKERS"
echo ""

cd "$BACKEND_DIR"

if [ "${APP_ENV:-development}" = "production" ]; then
  # In production, write PID of the master uvicorn process
  python3 -m uvicorn main:app \
    --host 0.0.0.0 --port "$PORT" --workers "$WORKERS" &
else
  python3 -m uvicorn main:app \
    --host 0.0.0.0 --port "$PORT" --reload --reload-dir "$BACKEND_DIR" &
fi

UVICORN_PID=$!
echo "$UVICORN_PID" > "$PID_FILE"
ok "API started (PID $UVICORN_PID) — PID file: $PID_FILE"
echo ""
echo "  Press Ctrl+C to stop."
echo ""

# Wait for the uvicorn process
wait "$UVICORN_PID"
