#!/bin/bash
# NetWatch — start script
# Run this from the netwatch/ directory

cd "$(dirname "$0")"

echo ""
echo "  ███╗   ██╗███████╗████████╗██╗    ██╗ █████╗ ████████╗ ██████╗██╗  ██╗"
echo "  ████╗  ██║██╔════╝╚══██╔══╝██║    ██║██╔══██╗╚══██╔══╝██╔════╝██║  ██║"
echo "  ██╔██╗ ██║█████╗     ██║   ██║ █╗ ██║███████║   ██║   ██║     ███████║"
echo "  ██║╚██╗██║██╔══╝     ██║   ██║███╗██║██╔══██║   ██║   ██║     ██╔══██║"
echo "  ██║ ╚████║███████╗   ██║   ╚███╔███╔╝██║  ██║   ██║   ╚██████╗██║  ██║"
echo "  ╚═╝  ╚═══╝╚══════╝   ╚═╝    ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝╚═╝  ╚═╝"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 not found. Install Python 3.8+"
  exit 1
fi

# Install deps if needed
pip install flask ping3 speedtest-cli --quiet --break-system-packages 2>/dev/null || \
pip install flask ping3 speedtest-cli --quiet 2>/dev/null

# Open browser (works on macOS, Linux, Windows/WSL)
sleep 2 && (
  if command -v open &>/dev/null; then open http://localhost:5000
  elif command -v xdg-open &>/dev/null; then xdg-open http://localhost:5000
  elif command -v start &>/dev/null; then start http://localhost:5000
  fi
) &

# Run with elevated privileges for raw ICMP (needed for accurate pings)
if command -v sudo &>/dev/null && [ "$(id -u)" != "0" ]; then
  echo "  Note: Running without sudo — using TCP fallback for pings (still accurate)"
fi

python3 app.py
