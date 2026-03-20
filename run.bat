@echo off
setlocal EnableDelayedExpansion

set PORT=8000
set DENO_PORT=8001
set RUN_DENO=1
set SCRIPT_DIR=%~dp0
set BACKEND_DIR=%SCRIPT_DIR%backend
set DENO_DIR=%SCRIPT_DIR%deno
set PID_FILE=%TEMP%\borgortube_api.pid
set DENO_PID_FILE=%TEMP%\borgortube_deno.pid

REM ── Parse arguments ───────────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto done_args
if /i "%~1"=="--no-deno" ( set RUN_DENO=0 & shift & goto parse_args )
if /i "%~1"=="--port"    ( set PORT=%~2  & shift & shift & goto parse_args )
shift & goto parse_args
:done_args

echo.
echo +------------------------------------------+
echo ^|         BorgorTube Web Edition           ^|
echo +------------------------------------------+
echo.

REM ── Find Python ───────────────────────────────────────────────────────────
set PYTHON=
where python  >nul 2>&1 && set PYTHON=python
where python3 >nul 2>&1 && if not defined PYTHON set PYTHON=python3
where py      >nul 2>&1 && if not defined PYTHON set PYTHON=py
if not defined PYTHON (
    echo [ERROR] Python 3 not found.
    echo         Install: winget install Python.Python.3
    pause & exit /b 1
)

REM ── Kill existing API by PID file ─────────────────────────────────────────
echo [INFO]  Checking for existing BorgorTube instance...

if exist "%PID_FILE%" (
    set /p OLD_PID=<"%PID_FILE%"
    if defined OLD_PID if not "!OLD_PID!"=="" (
        tasklist /FI "PID eq !OLD_PID!" /NH 2>nul | findstr /I "!OLD_PID!" >nul 2>&1
        if not errorlevel 1 (
            echo [INFO]  Found existing API process PID !OLD_PID! -- killing...
            taskkill /PID !OLD_PID! /F /T >nul 2>&1
            echo [OK]    Stopped PID !OLD_PID!
        ) else (
            echo [INFO]  Stale PID file cleared.
        )
    )
    del "%PID_FILE%" >nul 2>&1
)

REM ── Kill whatever is on PORT (no regex, just plain findstr) ───────────────
netstat -ano 2>nul > "%TEMP%\bt_netstat.tmp"
for /f "tokens=5" %%A in ('findstr ":%PORT% " "%TEMP%\bt_netstat.tmp" ^| findstr "LISTENING"') do (
    set PORT_PID=%%A
    if defined PORT_PID if not "!PORT_PID!"=="0" (
        echo [INFO]  Port %PORT% occupied by PID !PORT_PID! -- killing...
        taskkill /PID !PORT_PID! /F /T >nul 2>&1
        echo [OK]    Freed port %PORT%
    )
)
del "%TEMP%\bt_netstat.tmp" >nul 2>&1

REM ── Kill existing Deno by PID file ────────────────────────────────────────
if exist "%DENO_PID_FILE%" (
    set /p OLD_DENO=<"%DENO_PID_FILE%"
    if defined OLD_DENO if not "!OLD_DENO!"=="" (
        taskkill /PID !OLD_DENO! /F /T >nul 2>&1
        echo [OK]    Stopped old Deno bridge PID !OLD_DENO!
    )
    del "%DENO_PID_FILE%" >nul 2>&1
)

REM ── Kill whatever is on DENO_PORT ─────────────────────────────────────────
netstat -ano 2>nul > "%TEMP%\bt_netstat2.tmp"
for /f "tokens=5" %%A in ('findstr ":%DENO_PORT% " "%TEMP%\bt_netstat2.tmp" ^| findstr "LISTENING"') do (
    set DP=%%A
    if defined DP if not "!DP!"=="0" (
        taskkill /PID !DP! /F /T >nul 2>&1
        echo [OK]    Freed Deno port %DENO_PORT%
    )
)
del "%TEMP%\bt_netstat2.tmp" >nul 2>&1

REM ── Optional tool checks ──────────────────────────────────────────────────
where mpv    >nul 2>&1 || echo [WARN]  mpv not found.    Install: winget install mpv
where ffmpeg >nul 2>&1 || echo [WARN]  ffmpeg not found. Install: winget install Gyan.FFmpeg

REM ── Virtualenv ────────────────────────────────────────────────────────────
if not exist "%SCRIPT_DIR%.venv\Scripts\activate.bat" (
    echo [INFO]  Creating virtual environment...
    %PYTHON% -m venv "%SCRIPT_DIR%.venv"
    echo [OK]    Virtual environment created.
)
call "%SCRIPT_DIR%.venv\Scripts\activate.bat"
echo [OK]    Virtual environment active.

REM ── Python deps ───────────────────────────────────────────────────────────
echo [INFO]  Checking Python dependencies...
pip install -q -r "%SCRIPT_DIR%requirements.txt"
echo [OK]    Dependencies ready.

REM ── Start Deno bridge ─────────────────────────────────────────────────────
if "%RUN_DENO%"=="1" (
    where deno >nul 2>&1
    if not errorlevel 1 (
        echo [INFO]  Starting Deno MPV bridge on port %DENO_PORT%...
        start "BorgorTube Deno Bridge" /min cmd /c "set WS_PORT=%DENO_PORT%&& set MPV_SOCKET=\\.\pipe\mpvsocket&& deno run --allow-net --allow-read --allow-write --allow-env "%DENO_DIR%\ws_bridge.ts""
        timeout /t 2 /nobreak >nul
        REM Grab Deno PID from the port it opened
        netstat -ano 2>nul > "%TEMP%\bt_deno.tmp"
        for /f "tokens=5" %%A in ('findstr ":%DENO_PORT% " "%TEMP%\bt_deno.tmp" ^| findstr "LISTENING"') do (
            echo %%A>"%DENO_PID_FILE%"
            echo [OK]    Deno bridge started PID %%A
        )
        del "%TEMP%\bt_deno.tmp" >nul 2>&1
    ) else (
        echo [WARN]  deno not found. Install: winget install DenoLand.Deno
    )
)

REM ── Worker count ──────────────────────────────────────────────────────────
for /f %%W in ('python -c "import os; print(max(2, os.cpu_count()))"') do set WORKERS=%%W
if defined BORGORTUBE_UVICORN_WORKERS set WORKERS=%BORGORTUBE_UVICORN_WORKERS%

REM ── Start FastAPI ─────────────────────────────────────────────────────────
echo.
echo [INFO]  Starting FastAPI on http://localhost:%PORT%
echo [INFO]  Frontend:  http://localhost:%PORT%/static/index.html
echo [INFO]  Workers:   %WORKERS%
echo [INFO]  PID file:  %PID_FILE%
echo.
echo         Press Ctrl+C to stop.
echo.

cd /d "%BACKEND_DIR%"

start "BorgorTube API" /b python -m uvicorn main:app --host 0.0.0.0 --port %PORT% --reload --reload-dir "%BACKEND_DIR%"

REM Wait a moment then write the PID
timeout /t 3 /nobreak >nul
netstat -ano 2>nul > "%TEMP%\bt_api.tmp"
for /f "tokens=5" %%A in ('findstr ":%PORT% " "%TEMP%\bt_api.tmp" ^| findstr "LISTENING"') do (
    echo %%A>"%PID_FILE%"
    echo [OK]    API started PID %%A -- PID saved to %PID_FILE%
    goto api_started
)
:api_started
del "%TEMP%\bt_api.tmp" >nul 2>&1

echo.
echo [INFO]  BorgorTube is running. Close this window or press any key to stop.
echo.
pause >nul

REM ── Cleanup ───────────────────────────────────────────────────────────────
echo [INFO]  Shutting down...
if exist "%PID_FILE%" (
    set /p KILL_PID=<"%PID_FILE%"
    if defined KILL_PID taskkill /PID !KILL_PID! /F /T >nul 2>&1
    del "%PID_FILE%" >nul 2>&1
)
if exist "%DENO_PID_FILE%" (
    set /p KILL_DENO=<"%DENO_PID_FILE%"
    if defined KILL_DENO taskkill /PID !KILL_DENO! /F /T >nul 2>&1
    del "%DENO_PID_FILE%" >nul 2>&1
)
echo [OK]    Stopped.
endlocal
