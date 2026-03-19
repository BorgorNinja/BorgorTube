#!/usr/bin/env bash
# BorgorTube – One-time setup (Linux / macOS)
# Run this once before your first launch.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "+------------------------------------------+"
echo "|      BorgorTube Setup (Linux/macOS)      |"
echo "+------------------------------------------+"
echo ""

ok()   { echo "[OK]    $*"; }
info() { echo "[INFO]  $*"; }
warn() { echo "[WARN]  $*"; }
fail() { echo "[ERROR] $*"; exit 1; }

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

# ── pip ───────────────────────────────────────────────────────────────────
info "Upgrading pip..."
"$PYTHON" -m pip install --upgrade pip -q

# ── Python dependencies ───────────────────────────────────────────────────
info "Installing Python dependencies..."
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt"
ok "Python dependencies installed."

# ── Install Playwright Chromium ──────────────────────────────────────────────
info "Installing Playwright Chromium browser (for comment scraping)..."
if "$PYTHON" -m playwright install chromium --with-deps 2>/dev/null; then
    ok "Playwright Chromium installed."
else
    warn "Playwright Chromium install failed. Comments may not load."
    echo "        Retry: $PYTHON -m playwright install chromium"
fi

# ── Verify yt-dlp-ejs ─────────────────────────────────────────────────────
info "Verifying yt-dlp-ejs (YouTube challenge solver)..."
if "$PYTHON" -c "import yt_dlp_ejs" 2>/dev/null; then
    ok "yt-dlp-ejs installed."
else
    warn "yt-dlp-ejs import failed - retrying direct install..."
    "$PYTHON" -m pip install yt-dlp-ejs
fi

# ── Optional tools ────────────────────────────────────────────────────────
echo ""
info "Checking optional tools..."

if command -v mpv &>/dev/null; then
    ok "mpv found ($(mpv --version | head -1))."
else
    warn "mpv not found. MPV pop-out unavailable."
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "        Install: brew install mpv"
    else
        echo "        Install: sudo apt install mpv"
    fi
fi

if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg found."
else
    warn "ffmpeg not found. HLS in-browser HD streaming unavailable."
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "        Install: brew install ffmpeg"
    else
        echo "        Install: sudo apt install ffmpeg"
    fi
fi

if command -v deno &>/dev/null; then
    ok "Deno found ($(deno --version | head -1))."
else
    warn "Deno not found. MPV real-time sync unavailable (optional)."
    echo "        Install: curl -fsSL https://deno.land/install.sh | sh"
fi

echo ""
echo "+------------------------------------------+"
echo "|           Setup complete!                |"
echo "|   Run ./run.sh to start BorgorTube       |"
echo "+------------------------------------------+"
echo ""
