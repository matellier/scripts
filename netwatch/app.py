#!/usr/bin/env python3
"""
NetWatch — Internet Connection Monitor
Runs a local web server you open in your browser.
Collects latency, packet loss, and speed data over time.
"""

import threading
import time
import json
import statistics
import os
import socket
import struct
import select
import random
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, jsonify, render_template_string, request

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
PING_INTERVAL_SECONDS = 10          # How often to ping
SPEED_TEST_INTERVAL_MINUTES = 30    # How often to run speed tests
MAX_HISTORY = 2000                  # Max data points to keep in memory
PING_TARGETS = [
    ("8.8.8.8",   "Google DNS"),
    ("1.1.1.1",   "Cloudflare"),
    ("208.67.222.222", "OpenDNS"),
]
DB_FILE = "netwatch_data.json"      # Persistent storage file

# ──────────────────────────────────────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────────────────────────────────────
data_lock = threading.Lock()
ping_history   = deque(maxlen=MAX_HISTORY)
speed_history  = deque(maxlen=200)
alerts         = deque(maxlen=100)
current_status = {
    "online": True,
    "last_ping_ms": None,
    "last_check": None,
    "consecutive_failures": 0,
    "uptime_start": datetime.now().isoformat(),
    "speed_test_running": False,
    "last_speed": None,
}


def save_data():
    """Persist data to JSON file."""
    try:
        payload = {
            "ping_history": list(ping_history),
            "speed_history": list(speed_history),
            "alerts": list(alerts),
        }
        with open(DB_FILE, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass


def load_data():
    """Load persisted data."""
    if not os.path.exists(DB_FILE):
        return
    try:
        with open(DB_FILE) as f:
            payload = json.load(f)
        for item in payload.get("ping_history", []):
            ping_history.append(item)
        for item in payload.get("speed_history", []):
            speed_history.append(item)
        for item in payload.get("alerts", []):
            alerts.append(item)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Ping implementation (raw ICMP via Python stdlib)
# ──────────────────────────────────────────────────────────────────────────────
def checksum(data):
    s = 0
    n = len(data) % 2
    for i in range(0, len(data) - n, 2):
        s += (data[i]) + ((data[i + 1]) << 8)
    if n:
        s += data[-1]
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def raw_ping(host, timeout=2):
    """Returns latency in ms or None on failure."""
    try:
        dest_addr = socket.gethostbyname(host)
    except socket.gaierror:
        return None

    ICMP_ECHO_REQUEST = 8
    my_id = random.randint(1, 65535)
    my_seq = 1

    header = struct.pack("bbHHh", ICMP_ECHO_REQUEST, 0, 0, my_id, my_seq)
    data = b"NetWatch" * 4
    chk = checksum(header + data)
    header = struct.pack("bbHHh", ICMP_ECHO_REQUEST, 0, chk, my_id, my_seq)
    packet = header + data

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.getprotobyname("icmp"))
    except PermissionError:
        # Fall back to TCP connect timing if no raw socket permission
        return tcp_ping(dest_addr, timeout)

    try:
        sock.settimeout(timeout)
        send_time = time.time()
        sock.sendto(packet, (dest_addr, 1))
        ready = select.select([sock], [], [], timeout)
        if ready[0]:
            recv_time = time.time()
            return round((recv_time - send_time) * 1000, 2)
        return None
    except Exception:
        return None
    finally:
        sock.close()


def tcp_ping(host, timeout=2, port=53):
    """TCP connect latency as fallback."""
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        elapsed = (time.time() - start) * 1000
        sock.close()
        return round(elapsed, 2)
    except Exception:
        return None


def measure_latency():
    """Ping all targets, return dict of results."""
    results = {}
    for ip, name in PING_TARGETS:
        results[name] = raw_ping(ip)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Speed test
# ──────────────────────────────────────────────────────────────────────────────
def run_speed_test():
    """Run speedtest-cli and store result."""
    current_status["speed_test_running"] = True
    add_alert("info", "Speed test started…")
    try:
        import speedtest as st
        s = st.Speedtest(secure=True)
        s.get_best_server()
        down = s.download() / 1_000_000   # Mbps
        up   = s.upload()   / 1_000_000
        ping = s.results.ping

        record = {
            "ts":        datetime.now().isoformat(),
            "download":  round(down, 2),
            "upload":    round(up,   2),
            "ping":      round(ping, 2),
            "server":    s.results.server.get("name", "unknown"),
        }
        with data_lock:
            speed_history.append(record)
            current_status["last_speed"] = record
        add_alert("success", f"Speed test done — ↓{down:.1f} Mbps  ↑{up:.1f} Mbps  ping {ping:.0f}ms")
        save_data()
    except Exception as e:
        add_alert("error", f"Speed test failed: {e}")
    finally:
        current_status["speed_test_running"] = False


# ──────────────────────────────────────────────────────────────────────────────
# Alert helpers
# ──────────────────────────────────────────────────────────────────────────────
def add_alert(level, message):
    alerts.appendleft({
        "level":   level,
        "message": message,
        "ts":      datetime.now().isoformat(),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Background monitor loop
# ──────────────────────────────────────────────────────────────────────────────
def monitor_loop():
    last_speed_test = datetime.now() - timedelta(hours=1)   # run soon on start
    ping_count = 0

    while True:
        now = datetime.now()
        results = measure_latency()

        valid = {k: v for k, v in results.items() if v is not None}
        failed = [k for k, v in results.items() if v is None]
        avg_ms = round(statistics.mean(valid.values()), 2) if valid else None

        was_online = current_status["online"]
        online = len(valid) > 0

        record = {
            "ts":      now.isoformat(),
            "targets": results,
            "avg_ms":  avg_ms,
            "online":  online,
            "loss_pct": round(len(failed) / len(PING_TARGETS) * 100, 1),
        }

        with data_lock:
            ping_history.append(record)
            current_status.update({
                "online":       online,
                "last_ping_ms": avg_ms,
                "last_check":   now.isoformat(),
            })

            if not online:
                current_status["consecutive_failures"] += 1
            else:
                current_status["consecutive_failures"] = 0

        # Alerts
        if was_online and not online:
            add_alert("error", "⚠ Connection lost! All ping targets unreachable.")
        elif not was_online and online:
            add_alert("success", "✓ Connection restored.")
        elif avg_ms and avg_ms > 200:
            add_alert("warning", f"High latency: {avg_ms}ms average")
        elif record["loss_pct"] > 0:
            add_alert("warning", f"Packet loss: {record['loss_pct']}% ({', '.join(failed)} unreachable)")

        ping_count += 1
        # Save every 6 pings (~1 min)
        if ping_count % 6 == 0:
            save_data()

        # Speed test scheduler
        if (now - last_speed_test).total_seconds() >= SPEED_TEST_INTERVAL_MINUTES * 60:
            last_speed_test = now
            t = threading.Thread(target=run_speed_test, daemon=True)
            t.start()

        time.sleep(PING_INTERVAL_SECONDS)


# ──────────────────────────────────────────────────────────────────────────────
# Flask app
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html")) as f:
        return f.read()


@app.route("/api/status")
def api_status():
    with data_lock:
        return jsonify(current_status)


@app.route("/api/ping_history")
def api_ping_history():
    minutes = int(request.args.get("minutes", 60))
    cutoff  = datetime.now() - timedelta(minutes=minutes)
    with data_lock:
        filtered = [r for r in ping_history
                    if datetime.fromisoformat(r["ts"]) >= cutoff]
    return jsonify(filtered)


@app.route("/api/speed_history")
def api_speed_history():
    with data_lock:
        return jsonify(list(speed_history))


@app.route("/api/alerts")
def api_alerts():
    limit = int(request.args.get("limit", 20))
    with data_lock:
        return jsonify(list(alerts)[:limit])


@app.route("/api/stats")
def api_stats():
    minutes = int(request.args.get("minutes", 60))
    cutoff  = datetime.now() - timedelta(minutes=minutes)
    with data_lock:
        window = [r for r in ping_history
                  if datetime.fromisoformat(r["ts"]) >= cutoff]

    if not window:
        return jsonify({"error": "no data"})

    valid_ms  = [r["avg_ms"] for r in window if r["avg_ms"] is not None]
    losses    = [r["loss_pct"] for r in window]
    outages   = sum(1 for r in window if not r["online"])

    stats = {
        "samples":        len(window),
        "online_pct":     round((1 - outages / len(window)) * 100, 1),
        "avg_latency":    round(statistics.mean(valid_ms), 2) if valid_ms else None,
        "min_latency":    min(valid_ms) if valid_ms else None,
        "max_latency":    max(valid_ms) if valid_ms else None,
        "median_latency": round(statistics.median(valid_ms), 2) if valid_ms else None,
        "stdev_latency":  round(statistics.stdev(valid_ms), 2) if len(valid_ms) > 1 else 0,
        "avg_loss_pct":   round(statistics.mean(losses), 2),
        "outage_count":   outages,
    }
    return jsonify(stats)


@app.route("/api/speedtest/run", methods=["POST"])
def trigger_speed_test():
    if current_status["speed_test_running"]:
        return jsonify({"error": "already running"}), 409
    t = threading.Thread(target=run_speed_test, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/trends")
def api_trends():
    """Hour-by-hour aggregated data for trend analysis."""
    with data_lock:
        all_data = list(ping_history)

    buckets = {}
    for r in all_data:
        hour_key = r["ts"][:13]  # "2024-01-15T14"
        if hour_key not in buckets:
            buckets[hour_key] = []
        buckets[hour_key].append(r)

    trends = []
    for hour_key in sorted(buckets.keys()):
        bucket = buckets[hour_key]
        valid_ms = [r["avg_ms"] for r in bucket if r["avg_ms"] is not None]
        losses   = [r["loss_pct"] for r in bucket]
        outages  = sum(1 for r in bucket if not r["online"])
        trends.append({
            "hour":         hour_key,
            "avg_ms":       round(statistics.mean(valid_ms), 1) if valid_ms else None,
            "max_ms":       max(valid_ms) if valid_ms else None,
            "avg_loss":     round(statistics.mean(losses), 1),
            "uptime_pct":   round((1 - outages / len(bucket)) * 100, 1),
            "samples":      len(bucket),
        })
    return jsonify(trends)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 56)
    print("  NetWatch — Internet Connection Monitor")
    print("=" * 56)
    load_data()
    print(f"  Loaded {len(ping_history)} historical ping records")
    print(f"  Loaded {len(speed_history)} speed test records")

    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    print("  Monitor started — pinging every", PING_INTERVAL_SECONDS, "seconds")
    print("  Speed tests every", SPEED_TEST_INTERVAL_MINUTES, "minutes")
    print()
    print("  Open your browser → http://localhost:5000")
    print("=" * 56)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
