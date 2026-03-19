# BorgorTube – Full stack startup (Windows PowerShell)
# Usage:  .\run.ps1 [-NoDeno] [-Port 8000]
#
# Requirements: Python 3.10+, optionally mpv, ffmpeg, deno
# Run once with:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

param(
    [switch]$NoDeno,
    [int]$Port = 8000,
    [int]$DenoPort = 8001
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ScriptDir "backend"
$DenoDir    = Join-Path $ScriptDir "deno"

# ── Helpers ────────────────────────────────────────────────────────────────

function Write-Header {
    Write-Host ""
    Write-Host "+------------------------------------------+" -ForegroundColor DarkGray
    Write-Host "|         BorgorTube Web Edition           |" -ForegroundColor White
    Write-Host "+------------------------------------------+" -ForegroundColor DarkGray
    Write-Host ""
}

function Write-Step  ([string]$msg) { Write-Host "[INFO]  $msg" -ForegroundColor Cyan }
function Write-Warn  ([string]$msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Fail  ([string]$msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Ok    ([string]$msg) { Write-Host "[OK]    $msg" -ForegroundColor Green }

function Test-Command([string]$cmd) {
    $null = Get-Command $cmd -ErrorAction SilentlyContinue
    return $?
}

# ── Header ─────────────────────────────────────────────────────────────────
Write-Header

# ── Python ─────────────────────────────────────────────────────────────────
$PythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Test-Command $candidate) {
        # Verify it's Python 3
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3") {
            $PythonCmd = $candidate
            Write-Ok "Found $ver ($candidate)"
            break
        }
    }
}
if (-not $PythonCmd) {
    Write-Fail "Python 3 not found."
    Write-Host "  Install: winget install Python.Python.3  or  https://python.org" -ForegroundColor Gray
    Read-Host "Press Enter to exit"
    exit 1
}

# ── Optional tools ─────────────────────────────────────────────────────────
if (-not (Test-Command "mpv")) {
    Write-Warn "mpv not found. MPV pop-out will not work."
    Write-Host "  Install: winget install mpv  or  https://mpv.io" -ForegroundColor Gray
}

if (-not (Test-Command "ffmpeg")) {
    Write-Warn "ffmpeg not found. HLS in-browser HD streaming will not work."
    Write-Host "  Install: winget install Gyan.FFmpeg  or  https://ffmpeg.org" -ForegroundColor Gray
}

# ── Python dependencies ────────────────────────────────────────────────────
Write-Step "Checking Python dependencies..."
try {
    & $PythonCmd -m pip install -q -r (Join-Path $ScriptDir "requirements.txt")
    Write-Ok "Dependencies up to date."
} catch {
    Write-Fail "pip install failed: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

# ── Deno MPV bridge ────────────────────────────────────────────────────────
$DenoJob = $null
if (-not $NoDeno) {
    if (Test-Command "deno") {
        Write-Step "Starting Deno MPV bridge on port $DenoPort..."

        # On Windows, mpv uses a named pipe instead of a Unix socket
        $env:WS_PORT      = $DenoPort
        $env:MPV_SOCKET   = "\\.\pipe\mpvsocket"
        $env:POLL_MS      = "1000"

        $DenoBridgePath = Join-Path $DenoDir "ws_bridge.ts"
        $DenoArgs = @(
            "run",
            "--allow-net",
            "--allow-read",
            "--allow-write",
            "--allow-env",
            $DenoBridgePath
        )

        $DenoJob = Start-Process `
            -FilePath "deno" `
            -ArgumentList $DenoArgs `
            -WindowStyle Minimized `
            -PassThru

        Write-Ok "Deno bridge running (PID $($DenoJob.Id))"
    } else {
        Write-Warn "deno not found. MPV real-time sync will not work."
        Write-Host "  Install: winget install DenoLand.Deno  or  https://deno.land" -ForegroundColor Gray
    }
}

# ── Open browser after short delay ────────────────────────────────────────
$AppUrl = "http://localhost:$Port/static/index.html"
Start-Job -ScriptBlock {
    param($url)
    Start-Sleep -Seconds 3
    Start-Process $url
} -ArgumentList $AppUrl | Out-Null

# ── FastAPI backend ────────────────────────────────────────────────────────
Write-Host ""
Write-Step "Starting FastAPI backend on http://localhost:$Port"
Write-Host "  Frontend:  $AppUrl" -ForegroundColor White
Write-Host "  API docs:  http://localhost:$Port/docs" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop all services." -ForegroundColor DarkGray
Write-Host ""

# Register cleanup on Ctrl+C
$CleanupScript = {
    Write-Host ""
    Write-Step "Shutting down..."
    if ($DenoJob -and -not $DenoJob.HasExited) {
        Stop-Process -Id $DenoJob.Id -Force -ErrorAction SilentlyContinue
        Write-Ok "Deno bridge stopped."
    }
}
[Console]::TreatControlCAsInput = $false
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $CleanupScript | Out-Null

try {
    Set-Location $BackendDir
    & $PythonCmd -m uvicorn main:app `
        --host 0.0.0.0 `
        --port $Port `
        --reload `
        --reload-dir $BackendDir
} finally {
    # Cleanup Deno bridge if still running
    if ($DenoJob -and -not $DenoJob.HasExited) {
        Stop-Process -Id $DenoJob.Id -Force -ErrorAction SilentlyContinue
    }
}
