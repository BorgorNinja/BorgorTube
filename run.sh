#!/usr/bin/env bash
# BorgorTube – Full stack startup (Linux / macOS)
# Usage: ./run.sh [--no-deno] [--port 8000]

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
VENV_DIR="$SCRIPT_DIR/.venv"
PID_FILE="/tmp/borgortube_api.pid"
DENO_PID_FILE="/tmp/borgortube_deno.pid"

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│           BorgorTube Web Edition        │"
echo "└─────────────────────────────────────────┘"
echo ""

# ── Find Python ───────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    echo "[ERROR] Python 3.10+ not found."
    exit 1
fi

# ── Virtualenv setup ──────────────────────────────────────────────────────
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "[INFO]  Creating virtual environment at .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
    echo "[OK]    Virtual environment created."
fi

# Activate venv — from this point, 'python' and 'pip' are the venv ones
source "$VENV_DIR/bin/activate"
echo "[OK]    Virtual environment active ($VIRTUAL_ENV)"

# ── Kill existing instance ────────────────────────────────────────────────
kill_existing() {
    local label="$1" pid_file="$2" check_port="${3:-}"

    if [[ -f "$pid_file" ]]; then
        local old_pid
        old_pid=$(cat "$pid_file" 2>/dev/null || echo "")
        if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
            echo "[INFO]  Found existing $label (PID $old_pid) — killing..."
            kill "$old_pid" 2>/dev/null || true
            local i=0
            while kill -0 "$old_pid" 2>/dev/null && [[ $i -lt 15 ]]; do
                sleep 0.2; ((i++))
            done
            kill -0 "$old_pid" 2>/dev/null && kill -9 "$old_pid" 2>/dev/null || true
            echo "[OK]    $label stopped."
        else
            echo "[INFO]  Stale PID file for $label — clearing."
        fi
        rm -f "$pid_file"
    fi

    if [[ -n "$check_port" ]]; then
        local port_pid=""
        if command -v lsof &>/dev/null; then
            port_pid=$(lsof -ti tcp:"$check_port" 2>/dev/null | head -1 || echo "")
        elif command -v ss &>/dev/null; then
            port_pid=$(ss -tlnp 2>/dev/null | grep ":$check_port " \
                | grep -oP 'pid=\K[0-9]+' | head -1 || echo "")
        fi
        if [[ -n "$port_pid" ]]; then
            echo "[INFO]  Port $check_port in use by PID $port_pid — killing..."
            kill "$port_pid" 2>/dev/null || true
            sleep 0.5
            kill -0 "$port_pid" 2>/dev/null && kill -9 "$port_pid" 2>/dev/null || true
            echo "[OK]    Port $check_port freed."
        fi
    fi
}

echo "[INFO]  Checking for existing BorgorTube instances..."
kill_existing "BorgorTube API"  "$PID_FILE"      "$PORT"
kill_existing "BorgorTube Deno" "$DENO_PID_FILE" "$DENO_PORT"

# ── Optional tool checks ──────────────────────────────────────────────────
command -v mpv    &>/dev/null || echo "[WARN]  mpv not found.    Install: sudo apt install mpv"
command -v ffmpeg &>/dev/null || echo "[WARN]  ffmpeg not found. Install: sudo apt install ffmpeg"

# ── Python deps (into venv, no --break-system-packages needed) ────────────
echo "[INFO]  Installing Python dependencies into venv..."
pip install -q -r "$SCRIPT_DIR/requirements.txt"
echo "[OK]    Dependencies ready."

# Install playwright browser if not already installed
python -c "from playwright.sync_api import sync_playwright; sync_playwright().__enter__().chromium" \
    &>/dev/null 2>&1 || {
    echo "[INFO]  Installing Playwright Chromium..."
    python -m playwright install chromium --with-deps 2>&1 | tail -3
}

# ── Deno bridge ───────────────────────────────────────────────────────────
DENO_BG_PID=""
if $RUN_DENO && command -v deno &>/dev/null; then
    echo "[INFO]  Starting Deno MPV bridge on port $DENO_PORT..."
    WS_PORT="$DENO_PORT" deno run \
        --allow-net --allow-read --allow-write --allow-env \
        "$DENO_DIR/ws_bridge.ts" &
    DENO_BG_PID=$!
    echo "$DENO_BG_PID" > "$DENO_PID_FILE"
    echo "[OK]    Deno bridge started (PID $DENO_BG_PID)"
elif $RUN_DENO; then
    echo "[WARN]  deno not found. MPV real-time sync unavailable."
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "[INFO]  Shutting down..."
    [[ -n "$DENO_BG_PID" ]] && kill "$DENO_BG_PID" 2>/dev/null || true
    rm -f "$PID_FILE" "$DENO_PID_FILE"
    exit 0
}
trap cleanup INT TERM EXIT

# ── Start FastAPI ─────────────────────────────────────────────────────────
WORKERS="${BORGORTUBE_UVICORN_WORKERS:-$(python -c "import os; print(max(2, os.cpu_count()))")}"

echo ""
echo "[INFO]  Starting FastAPI on http://localhost:$PORT"
echo "        Frontend: http://localhost:$PORT/static/index.html"
echo "        Workers:  $WORKERS"
echo "        PID file: $PID_FILE"
echo ""
echo "        Press Ctrl+C to stop."
echo ""

cd "$BACKEND_DIR"

if [ "${APP_ENV:-development}" = "production" ]; then
    python -m uvicorn main:app \
        --host 0.0.0.0 --port "$PORT" --workers "$WORKERS" &
else
    python -m uvicorn main:app \
        --host 0.0.0.0 --port "$PORT" \
        --reload --reload-dir "$BACKEND_DIR" &
fi

UVICORN_PID=$!
echo "$UVICORN_PID" > "$PID_FILE"
echo "[OK]    API started (PID $UVICORN_PID)"

wait "$UVICORN_PID"
