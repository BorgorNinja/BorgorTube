@echo off
REM BorgorTube – One-time setup (Windows)
REM Run this once before your first launch. It installs all Python
REM dependencies including the yt-dlp YouTube JS challenge solver.

setlocal EnableDelayedExpansion

echo.
echo +------------------------------------------+
echo ^|       BorgorTube Setup (Windows)         ^|
echo +------------------------------------------+
echo.

REM ── Find Python ──────────────────────────────────────────────────────────
set PYTHON=
for %%P in (python python3 py) do (
    if not defined PYTHON (
        where %%P >nul 2>&1 && set PYTHON=%%P
    )
)
if not defined PYTHON (
    echo [ERROR] Python 3 not found.
    echo         Install from https://python.org  or  winget install Python.Python.3
    pause & exit /b 1
)

REM Verify Python 3
%PYTHON% -c "import sys; exit(0 if sys.version_info.major==3 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Found Python but it is not Python 3.
    pause & exit /b 1
)
for /f "tokens=*" %%V in ('%PYTHON% --version 2^>^&1') do echo [OK]    Found %%V

REM ── Upgrade pip silently ──────────────────────────────────────────────────
echo [INFO]  Upgrading pip...
%PYTHON% -m pip install --upgrade pip -q

REM ── Install Python dependencies ───────────────────────────────────────────
echo [INFO]  Installing Python dependencies...
%PYTHON% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause & exit /b 1
)
echo [OK]    Python dependencies installed.

REM ── Install Playwright Chromium ────────────────────────────────────────────
echo [INFO]  Installing Playwright Chromium browser (for comment scraping)...
%PYTHON% -m playwright install chromium --with-deps
if errorlevel 1 (
    echo [WARN]  Playwright Chromium install failed. Comments may not load.
    echo         You can retry later with: python -m playwright install chromium
) else (
    echo [OK]    Playwright Chromium installed.
)

REM ── Verify yt-dlp-ejs installed ───────────────────────────────────────────
echo [INFO]  Verifying yt-dlp-ejs (YouTube challenge solver)...
%PYTHON% -c "import yt_dlp_ejs; print('[OK]    yt-dlp-ejs', yt_dlp_ejs.__version__ if hasattr(yt_dlp_ejs,'__version__') else 'installed')" 2>nul
if errorlevel 1 (
    echo [WARN]  yt-dlp-ejs import check failed - trying direct install...
    %PYTHON% -m pip install yt-dlp-ejs
)

REM ── Check optional tools ──────────────────────────────────────────────────
echo.
echo [INFO]  Checking optional tools...

where mpv >nul 2>&1
if errorlevel 1 (
    echo [WARN]  mpv not found. MPV pop-out unavailable.
    echo         Install: winget install mpv
) else (
    echo [OK]    mpv found.
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [WARN]  ffmpeg not found. HLS in-browser HD streaming unavailable.
    echo         Install: winget install Gyan.FFmpeg
) else (
    echo [OK]    ffmpeg found.
)

where deno >nul 2>&1
if errorlevel 1 (
    echo [WARN]  Deno not found. MPV real-time sync unavailable (optional).
    echo         Install: winget install DenoLand.Deno
) else (
    echo [OK]    Deno found.
)

echo.
echo +------------------------------------------+
echo ^|           Setup complete!                ^|
echo ^|   Run run.bat to start BorgorTube        ^|
echo +------------------------------------------+
echo.
pause
