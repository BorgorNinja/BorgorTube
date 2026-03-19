#!/usr/bin/env bash
# BorgorTube – One-time setup (Linux / macOS)
# Run this once before your first launch, OR just run ./run.sh directly
# (it also sets up the venv on first run).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

ok()   { echo "[OK]    $*"; }
info() { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
fail() { echo "[ERROR] $*"; exit 1; }

echo ""
echo "+------------------------------------------+"
echo "|      BorgorTube Setup (Linux/macOS)      |"
echo "+------------------------------------------+"
echo ""

# ── Python ────────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done
[[ -z "$PYTHON" ]] && fail "Python 3.10+ not found. Install from https://python.org"
ok "Found $($PYTHON --version)"

# ── Virtualenv ────────────────────────────────────────────────────────────
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    info "Creating virtual environment at .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created."
else
    ok "Virtual environment already exists."
fi

source "$VENV_DIR/bin/activate"
ok "Virtual environment active."

# ── Python dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies..."
pip install -q --upgrade pip
pip install -r "$SCRIPT_DIR/requirements.txt"
ok "Python dependencies installed."

# ── Playwright Chromium ───────────────────────────────────────────────────
info "Installing Playwright Chromium browser..."
if python -m playwright install chromium --with-deps 2>/dev/null; then
    ok "Playwright Chromium installed."
else
    warn "Playwright Chromium install failed. Comments may not load."
    echo "  Retry: source .venv/bin/activate && python -m playwright install chromium"
fi

# ── Verify yt-dlp-ejs ────────────────────────────────────────────────────
info "Verifying yt-dlp-ejs..."
python -c "import yt_dlp_ejs; print('[OK]    yt-dlp-ejs ready')" 2>/dev/null \
    || warn "yt-dlp-ejs not importable — challenge solving may not work."

# ── Optional tools ────────────────────────────────────────────────────────
echo ""
info "Checking optional tools..."
if command -v mpv &>/dev/null; then
    ok "mpv found."
else
    warn "mpv not found.    Install: sudo apt install mpv  /  brew install mpv"
fi
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg found."
else
    warn "ffmpeg not found. Install: sudo apt install ffmpeg  /  brew install ffmpeg"
fi
if command -v deno &>/dev/null; then
    ok "Deno found."
else
    warn "Deno not found (optional). Install: curl -fsSL https://deno.land/install.sh | sh"
fi

echo ""
echo "+------------------------------------------+"
echo "|           Setup complete!                |"
echo "|   Run ./run.sh to start BorgorTube       |"
echo "+------------------------------------------+"
echo ""
