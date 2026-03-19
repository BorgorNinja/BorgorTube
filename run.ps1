# BorgorTube – Full stack startup (Windows PowerShell)
# Usage:  .\run.ps1 [-NoDeno] [-Port 8000]
#
# On every run: detects any existing BorgorTube instance via PID file and
# port check, kills it, then starts fresh. Only one instance runs at a time.

param(
    [switch]$NoDeno,
    [int]$Port = 8000,
    [int]$DenoPort = 8001
)

$ErrorActionPreference = "Stop"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir  = Join-Path $ScriptDir "backend"
$DenoDir     = Join-Path $ScriptDir "deno"
$PidFile     = Join-Path $env:TEMP "borgortube_api.pid"
$DenoPidFile = Join-Path $env:TEMP "borgortube_deno.pid"

# ── Helpers ───────────────────────────────────────────────────────────────
function Write-Header { 
    Write-Host ""
    Write-Host "+------------------------------------------+" -ForegroundColor DarkGray
    Write-Host "|         BorgorTube Web Edition           |" -ForegroundColor White
    Write-Host "+------------------------------------------+" -ForegroundColor DarkGray
    Write-Host ""
}
function Write-Step ([string]$m) { Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Write-Ok   ([string]$m) { Write-Host "[OK]    $m" -ForegroundColor Green }
function Write-Warn ([string]$m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Write-Fail ([string]$m) { Write-Host "[ERROR] $m" -ForegroundColor Red }
function Test-Cmd   ([string]$c) { $null = Get-Command $c -ErrorAction SilentlyContinue; return $? }

Write-Header

# ── Kill existing instance ─────────────────────────────────────────────────
function Stop-ExistingInstance {
    param([string]$Label, [string]$PidFilePath, [int]$CheckPort = 0)

    # Method 1: PID file
    if (Test-Path $PidFilePath) {
        $oldPid = (Get-Content $PidFilePath -ErrorAction SilentlyContinue) -as [int]
        if ($oldPid -and $oldPid -gt 0) {
            $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Step "Found existing $Label (PID $oldPid) — killing..."
                try {
                    Stop-Process -Id $oldPid -Force
                    # Wait up to 3s
                    $deadline = (Get-Date).AddSeconds(3)
                    while ((Get-Process -Id $oldPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
                        Start-Sleep -Milliseconds 200
                    }
                    Write-Ok "$Label (PID $oldPid) stopped."
                } catch {
                    Write-Warn "Could not stop PID $oldPid`: $_"
                }
            } else {
                Write-Step "Stale PID file for $Label (PID $oldPid not running) — clearing."
            }
        }
        Remove-Item $PidFilePath -Force -ErrorAction SilentlyContinue
    }

    # Method 2: Port check (catches instances started without this script)
    if ($CheckPort -gt 0) {
        $portOwner = netstat -ano 2>$null |
            Select-String ":$CheckPort\s.*LISTENING" |
            ForEach-Object { ($_ -split '\s+')[-1] } |
            Select-Object -First 1

        if ($portOwner -and ($portOwner -as [int]) -gt 0) {
            $portPid = [int]$portOwner
            $proc = Get-Process -Id $portPid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Step "Port $CheckPort in use by '$($proc.ProcessName)' (PID $portPid) — killing..."
                try {
                    Stop-Process -Id $portPid -Force
                    Start-Sleep -Milliseconds 500
                    Write-Ok "Killed process on port $CheckPort (PID $portPid)."
                } catch {
                    Write-Warn "Could not kill port process: $_"
                }
            }
        }
    }
}

Write-Step "Checking for existing BorgorTube instances..."
Stop-ExistingInstance -Label "BorgorTube API"  -PidFilePath $PidFile     -CheckPort $Port
Stop-ExistingInstance -Label "BorgorTube Deno" -PidFilePath $DenoPidFile -CheckPort $DenoPort

# ── Find Python ───────────────────────────────────────────────────────────
$PythonCmd = $null
foreach ($c in @("python","python3","py")) {
    if (Test-Cmd $c) {
        $v = & $c --version 2>&1
        if ($v -match "Python 3") { $PythonCmd = $c; Write-Ok "Found $v ($c)"; break }
    }
}
if (-not $PythonCmd) {
    Write-Fail "Python 3 not found. Install: winget install Python.Python.3"
    Read-Host "Press Enter to exit"; exit 1
}

# ── Optional checks ────────────────────────────────────────────────────────
if (-not (Test-Cmd "mpv"))    { Write-Warn "mpv not found.    Install: winget install mpv" }
if (-not (Test-Cmd "ffmpeg")) { Write-Warn "ffmpeg not found. Install: winget install Gyan.FFmpeg" }

# ── Python deps ────────────────────────────────────────────────────────────
Write-Step "Checking Python dependencies..."
& $PythonCmd -m pip install -q -r (Join-Path $ScriptDir "requirements.txt")
Write-Ok "Dependencies up to date."

# ── Deno bridge ────────────────────────────────────────────────────────────
$DenoProcess = $null
if (-not $NoDeno -and (Test-Cmd "deno")) {
    Write-Step "Starting Deno MPV bridge on port $DenoPort..."
    $env:WS_PORT    = $DenoPort
    $env:MPV_SOCKET = "\\.\pipe\mpvsocket"
    $env:POLL_MS    = "1000"

    $DenoProcess = Start-Process "deno" `
        -ArgumentList @("run","--allow-net","--allow-read","--allow-write","--allow-env",
                        (Join-Path $DenoDir "ws_bridge.ts")) `
        -WindowStyle Minimized -PassThru

    $DenoProcess.Id | Out-File $DenoPidFile -Encoding ascii
    Write-Ok "Deno bridge started (PID $($DenoProcess.Id)) — PID file: $DenoPidFile"
} elseif (-not $NoDeno) {
    Write-Warn "deno not found. MPV real-time sync unavailable."
}

# ── Worker count ───────────────────────────────────────────────────────────
$Workers = if ($env:BORGORTUBE_UVICORN_WORKERS) {
    [int]$env:BORGORTUBE_UVICORN_WORKERS
} else {
    [math]::Max(2, [System.Environment]::ProcessorCount)
}

# ── Open browser after delay ───────────────────────────────────────────────
$AppUrl = "http://localhost:$Port/static/index.html"
Start-Job -ScriptBlock { param($u); Start-Sleep 3; Start-Process $u } -ArgumentList $AppUrl | Out-Null

# ── Start FastAPI ──────────────────────────────────────────────────────────
Write-Host ""
Write-Step "Starting FastAPI on http://localhost:$Port"
Write-Host "  Frontend: $AppUrl" -ForegroundColor White
Write-Host "  Workers:  $Workers" -ForegroundColor DarkGray
Write-Host "  PID file: $PidFile" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

Set-Location $BackendDir

# Cleanup handler
$CleanupBlock = {
    Write-Host ""
    Write-Host "[INFO]  Shutting down..." -ForegroundColor Cyan
    # Kill API
    if (Test-Path $PidFile) {
        $p = (Get-Content $PidFile) -as [int]
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    # Kill Deno
    if ($DenoProcess -and -not $DenoProcess.HasExited) {
        Stop-Process -Id $DenoProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $DenoPidFile) {
        $p = (Get-Content $DenoPidFile) -as [int]
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
        Remove-Item $DenoPidFile -Force -ErrorAction SilentlyContinue
    }
}
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $CleanupBlock | Out-Null

try {
    $ApiProcess = Start-Process $PythonCmd `
        -ArgumentList @("-m","uvicorn","main:app",
                        "--host","0.0.0.0","--port","$Port",
                        "--reload","--reload-dir",$BackendDir) `
        -PassThru -NoNewWindow

    # Write PID file
    $ApiProcess.Id | Out-File $PidFile -Encoding ascii
    Write-Ok "API started (PID $($ApiProcess.Id)) — PID file: $PidFile"

    # Wait for the process
    $ApiProcess.WaitForExit()
} finally {
    & $CleanupBlock
}
