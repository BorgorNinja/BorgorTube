@echo off
REM BorgorTube – Full stack startup (Windows)
REM Usage: run.bat [--no-deno] [--port 8000]
REM
REM On every run: detects any existing BorgorTube instance via PID file and
REM port check, kills it, then starts fresh. Only one instance runs at a time.

setlocal EnableDelayedExpansion

set PORT=8000
set DENO_PORT=8001
set RUN_DENO=true
set SCRIPT_DIR=%~dp0
set BACKEND_DIR=%SCRIPT_DIR%backend
set DENO_DIR=%SCRIPT_DIR%deno
set PID_FILE=%TEMP%\borgortube_api.pid
set DENO_PID_FILE=%TEMP%\borgortube_deno.pid

REM ── Parse arguments ───────────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--no-deno" ( set RUN_DENO=false & shift & goto parse_args )
if /i "%~1"=="--port"    ( set PORT=%~2 & shift & shift & goto parse_args )
shift & goto parse_args
:done_args

echo.
echo +------------------------------------------+
echo ^|         BorgorTube Web Edition           ^|
echo +------------------------------------------+
echo.

REM ── Find Python ───────────────────────────────────────────────────────────
set PYTHON=
for %%P in (python python3 py) do (
    if not defined PYTHON (
        where %%P >nul 2>&1 && set PYTHON=%%P
    )
)
if not defined PYTHON (
    echo [ERROR] Python 3 not found.
    echo         Install: winget install Python.Python.3
    pause & exit /b 1
)

REM ── Kill existing API instance ────────────────────────────────────────────
echo [INFO]  Checking for existing BorgorTube instance...

REM Method 1: PID file
if exist "%PID_FILE%" (
    set /p OLD_PID=<"%PID_FILE%"
    if defined OLD_PID (
        REM Check if process is actually running
        tasklist /FI "PID eq !OLD_PID!" 2>nul | find "!OLD_PID!" >nul
        if not errorlevel 1 (
            echo [INFO]  Found existing API process (PID !OLD_PID!) -- killing...
            taskkill /PID !OLD_PID! /F >nul 2>&1
            if errorlevel 1 (
                echo [WARN]  Could not kill PID !OLD_PID! (may have already exited)
            ) else (
                echo [OK]    Killed PID !OLD_PID!
            )
        ) else (
            echo [INFO]  Stale PID file found (process !OLD_PID! not running) -- clearing.
        )
    )
    del "%PID_FILE%" >nul 2>&1
)

REM Method 2: Port check (catches instances started without this script)
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| findstr /R ":%PORT% .*LISTENING"') do (
    set PORT_PID=%%A
    if defined PORT_PID (
        if "!PORT_PID!" neq "0" (
            echo [INFO]  Port %PORT% in use by PID !PORT_PID! -- killing...
            taskkill /PID !PORT_PID! /F >nul 2>&1
            if errorlevel 1 (
                echo [WARN]  Could not kill port process !PORT_PID!
            ) else (
                echo [OK]    Killed process on port %PORT% (PID !PORT_PID!)
            )
        )
    )
)

REM ── Kill existing Deno bridge ─────────────────────────────────────────────
if exist "%DENO_PID_FILE%" (
    set /p OLD_DENO_PID=<"%DENO_PID_FILE%"
    if defined OLD_DENO_PID (
        tasklist /FI "PID eq !OLD_DENO_PID!" 2>nul | find "!OLD_DENO_PID!" >nul
        if not errorlevel 1 (
            echo [INFO]  Found existing Deno bridge (PID !OLD_DENO_PID!) -- killing...
            taskkill /PID !OLD_DENO_PID! /F >nul 2>&1
            echo [OK]    Deno bridge stopped.
        )
    )
    del "%DENO_PID_FILE%" >nul 2>&1
)

REM Kill any leftover Deno process on the bridge port too
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| findstr /R ":%DENO_PORT% .*LISTENING"') do (
    set DENO_PORT_PID=%%A
    if defined DENO_PORT_PID if "!DENO_PORT_PID!" neq "0" (
        echo [INFO]  Port %DENO_PORT% in use by PID !DENO_PORT_PID! -- killing...
        taskkill /PID !DENO_PORT_PID! /F >nul 2>&1
    )
)

REM ── Optional tool checks ──────────────────────────────────────────────────
where mpv    >nul 2>&1 || echo [WARN]  mpv not found.    Install: winget install mpv
where ffmpeg >nul 2>&1 || echo [WARN]  ffmpeg not found. Install: winget install Gyan.FFmpeg

REM ── Python deps ───────────────────────────────────────────────────────────
echo [INFO]  Checking Python dependencies...
%PYTHON% -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 ( echo [ERROR] pip install failed. & pause & exit /b 1 )

REM ── Start Deno bridge ─────────────────────────────────────────────────────
if /i "%RUN_DENO%"=="true" (
    where deno >nul 2>&1
    if not errorlevel 1 (
        echo [INFO]  Starting Deno MPV bridge on port %DENO_PORT%...
        start "BorgorTube Deno Bridge" /min cmd /c ^
            "set WS_PORT=%DENO_PORT% && set MPV_SOCKET=\\.\pipe\mpvsocket && deno run --allow-net --allow-read --allow-write --allow-env "%DENO_DIR%\ws_bridge.ts""

        REM Give it a moment to start, then grab its PID from the port
        timeout /t 2 /nobreak >nul
        for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| findstr /R ":%DENO_PORT% .*LISTENING"') do (
            echo %%A> "%DENO_PID_FILE%"
            echo [OK]    Deno bridge started (PID %%A)
            goto deno_done
        )
        echo [INFO]  Deno bridge started (PID unknown -- bridge may take a moment)
        :deno_done
    ) else (
        echo [WARN]  deno not found. MPV real-time sync will not work.
    )
)

REM ── Start FastAPI ─────────────────────────────────────────────────────────
echo.
echo [INFO]  Starting FastAPI on http://localhost:%PORT%
echo [INFO]  Frontend:  http://localhost:%PORT%/static/index.html
echo.
echo         Press Ctrl+C to stop.
echo.

cd /d "%BACKEND_DIR%"
for /f %%W in ('%PYTHON% -c "import os; print(max(2, os.cpu_count()))"') do set WORKERS=%%W
if defined BORGORTUBE_UVICORN_WORKERS set WORKERS=%BORGORTUBE_UVICORN_WORKERS%
echo [INFO]  Workers: %WORKERS%
echo.

REM Start uvicorn and capture its PID
start /b "" %PYTHON% -m uvicorn main:app --host 0.0.0.0 --port %PORT% --reload --reload-dir "%BACKEND_DIR%"

REM Wait briefly then write PID of the process on that port
timeout /t 3 /nobreak >nul
for /f "tokens=5" %%A in ('netstat -ano 2^>nul ^| findstr /R ":%PORT% .*LISTENING"') do (
    echo %%A> "%PID_FILE%"
    echo [OK]    API started (PID %%A) -- PID file: %PID_FILE%
    goto started
)
echo [INFO]  API starting... (PID file will be written shortly)
:started

REM Keep window alive (uvicorn runs in background via start /b)
echo.
echo [INFO]  BorgorTube is running. Close this window or press Ctrl+C to stop.
echo         PID file: %PID_FILE%
echo.
pause >nul

REM Cleanup on exit
if exist "%PID_FILE%" (
    set /p FINAL_PID=<"%PID_FILE%"
    taskkill /PID !FINAL_PID! /F >nul 2>&1
    del "%PID_FILE%" >nul 2>&1
)
if exist "%DENO_PID_FILE%" (
    set /p FINAL_DENO=<"%DENO_PID_FILE%"
    taskkill /PID !FINAL_DENO! /F >nul 2>&1
    del "%DENO_PID_FILE%" >nul 2>&1
)

endlocal
