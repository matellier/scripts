# NetWatch on Windows 11

A Windows port of the NetWatch internet-connection monitor. It runs a local
web server (Flask) that pings DNS targets, tracks latency / packet loss / speed,
and serves a live dashboard at <http://localhost:5000>.

This folder is **self-contained and independent** of the Linux copy in
`../netwatch/`. Running this does not touch the instance already running on
Fedora — it keeps its own data file (`netwatch_data.json`) created here on
first run.

## What's different from the Linux version

| Area | Change |
|------|--------|
| Raw ICMP fallback | Catches `OSError` as well as `PermissionError`, so a non-admin Windows run (`WinError 10013`) cleanly falls back to TCP timing. |
| Data file path | Anchored to the script directory, so launching by double-click / `.bat` / Task Scheduler always finds `netwatch_data.json`. |
| Launchers | `start-netwatch.bat` and `start-netwatch.ps1` replace the bash `start.sh`. |
| Dependencies | Dropped the unused `ping3`; relaxed `requires-python` to `>=3.9`. |

The dashboard and all monitoring logic are otherwise identical.

## Prerequisites

- **Python 3.9+** — install from <https://python.org> or `winget install Python.Python.3.12`.
  During the python.org installer, tick **"Add python.exe to PATH"**.
- **uv** (recommended package manager):
  ```powershell
  winget install --id=astral-sh.uv -e
  ```

## Setup & run (recommended — uv)

From a terminal in this folder:

```powershell
uv sync          # creates .venv and installs dependencies from pyproject.toml
uv run app.py    # starts the server
```

Or just **double-click `start-netwatch.bat`** — it runs `uv run app.py` and
opens the dashboard in your browser automatically.

Then open <http://localhost:5000>.

## Setup & run (alternative — pip + venv, no uv)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # PowerShell
REM  (or)  .\.venv\Scripts\activate.bat   in cmd.exe
pip install -r requirements.txt
python app.py
```

## ICMP vs TCP pings (admin)

- **Run as Administrator** → uses raw ICMP (most accurate), matching the
  Linux `sudo` behavior. Right-click `start-netwatch.bat` → **Run as
  administrator**, or open an elevated PowerShell.
- **Run normally** → NetWatch automatically falls back to **TCP connect
  timing** on port 53. Still reliable; no admin needed.

## Windows Firewall

The server binds to `127.0.0.1` (localhost only), so it is not reachable from
other machines and normally triggers no firewall prompt. If Windows Defender
Firewall does prompt the first time Python opens a socket, you can safely allow
it on **Private** networks (or deny — localhost still works either way).

## Optional: run automatically at login (Task Scheduler)

1. Open **Task Scheduler** → **Create Task**.
2. **General**: name it `NetWatch`; check **Run with highest privileges** (for ICMP).
3. **Triggers**: New → **At log on**.
4. **Actions**: New → Program/script:
   - Program: `cmd.exe`
   - Arguments: `/c "C:\path\to\netwatch-windows\start-netwatch.bat"`
   - Start in: `C:\path\to\netwatch-windows`
5. Save. NetWatch will start in the background each time you log in.

## Data & files

- `netwatch_data.json` — ping/speed/alert history; created here on first run,
  persists across restarts. Delete it to reset history.
- Configuration (ping interval, targets, speed-test interval) lives at the top
  of `app.py` under the **Config** section.

## Stopping

Press **Ctrl+C** in the terminal window, or close it. If started via Task
Scheduler, end the `python.exe` task from Task Manager or the scheduler.
