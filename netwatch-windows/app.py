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
import io
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, jsonify, render_template_string, request, send_file

# reportlab imports
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Group
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics import renderPDF

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
# Persistent storage file — anchored to this script's directory so the data
# file is found regardless of the working directory (matters on Windows when
# launched via double-click, a .bat, or Task Scheduler).
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "netwatch_data.json")

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
    except (PermissionError, OSError):
        # Fall back to TCP connect timing if no raw socket permission.
        # On Windows, creating a raw ICMP socket without Administrator rights
        # raises OSError (WinError 10013, access denied) rather than a plain
        # PermissionError, so both are caught here.
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
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), encoding="utf-8") as f:
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
# PDF Report Generation
# ──────────────────────────────────────────────────────────────────────────────

# Brand colours (dark theme → print-friendly navy/teal)
C_NAVY   = colors.HexColor("#0d1b2a")
C_TEAL   = colors.HexColor("#007ea7")
C_RED    = colors.HexColor("#c0392b")
C_ORANGE = colors.HexColor("#e67e22")
C_GREEN  = colors.HexColor("#27ae60")
C_LGRAY  = colors.HexColor("#f5f7fa")
C_MGRAY  = colors.HexColor("#dee2e8")
C_DGRAY  = colors.HexColor("#6c757d")
C_WHITE  = colors.white
C_BLACK  = colors.HexColor("#1a1a2e")


def severity_color(loss_pct):
    if loss_pct == 0:   return C_GREEN
    if loss_pct < 5:    return C_ORANGE
    return C_RED


def build_styles():
    base = getSampleStyleSheet()
    styles = {}
    styles["title"] = ParagraphStyle(
        "rpt_title", parent=base["Normal"],
        fontSize=22, textColor=C_WHITE, fontName="Helvetica-Bold",
        spaceAfter=4, alignment=TA_LEFT,
    )
    styles["subtitle"] = ParagraphStyle(
        "rpt_sub", parent=base["Normal"],
        fontSize=10, textColor=colors.HexColor("#b0c4d8"), fontName="Helvetica",
        spaceAfter=2, alignment=TA_LEFT,
    )
    styles["h1"] = ParagraphStyle(
        "rpt_h1", parent=base["Normal"],
        fontSize=13, textColor=C_NAVY, fontName="Helvetica-Bold",
        spaceBefore=14, spaceAfter=6,
    )
    styles["h2"] = ParagraphStyle(
        "rpt_h2", parent=base["Normal"],
        fontSize=10, textColor=C_TEAL, fontName="Helvetica-Bold",
        spaceBefore=10, spaceAfter=4,
    )
    styles["body"] = ParagraphStyle(
        "rpt_body", parent=base["Normal"],
        fontSize=9, textColor=C_BLACK, fontName="Helvetica",
        leading=14, spaceAfter=4,
    )
    styles["small"] = ParagraphStyle(
        "rpt_small", parent=base["Normal"],
        fontSize=8, textColor=C_DGRAY, fontName="Helvetica",
        leading=12,
    )
    styles["mono"] = ParagraphStyle(
        "rpt_mono", parent=base["Normal"],
        fontSize=8, textColor=C_BLACK, fontName="Courier",
        leading=12, leftIndent=12,
    )
    styles["callout_red"] = ParagraphStyle(
        "rpt_callout_red", parent=base["Normal"],
        fontSize=9, textColor=C_RED, fontName="Helvetica-Bold",
        leading=13,
    )
    styles["callout_ok"] = ParagraphStyle(
        "rpt_callout_ok", parent=base["Normal"],
        fontSize=9, textColor=C_GREEN, fontName="Helvetica-Bold",
        leading=13,
    )
    return styles


def header_banner(title_text, subtitle_text):
    """Dark navy header banner with title."""
    d = Drawing(7.5 * inch, 80)
    bg = Rect(0, 0, 7.5 * inch, 80, fillColor=C_NAVY, strokeColor=None)
    accent = Rect(0, 0, 4, 80, fillColor=C_TEAL, strokeColor=None)
    d.add(bg)
    d.add(accent)
    d.add(String(16, 52, title_text,
                 fontName="Helvetica-Bold", fontSize=20, fillColor=C_WHITE))
    d.add(String(16, 34, subtitle_text,
                 fontName="Helvetica", fontSize=9,
                 fillColor=colors.HexColor("#90b4ce")))
    d.add(String(16, 18, "Generated by NetWatch  •  localhost:5000",
                 fontName="Helvetica", fontSize=8,
                 fillColor=colors.HexColor("#607080")))
    return d


def kpi_row(items):
    """
    items = list of (label, value, unit, color) tuples — renders as a KPI table row.
    """
    col_w = 7.5 * inch / len(items)
    data  = [[
        Paragraph(f'<font color="#{c.hexval()[2:8]}" size="18"><b>{v}</b></font>'
                  f'<br/><font color="#6c757d" size="7">{u}</font><br/>'
                  f'<font color="#1a1a2e" size="8">{l}</font>',
                  ParagraphStyle("kpi", alignment=TA_CENTER,
                                 fontName="Helvetica", leading=16))
        for l, v, u, c in items
    ]]
    t = Table(data, colWidths=[col_w] * len(items), rowHeights=[60])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LGRAY),
        ("BOX",        (0, 0), (-1, -1), 0.5, C_MGRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.5, C_MGRAY),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def mini_latency_chart(records, width=7.5 * inch, height=1.8 * inch):
    """Inline latency sparkline chart using reportlab LinePlot."""
    if not records:
        return Spacer(1, height)

    valid = [(i, r["avg_ms"]) for i, r in enumerate(records) if r.get("avg_ms")]
    if len(valid) < 2:
        return Spacer(1, height)

    xs = [p[0] for p in valid]
    ys = [p[1] for p in valid]
    max_y = max(ys) * 1.15 or 10

    d = Drawing(width, height)
    bg = Rect(0, 0, width, height, fillColor=C_LGRAY, strokeColor=C_MGRAY, strokeWidth=0.5)
    d.add(bg)

    # Grid lines
    for frac in [0.25, 0.5, 0.75, 1.0]:
        y = 20 + (height - 30) * frac
        d.add(Line(40, y, width - 10, y,
                   strokeColor=C_MGRAY, strokeWidth=0.5))
        d.add(String(2, y - 4, f"{int(max_y * frac)}",
                     fontName="Helvetica", fontSize=6, fillColor=C_DGRAY))

    # Plot line
    px_w = width - 50
    px_h = height - 30
    pts_x = [40 + (x / max(xs)) * px_w for x in xs]
    pts_y = [20 + (y / max_y) * px_h   for y in ys]

    for i in range(len(pts_x) - 1):
        clr = C_TEAL
        if ys[i] > 150: clr = C_RED
        elif ys[i] > 80: clr = C_ORANGE
        d.add(Line(pts_x[i], pts_y[i], pts_x[i+1], pts_y[i+1],
                   strokeColor=clr, strokeWidth=1.2))

    # X-axis labels (first, mid, last)
    for idx in [0, len(records)//2, len(records)-1]:
        ts = records[idx]["ts"][:16].replace("T", " ")
        px = 40 + (idx / max(len(records)-1, 1)) * px_w
        d.add(String(px - 20, 6, ts,
                     fontName="Helvetica", fontSize=6, fillColor=C_DGRAY))
    d.add(String(2, 6, "ms", fontName="Helvetica", fontSize=6, fillColor=C_DGRAY))
    return d


def mini_loss_chart(records, width=7.5 * inch, height=1.4 * inch):
    """Packet loss bar chart."""
    if not records:
        return Spacer(1, height)

    # Downsample to ~60 bars max
    step = max(1, len(records) // 60)
    sampled = records[::step]

    d = Drawing(width, height)
    bg = Rect(0, 0, width, height, fillColor=C_LGRAY,
              strokeColor=C_MGRAY, strokeWidth=0.5)
    d.add(bg)

    bar_w = (width - 50) / max(len(sampled), 1)
    chart_h = height - 20

    for i, r in enumerate(sampled):
        loss = r.get("loss_pct", 0)
        if loss <= 0:
            continue
        bar_h = (loss / 100) * chart_h
        x = 40 + i * bar_w
        clr = C_ORANGE if loss < 50 else C_RED
        d.add(Rect(x, 14, max(bar_w - 0.5, 1), bar_h,
                   fillColor=clr, strokeColor=None))

    # 100% marker
    d.add(Line(40, 14 + chart_h, width - 10, 14 + chart_h,
               strokeColor=C_MGRAY, strokeWidth=0.5))
    d.add(String(2, 14 + chart_h - 4, "100%",
                 fontName="Helvetica", fontSize=6, fillColor=C_DGRAY))
    d.add(String(2, 14, "0%",
                 fontName="Helvetica", fontSize=6, fillColor=C_DGRAY))
    return d


def outage_table(outage_events, styles):
    """Table of outage/loss events."""
    if not outage_events:
        return Paragraph("No outage events recorded in this period.", styles["body"])

    rows = [[
        Paragraph("<b>Timestamp</b>",   styles["small"]),
        Paragraph("<b>Duration</b>",    styles["small"]),
        Paragraph("<b>Packet Loss</b>", styles["small"]),
        Paragraph("<b>Affected Hosts</b>", styles["small"]),
        Paragraph("<b>Severity</b>",    styles["small"]),
    ]]
    for e in outage_events:
        sev_color = C_RED if e["loss_pct"] >= 50 else C_ORANGE
        rows.append([
            Paragraph(e["ts"][:19].replace("T", " "), styles["mono"]),
            Paragraph(e.get("duration", "—"),          styles["small"]),
            Paragraph(f'{e["loss_pct"]:.0f}%',         styles["small"]),
            Paragraph(e.get("hosts", "—"),             styles["small"]),
            Paragraph(
                f'<font color="#{sev_color.hexval()[2:8]}"><b>{e["severity"]}</b></font>',
                styles["small"]
            ),
        ])

    t = Table(rows, colWidths=[1.6*inch, 0.9*inch, 0.9*inch, 2.5*inch, 1.0*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LGRAY]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_MGRAY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def hourly_table(trend_data, styles):
    """Hourly breakdown table."""
    if not trend_data:
        return Paragraph("No hourly data available.", styles["body"])

    rows = [[
        Paragraph("<b>Hour</b>",       styles["small"]),
        Paragraph("<b>Avg ms</b>",     styles["small"]),
        Paragraph("<b>Max ms</b>",     styles["small"]),
        Paragraph("<b>Avg Loss</b>",   styles["small"]),
        Paragraph("<b>Uptime %</b>",   styles["small"]),
        Paragraph("<b>Samples</b>",    styles["small"]),
    ]]
    for t in trend_data:
        loss    = t.get("avg_loss", 0) or 0
        uptime  = t.get("uptime_pct", 100) or 100
        lc      = severity_color(loss)
        uc      = C_GREEN if uptime >= 99 else (C_ORANGE if uptime >= 95 else C_RED)
        rows.append([
            Paragraph(t["hour"].replace("T", " "), styles["mono"]),
            Paragraph(str(t.get("avg_ms") or "—"),  styles["small"]),
            Paragraph(str(t.get("max_ms") or "—"),  styles["small"]),
            Paragraph(
                f'<font color="#{lc.hexval()[2:8]}"><b>{loss:.1f}%</b></font>',
                styles["small"]
            ),
            Paragraph(
                f'<font color="#{uc.hexval()[2:8]}"><b>{uptime:.1f}%</b></font>',
                styles["small"]
            ),
            Paragraph(str(t.get("samples", 0)), styles["small"]),
        ])

    tbl = Table(rows, colWidths=[1.6*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.8*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LGRAY]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_MGRAY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def speed_table(speed_data, styles):
    """Speed test history table."""
    if not speed_data:
        return Paragraph("No speed test data recorded.", styles["body"])

    rows = [[
        Paragraph("<b>Timestamp</b>",       styles["small"]),
        Paragraph("<b>Download Mbps</b>",   styles["small"]),
        Paragraph("<b>Upload Mbps</b>",     styles["small"]),
        Paragraph("<b>Ping ms</b>",         styles["small"]),
        Paragraph("<b>Server</b>",          styles["small"]),
    ]]
    for s in speed_data:
        rows.append([
            Paragraph(s["ts"][:19].replace("T", " "), styles["mono"]),
            Paragraph(f'{s["download"]:.1f}',         styles["small"]),
            Paragraph(f'{s["upload"]:.1f}',           styles["small"]),
            Paragraph(f'{s["ping"]:.0f}',             styles["small"]),
            Paragraph(s.get("server", "—"),           styles["small"]),
        ])

    tbl = Table(rows, colWidths=[1.7*inch, 1.2*inch, 1.2*inch, 0.9*inch, 2.3*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LGRAY]),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_MGRAY),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def write_isp_narrative(stats, outage_events, trend_data, speed_data, styles, period_label):
    """Generate plain-English ISP complaint summary paragraphs."""
    paragraphs = []
    now_str = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # --- Opening ---
    paragraphs.append(Paragraph(
        f"This report documents internet connection quality issues observed over the "
        f"<b>{period_label}</b>, generated on <b>{now_str}</b> using NetWatch automated "
        f"monitoring software. All data was collected from this subscriber's premises.",
        styles["body"]
    ))
    paragraphs.append(Spacer(1, 6))

    # --- Packet loss summary ---
    avg_loss   = stats.get("avg_loss_pct", 0) or 0
    max_loss   = max((r.get("loss_pct", 0) for r in outage_events), default=0)
    loss_count = len(outage_events)

    if avg_loss > 0:
        paragraphs.append(Paragraph(
            f"<b>Packet Loss:</b> During the reporting period, average packet loss measured "
            f"<font color='#c0392b'><b>{avg_loss:.1f}%</b></font> across three independent "
            f"DNS targets (Google 8.8.8.8, Cloudflare 1.1.1.1, OpenDNS 208.67.222.222). "
            f"A total of <b>{loss_count}</b> packet-loss events were recorded, with a peak "
            f"of <b>{max_loss:.0f}%</b>. Simultaneous loss across all three targets strongly "
            f"indicates the issue originates upstream from the subscriber's equipment.",
            styles["body"]
        ))
    else:
        paragraphs.append(Paragraph(
            "<b>Packet Loss:</b> No packet loss was detected during the reporting period.",
            styles["body"]
        ))
    paragraphs.append(Spacer(1, 4))

    # --- Latency summary ---
    avg_ms = stats.get("avg_latency")
    max_ms = stats.get("max_latency")
    if avg_ms:
        latency_note = ""
        if avg_ms > 150:
            latency_note = " This significantly exceeds acceptable thresholds for broadband service."
        elif avg_ms > 80:
            latency_note = " Elevated latency was observed during portions of the period."
        paragraphs.append(Paragraph(
            f"<b>Latency:</b> Average round-trip latency was <b>{avg_ms:.0f}ms</b> "
            f"(min {stats.get('min_latency', '—')}ms, max {max_ms:.0f}ms, "
            f"jitter ±{stats.get('stdev_latency', 0):.0f}ms).{latency_note}",
            styles["body"]
        ))
        paragraphs.append(Spacer(1, 4))

    # --- Uptime ---
    uptime = stats.get("online_pct", 100) or 100
    outage_count = stats.get("outage_count", 0) or 0
    if uptime < 100:
        paragraphs.append(Paragraph(
            f"<b>Availability:</b> Connection uptime was "
            f"<font color='#c0392b'><b>{uptime:.1f}%</b></font> during the period, "
            f"with {outage_count} complete outage intervals recorded where all targets "
            f"were unreachable simultaneously.",
            styles["body"]
        ))
        paragraphs.append(Spacer(1, 4))

    # --- Speed ---
    if speed_data:
        dl_vals = [s["download"] for s in speed_data]
        ul_vals = [s["upload"]   for s in speed_data]
        avg_dl  = sum(dl_vals) / len(dl_vals)
        avg_ul  = sum(ul_vals) / len(ul_vals)
        paragraphs.append(Paragraph(
            f"<b>Speed Tests:</b> {len(speed_data)} speed test(s) were conducted. "
            f"Average download was <b>{avg_dl:.1f} Mbps</b>, average upload "
            f"<b>{avg_ul:.1f} Mbps</b>.",
            styles["body"]
        ))
        paragraphs.append(Spacer(1, 4))

    # --- Time-of-day pattern ---
    if trend_data:
        worst_hours = sorted(trend_data, key=lambda t: t.get("avg_loss", 0) or 0, reverse=True)[:3]
        worst_hours = [h for h in worst_hours if (h.get("avg_loss") or 0) > 0]
        if worst_hours:
            hour_strs = ", ".join(h["hour"].replace("T", " ") for h in worst_hours)
            paragraphs.append(Paragraph(
                f"<b>Pattern:</b> Packet loss was most concentrated during: "
                f"<b>{hour_strs}</b>. Recurring issues at specific times of day may "
                f"indicate congestion on shared infrastructure.",
                styles["body"]
            ))
            paragraphs.append(Spacer(1, 4))

    # --- Closing request ---
    paragraphs.append(Spacer(1, 6))
    paragraphs.append(HRFlowable(width="100%", thickness=0.5, color=C_MGRAY))
    paragraphs.append(Spacer(1, 6))
    paragraphs.append(Paragraph(
        "<b>Requested Action:</b> Please investigate the upstream path from this "
        "subscriber's connection point. Given that packet loss is observed simultaneously "
        "to multiple independent third-party targets, the issue is unlikely to be caused "
        "by subscriber equipment or in-home wiring. We request a line quality check, "
        "node-level diagnostic, and written response with findings.",
        styles["body"]
    ))

    return paragraphs


def generate_report_pdf(options):
    """
    options: dict with boolean keys:
      include_summary, include_latency_chart, include_loss_chart,
      include_outage_log, include_hourly, include_speed, include_raw_data
      period_minutes: int
    Returns: BytesIO containing PDF
    """
    period_minutes = int(options.get("period_minutes", 1440))
    cutoff = datetime.now() - timedelta(minutes=period_minutes)

    with data_lock:
        window      = [r for r in ping_history
                       if datetime.fromisoformat(r["ts"]) >= cutoff]
        speed_data  = list(speed_history)
        all_trends  = list(ping_history)   # for hourly breakdown

    # ── Compute stats ──
    valid_ms    = [r["avg_ms"]   for r in window if r["avg_ms"] is not None]
    losses      = [r["loss_pct"] for r in window]
    outages     = sum(1 for r in window if not r["online"])
    stats = {
        "samples":       len(window),
        "online_pct":    round((1 - outages / len(window)) * 100, 1) if window else 100,
        "avg_latency":   round(statistics.mean(valid_ms), 1)   if valid_ms else None,
        "min_latency":   min(valid_ms)                          if valid_ms else None,
        "max_latency":   max(valid_ms)                          if valid_ms else None,
        "median_latency":round(statistics.median(valid_ms), 1)  if valid_ms else None,
        "stdev_latency": round(statistics.stdev(valid_ms), 1)   if len(valid_ms) > 1 else 0,
        "avg_loss_pct":  round(statistics.mean(losses), 2)      if losses else 0,
        "outage_count":  outages,
    }

    # ── Build outage events list ──
    outage_events = []
    in_outage     = False
    outage_start  = None
    for r in window:
        if r["loss_pct"] > 0 or not r["online"]:
            failed = [k for k, v in r["targets"].items() if v is None]
            if not in_outage:
                in_outage    = True
                outage_start = r["ts"]
            sev = "COMPLETE" if not r["online"] else ("HIGH" if r["loss_pct"] >= 50 else "PARTIAL")
            outage_events.append({
                "ts":       r["ts"],
                "loss_pct": r["loss_pct"],
                "hosts":    ", ".join(failed) if failed else "partial",
                "duration": "—",
                "severity": sev,
            })
        else:
            in_outage = False

    # ── Hourly trends ──
    buckets = {}
    for r in all_trends:
        if datetime.fromisoformat(r["ts"]) < cutoff:
            continue
        hk = r["ts"][:13]
        buckets.setdefault(hk, []).append(r)
    trend_data = []
    for hk in sorted(buckets):
        b = buckets[hk]
        vm = [x["avg_ms"] for x in b if x["avg_ms"] is not None]
        ls = [x["loss_pct"] for x in b]
        ot = sum(1 for x in b if not x["online"])
        trend_data.append({
            "hour":       hk,
            "avg_ms":     round(statistics.mean(vm), 1) if vm else None,
            "max_ms":     max(vm) if vm else None,
            "avg_loss":   round(statistics.mean(ls), 1) if ls else 0,
            "uptime_pct": round((1 - ot / len(b)) * 100, 1),
            "samples":    len(b),
        })

    # ── Period label ──
    labels = {60:"Last 1 Hour", 180:"Last 3 Hours", 360:"Last 6 Hours",
              1440:"Last 24 Hours", 4320:"Last 3 Days", 10080:"Last 7 Days"}
    period_label = labels.get(period_minutes, f"Last {period_minutes} Minutes")

    # ── Build PDF ──
    buf    = io.BytesIO()
    doc    = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.65*inch, rightMargin=0.65*inch,
        topMargin=0.5*inch,   bottomMargin=0.65*inch,
    )
    styles = build_styles()
    story  = []

    # Header banner
    story.append(header_banner(
        "NetWatch — ISP Issue Report",
        f"Period: {period_label}  •  Subscriber: {socket.gethostname()}  •  {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ))
    story.append(Spacer(1, 10))

    # KPI row
    avg_loss_str = f"{stats['avg_loss_pct']:.1f}%"
    story.append(kpi_row([
        ("Avg Latency",   str(stats["avg_latency"]) if stats["avg_latency"] else "N/A", "ms",       C_TEAL),
        ("Avg Packet Loss", avg_loss_str,                                                "%",        severity_color(stats["avg_loss_pct"])),
        ("Uptime",        f"{stats['online_pct']:.1f}",                                 "%",        C_GREEN if stats["online_pct"] >= 99 else C_RED),
        ("Samples",       str(stats["samples"]),                                         "checks",   C_TEAL),
        ("Outage Events", str(stats["outage_count"]),                                    "intervals",C_RED if stats["outage_count"] > 0 else C_GREEN),
    ]))
    story.append(Spacer(1, 12))

    # ── Executive Summary ──
    if options.get("include_summary", True):
        story.append(Paragraph("Executive Summary", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.extend(write_isp_narrative(stats, outage_events, trend_data, speed_data, styles, period_label))
        story.append(Spacer(1, 10))

    # ── Latency Chart ──
    if options.get("include_latency_chart", True) and window:
        story.append(Paragraph("Latency Over Time", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.append(mini_latency_chart(window))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"Min: {stats['min_latency']}ms  |  Avg: {stats['avg_latency']}ms  |  "
            f"Median: {stats['median_latency']}ms  |  Max: {stats['max_latency']}ms  |  "
            f"Jitter: ±{stats['stdev_latency']}ms",
            styles["small"]
        ))
        story.append(Spacer(1, 10))

    # ── Packet Loss Chart ──
    if options.get("include_loss_chart", True) and window:
        story.append(Paragraph("Packet Loss Over Time", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.append(mini_loss_chart(window))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Each bar represents a 10-second measurement interval. "
            "Orange = partial loss (<50%), Red = severe loss (≥50%).",
            styles["small"]
        ))
        story.append(Spacer(1, 10))

    # ── Outage / Loss Event Log ──
    if options.get("include_outage_log", True):
        story.append(Paragraph("Packet Loss & Outage Event Log", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"{len(outage_events)} event(s) recorded where one or more ping targets were unreachable.",
            styles["body"]
        ))
        story.append(Spacer(1, 6))
        story.append(outage_table(outage_events[:100], styles))
        if len(outage_events) > 100:
            story.append(Paragraph(
                f"… {len(outage_events) - 100} additional events omitted. "
                "See raw data section or netwatch_data.json for full log.",
                styles["small"]
            ))
        story.append(Spacer(1, 10))

    # ── Hourly Breakdown ──
    if options.get("include_hourly", True) and trend_data:
        story.append(Paragraph("Hourly Breakdown", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.append(hourly_table(trend_data, styles))
        story.append(Spacer(1, 10))

    # ── Speed Tests ──
    if options.get("include_speed", True) and speed_data:
        story.append(Paragraph("Speed Test History", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        story.append(speed_table(speed_data, styles))
        story.append(Spacer(1, 10))

    # ── Raw Data Sample ──
    if options.get("include_raw_data", False) and window:
        story.append(Paragraph("Raw Data Sample (last 50 records)", styles["h1"]))
        story.append(HRFlowable(width="100%", thickness=1, color=C_TEAL))
        story.append(Spacer(1, 6))
        for r in window[-50:]:
            ts  = r["ts"][:19].replace("T", " ")
            ms  = r.get("avg_ms", "FAIL")
            lp  = r.get("loss_pct", 0)
            story.append(Paragraph(
                f'{ts}  avg={ms}ms  loss={lp}%',
                styles["mono"]
            ))
        story.append(Spacer(1, 10))

    # ── Footer note ──
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_MGRAY))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Report generated by NetWatch v1.0 on {socket.gethostname()} · "
        f"{datetime.now().isoformat()[:19]} · "
        f"Data file: netwatch_data.json",
        styles["small"]
    ))

    doc.build(story)
    buf.seek(0)
    return buf


@app.route("/api/report", methods=["POST"])
def generate_report():
    options = request.get_json() or {}
    try:
        pdf_buf = generate_report_pdf(options)
        filename = f"netwatch_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        return send_file(
            pdf_buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
