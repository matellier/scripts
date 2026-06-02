# ============================================================
#  NetWatch - Windows launcher (PowerShell)
#  Run:  powershell -ExecutionPolicy Bypass -File .\start-netwatch.ps1
#  For raw ICMP pings, launch this from an elevated (admin) shell.
#  Without admin, NetWatch automatically uses the TCP fallback.
# ============================================================

Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: 'uv' was not found on PATH." -ForegroundColor Red
    Write-Host "Install it with:  winget install --id=astral-sh.uv -e"
    Write-Host "Or see README-WINDOWS.md for the pip/venv alternative."
    exit 1
}

# Open the dashboard once the server has had a moment to start.
Start-Job -ScriptBlock { Start-Sleep -Seconds 3; Start-Process "http://localhost:5000" } | Out-Null

Write-Host "Starting NetWatch... open http://localhost:5000 in your browser."
uv run app.py
