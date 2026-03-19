@echo off
REM BorgorTube – Full stack startup (Windows)
REM Usage: run.bat [--no-deno] [--port 8000]

setlocal EnableDelayedExpansion

set PORT=8000
set DENO_PORT=8001
set RUN_DENO=true
set SCRIPT_DIR=%~dp0
set BACKEND_DIR=%SCRIPT_DIR%backend
set DENO_DIR=%SCRIPT_DIR%deno

REM ── Parse arguments ─────────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--no-deno" (
    set RUN_DENO=false
    shift
    goto parse_args
)
if /i "%~1"=="--port" (
    set PORT=%~2
    shift
    shift
    goto parse_args
)
shift
goto parse_args
:done_args

echo.
echo +------------------------------------------+
echo ^|         BorgorTube Web Edition           ^|
echo +------------------------------------------+
echo.

REM ── Check Python ────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    where python3 >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] python / python3 not found.
        echo         Install from https://python.org or winget install Python.Python.3
        pause
        exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)

REM ── Check mpv ────────────────────────────────────────────────────────────
where mpv >nul 2>&1
if errorlevel 1 (
    echo [WARN]  mpv not found. MPV pop-out will not work.
    echo         Install: winget install mpv  or  https://mpv.io
)

REM ── Check ffmpeg ─────────────────────────────────────────────────────────
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARN]  ffmpeg not found. HLS in-browser HD streaming will not work.
    echo         Install: winget install Gyan.FFmpeg  or  https://ffmpeg.org
)

REM ── Install Python deps ───────────────────────────────────────────────────
echo [INFO]  Checking Python dependencies (including yt-dlp-ejs challenge solver)...
%PYTHON% -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed. Check requirements.txt.
    pause
    exit /b 1
)

REM ── Start Deno MPV bridge ─────────────────────────────────────────────────
if /i "%RUN_DENO%"=="true" (
    where deno >nul 2>&1
    if errorlevel 1 (
        echo [WARN]  deno not found. MPV real-time sync will not work.
        echo         Install: winget install DenoLand.Deno  or  https://deno.land
    ) else (
        echo [INFO]  Starting Deno MPV bridge on port %DENO_PORT%...
        start "BorgorTube Deno Bridge" /min cmd /c ^
            "set WS_PORT=%DENO_PORT% && set MPV_SOCKET=\\.\pipe\mpvsocket && deno run --allow-net --allow-read --allow-write --allow-env "%DENO_DIR%\ws_bridge.ts""
        echo [INFO]  Deno bridge started in background window.
    )
)

REM ── Start FastAPI backend ─────────────────────────────────────────────────
echo.
echo [INFO]  Starting FastAPI backend on http://localhost:%PORT%
echo [INFO]  Frontend: http://localhost:%PORT%/static/index.html
echo.
echo         Press Ctrl+C to stop.
echo.

cd /d "%BACKEND_DIR%"
REM Workers: auto-detect CPU count (min 2) for concurrent users
for /f %%W in ('%PYTHON% -c "import os; print(max(2, os.cpu_count()))"') do set WORKERS=%%W
if defined BORGORTUBE_UVICORN_WORKERS set WORKERS=%BORGORTUBE_UVICORN_WORKERS%
echo [INFO]  Starting with %WORKERS% workers (set BORGORTUBE_UVICORN_WORKERS to override)
echo.
%PYTHON% -m uvicorn main:app --host 0.0.0.0 --port %PORT% --reload --reload-dir "%BACKEND_DIR%"

endlocal
