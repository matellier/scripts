# NetWatch — Internet Connection Monitor

A local desktop app that monitors your internet connection 24/7, logs latency,
packet loss, and speed test results, and displays everything in a live dashboard.

## Requirements

- Python 3.8+
- [uv](https://astral.sh/uv) — modern Python package manager (replaces pip)

## Setup (first time only)

```bash
# 1. Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. In the netwatch/ directory, init the project and add dependencies
uv init --bare
uv add flask ping3 speedtest-cli

# 3. Create the virtual environment
uv venv

# 4. Run the app
uv run app.py
```

Then open http://localhost:5000 in your browser.

## Running after first-time setup

```bash
uv run app.py
```

That's it — `uv` handles the environment automatically from the `pyproject.toml`.

## Features

| Feature | Detail |
|---------|--------|
| Ping monitoring | Every 10 seconds to 3 targets (Google, Cloudflare, OpenDNS) |
| Packet loss tracking | Per-ping, per-host, charted over time |
| Latency stats | Min / Avg / Median / Max / Jitter (std dev) |
| Speed tests | Auto-runs every 30 min; manual trigger in dashboard |
| Trend detection | Detects improving/degrading patterns in recent data |
| Alerts log | All outages, high-latency events, speed test results |
| Hourly trends | Aggregated hourly view for full-day patterns |
| Data persistence | All data saved to `netwatch_data.json` — survives restarts |
| Time range filter | View 30m / 1h / 3h / 6h / 24h windows |

## Configuration (top of app.py)

```python
PING_INTERVAL_SECONDS       = 10   # How often to ping
SPEED_TEST_INTERVAL_MINUTES = 30   # How often to auto-run speed test
MAX_HISTORY                 = 2000 # Max ping records in memory
```

## Ping Method

- Tries raw ICMP first (requires root/admin) for most accurate results
- Falls back to TCP connect timing if no elevated permissions
- Both methods produce reliable latency measurements

## Tips

- **Leave it running all day** — the hourly trend chart fills in over time
- **Run as root/admin** for raw ICMP pings (`sudo uv run app.py`)
- **The data file** `netwatch_data.json` persists between sessions
- Speed tests take 30–60 seconds and use ~50MB of bandwidth each
