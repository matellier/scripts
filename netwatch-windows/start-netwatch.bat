@echo off
REM ============================================================
REM  NetWatch - Windows launcher (uv)
REM  Double-click this file, or run it from a terminal.
REM  For raw ICMP pings, right-click -> "Run as administrator".
REM  Without admin, NetWatch automatically uses the TCP fallback.
REM ============================================================

cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: 'uv' was not found on PATH.
  echo Install it with:  winget install --id=astral-sh.uv  -e
  echo Or see README-WINDOWS.md for the pip/venv alternative.
  pause
  exit /b 1
)

REM Open the dashboard in the default browser once the server is up.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:5000"

echo Starting NetWatch... open http://localhost:5000 in your browser.
uv run app.py

pause
