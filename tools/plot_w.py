#!/usr/bin/env python3

import argparse
import csv
import json
import math
import os
import select
import struct
import sys
import termios
import time
from collections import deque
from datetime import datetime

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

HEADER = b"\xaf\xfa"
FRAME_SIZE = 175
CRC_OFFSET = 174
W_OFFSET = 166
DRAW_INTERVAL = 1.0 / 15.0
DEFAULT_W_LIMIT = 20.0
DEFAULT_DPI = 220

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>6020 w data</title>
<style>
  html, body { margin: 0; padding: 0; background: #f4f6f8; color: #1f2328;
               font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { padding: 10px 14px 4px; }
  h1 { font-size: 16px; margin: 0 0 4px; font-weight: 600; }
  .hint { font-size: 12px; color: #5c6570; line-height: 1.5; }
  #note { color: #b45309; }
  #toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
             padding: 6px 14px 10px; font-size: 13px; }
  button { font: inherit; padding: 4px 10px; border: 1px solid #c9d0d6;
           border-radius: 4px; background: #fff; cursor: pointer; }
  button:hover { background: #eef2f5; }
  button:active { background: #e2e8ed; }
  label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
  #info { color: #333; font-family: "DejaVu Sans Mono", monospace; font-size: 12px; }
  #wrap { position: relative; margin: 0 14px 14px; height: 64vh; min-height: 360px;
          background: #fff; border: 1px solid #ccd3da; border-radius: 4px;
          box-sizing: border-box; overflow: hidden; }
  #chart { display: block; cursor: crosshair; touch-action: none; }
  #tip { position: absolute; left: 0; top: 0; display: none; pointer-events: none;
         background: rgba(24, 28, 33, .92); color: #fff; font-size: 12px;
         font-family: "DejaVu Sans Mono", monospace; padding: 4px 7px;
         border-radius: 4px; white-space: nowrap; transform: translate(-50%, -130%);
         z-index: 2; }
</style>
</head>
<body>
<header>
  <h1>6020 angular velocity &mdash; interactive</h1>
  <div class="hint">滚轮缩放时间轴 &middot; 拖动平移 &middot; 双击还原 &middot; 鼠标悬停查看精确值</div>
  <div class="hint" id="note">__NOTE__</div>
</header>
<div id="toolbar">
  <button id="zoomIn">放大</button>
  <button id="zoomOut">缩小</button>
  <button id="reset">全览</button>
  <label><input type="checkbox" id="clamp" checked> 限幅 &plusmn;__LIMIT__</label>
  <span id="info"></span>
</div>
<div id="wrap"><canvas id="chart"></canvas><div id="tip"></div></div>
<script>
"use strict";

var DATA = __DATA__;
var LIMIT = __LIMIT__;

var canvas = document.getElementById("chart");
var ctx = canvas.getContext("2d");
var tip = document.getElementById("tip");
var info = document.getElementById("info");
var clampBox = document.getElementById("clamp");
var base = document.createElement("canvas");
var bctx = base.getContext("2d");

var fullStart = DATA.length ? DATA[0][0] : 0;
var fullEnd = DATA.length ? DATA[DATA.length - 1][0] : 1;
var view = { start: fullStart, end: fullEnd };

var W = 0, H = 0, dpr = 1;
var M = { left: 68, right: 20, top: 20, bottom: 40 };
var hoverIndex = -1;
var drag = null;

function plotWidth() { return Math.max(10, W - M.left - M.right); }
function plotHeight() { return Math.max(10, H - M.top - M.bottom); }
function fullSpan() { return Math.max(1e-6, fullEnd - fullStart); }
function viewSpan() { return Math.max(1e-6, view.end - view.start); }

function clampValue(v) {
  if (!clampBox.checked || !(LIMIT > 0)) return v;
  return Math.max(-LIMIT, Math.min(LIMIT, v));
}

function lowerBound(t) {
  var lo = 0, hi = DATA.length;
  while (lo < hi) {
    var mid = (lo + hi) >> 1;
    if (DATA[mid][0] < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function visibleRange() {
  var i0 = lowerBound(view.start);
  if (i0 > 0) i0--;
  var i1 = lowerBound(view.end);
  if (i1 < DATA.length) i1++;
  if (i1 <= i0) i1 = Math.min(DATA.length, i0 + 1);
  return [i0, i1];
}

function xOf(t) {
  return M.left + (t - view.start) / viewSpan() * plotWidth();
}

function yOf(v) {
  return M.top + (1 - (v - yMin) / (yMax - yMin)) * plotHeight();
}

function yOfDraw(v) {
  var y = yOf(v);
  if (y < M.top) return M.top;
  if (y > H - M.bottom) return H - M.bottom;
  return y;
}

var yMin = -1, yMax = 1;

function niceStep(raw) {
  if (!(raw > 0) || !isFinite(raw)) return 1;
  var p = Math.pow(10, Math.floor(Math.log10(raw)));
  var n = raw / p;
  var m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return m * p;
}

function decimalsFor(step) {
  return Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));
}

function nearestIndex(t) {
  var i = lowerBound(t);
  if (i >= DATA.length) i = DATA.length - 1;
  if (i > 0 && Math.abs(DATA[i - 1][0] - t) < Math.abs(DATA[i][0] - t)) i--;
  return i;
}

function drawSeries(c, i0, i1) {
  var count = i1 - i0;
  var pw = plotWidth();
  c.strokeStyle = "#1f77b4";
  c.lineWidth = 1;
  if (count <= pw * 2) {
    var started = false;
    c.beginPath();
    for (var i = i0; i < i1; i++) {
      var w = DATA[i][1];
      if (!isFinite(w)) continue;
      var x = xOf(DATA[i][0]);
      var y = yOfDraw(clampValue(w));
      if (!started) { c.moveTo(x, y); started = true; } else { c.lineTo(x, y); }
    }
    if (started) c.stroke();
    return;
  }
  var cols = Math.max(1, Math.round(pw));
  var mins = new Float64Array(cols + 1);
  var maxs = new Float64Array(cols + 1);
  for (var k = 0; k <= cols; k++) { mins[k] = Infinity; maxs[k] = -Infinity; }
  for (var j = i0; j < i1; j++) {
    var wj = DATA[j][1];
    if (!isFinite(wj)) continue;
    var xj = xOf(DATA[j][0]);
    var col = Math.round(xj - M.left);
    if (col < 0) col = 0; else if (col > cols) col = cols;
    var yj = yOfDraw(clampValue(wj));
    if (yj < mins[col]) mins[col] = yj;
    if (yj > maxs[col]) maxs[col] = yj;
  }
  c.beginPath();
  for (var q = 0; q <= cols; q++) {
    if (mins[q] === Infinity) continue;
    c.moveTo(M.left + q, mins[q]);
    c.lineTo(M.left + q, maxs[q]);
  }
  c.stroke();
}

function drawBase() {
  if (base.width !== canvas.width || base.height !== canvas.height) {
    base.width = canvas.width;
    base.height = canvas.height;
  }
  var c = bctx;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, W, H);
  c.fillStyle = "#ffffff";
  c.fillRect(0, 0, W, H);

  var range = visibleRange();
  var i0 = range[0], i1 = range[1];
  var lo = Infinity, hi = -Infinity;
  var sum = 0, n = 0, realN = 0, clippedInView = 0;
  var clamping = clampBox.checked && LIMIT > 0;
  for (var i = i0; i < i1; i++) {
    var raw = DATA[i][1];
    if (!isFinite(raw)) continue;
    n++;
    if (clamping && Math.abs(raw) > LIMIT) {
      clippedInView++;
      continue;
    }
    if (raw < lo) lo = raw;
    if (raw > hi) hi = raw;
    sum += raw; realN++;
  }
  if (!isFinite(lo) || !isFinite(hi)) {
    lo = clamping ? -LIMIT : -1;
    hi = clamping ? LIMIT : 1;
  }
  var span = hi - lo;
  if (span < 0.2) span = 0.2;
  var pad = span * 0.12;
  yMin = lo - pad;
  yMax = hi + pad;

  var pw = plotWidth(), ph = plotHeight();
  var yStep = niceStep((yMax - yMin) / 5);
  var yStart = Math.ceil(yMin / yStep) * yStep;
  var yDec = decimalsFor(yStep);
  c.font = "12px 'DejaVu Sans Mono', monospace";
  c.textAlign = "right";
  c.textBaseline = "middle";
  c.lineWidth = 1;
  for (var k = 0; ; k++) {
    var yv = yStart + k * yStep;
    if (yv > yMax + 1e-9) break;
    var yy = yOf(yv);
    c.strokeStyle = "#e9edf1";
    c.beginPath(); c.moveTo(M.left, yy); c.lineTo(W - M.right, yy); c.stroke();
    c.fillStyle = "#3c444c";
    c.fillText(yv.toFixed(yDec), M.left - 8, yy);
  }

  var xStep = niceStep(viewSpan() / 6);
  var xStart = Math.ceil(view.start / xStep) * xStep;
  var xDec = decimalsFor(xStep);
  c.textAlign = "center";
  c.textBaseline = "top";
  for (var m = 0; ; m++) {
    var xv = xStart + m * xStep;
    if (xv > view.end + 1e-9) break;
    var xx = xOf(xv);
    c.strokeStyle = "#f0f3f6";
    c.beginPath(); c.moveTo(xx, M.top); c.lineTo(xx, H - M.bottom); c.stroke();
    c.fillStyle = "#3c444c";
    c.fillText(xv.toFixed(xDec), xx, H - M.bottom + 8);
  }

  c.strokeStyle = "#c3cbd3";
  c.strokeRect(M.left, M.top, pw, ph);

  c.save();
  c.beginPath();
  c.rect(M.left, M.top, pw, ph);
  c.clip();
  drawSeries(c, i0, i1);
  c.restore();

  c.save();
  c.translate(15, M.top + ph / 2);
  c.rotate(-Math.PI / 2);
  c.textAlign = "center";
  c.textBaseline = "middle";
  c.fillStyle = "#3c444c";
  c.font = "12px system-ui, sans-serif";
  c.fillText("w (rad/s)", 0, 0);
  c.restore();

  c.textAlign = "center";
  c.textBaseline = "top";
  c.fillStyle = "#3c444c";
  c.font = "12px system-ui, sans-serif";
  c.fillText("time (s)", M.left + pw / 2, H - 20);

  if (n) {
    info.textContent = "visible " + n + " pts   min " + lo.toFixed(4) +
      "   max " + hi.toFixed(4) + "   mean " +
      (realN ? (sum / realN).toFixed(4) : "n/a") +
      (clippedInView ? "   clipped " + clippedInView : "");
  } else {
    info.textContent = "no samples in view";
  }
}

function draw() {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(base, 0, 0, W, H);
  if (hoverIndex < 0 || hoverIndex >= DATA.length) return;
  var t = DATA[hoverIndex][0];
  if (t < view.start || t > view.end) return;
  var raw = DATA[hoverIndex][1];
  var x = xOf(t);
  var y = yOfDraw(clampValue(raw));
  ctx.save();
  ctx.setLineDash([4, 3]);
  ctx.strokeStyle = "rgba(70, 78, 86, .5)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, M.top); ctx.lineTo(x, H - M.bottom); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(M.left, y); ctx.lineTo(W - M.right, y); ctx.stroke();
  ctx.restore();
  ctx.beginPath();
  ctx.arc(x, y, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = "#d62728";
  ctx.fill();
}

function updateHover(clientX, clientY) {
  var rect = canvas.getBoundingClientRect();
  var x = clientX - rect.left;
  var y = clientY - rect.top;
  if (x < M.left || x > W - M.right || y < M.top || y > H - M.bottom) {
    hoverIndex = -1;
    tip.style.display = "none";
    draw();
    return;
  }
  var t = view.start + (x - M.left) / plotWidth() * viewSpan();
  hoverIndex = nearestIndex(t);
  draw();
  var px = xOf(DATA[hoverIndex][0]);
  var py = yOfDraw(clampValue(DATA[hoverIndex][1]));
  tip.textContent = DATA[hoverIndex][0].toFixed(3) + " s   w=" +
    DATA[hoverIndex][1].toFixed(4) + " rad/s";
  tip.style.left = px + "px";
  tip.style.top = py + "px";
  tip.style.display = "block";
}

function resetView() {
  view.start = fullStart;
  view.end = fullEnd;
  hoverIndex = -1;
  tip.style.display = "none";
  drawBase(); draw();
}

function zoomAt(frac, factor) {
  if (fullSpan() <= 1e-6) return;
  var span = viewSpan();
  var ns = span * factor;
  var minSpan = Math.max(1e-4, fullSpan() / 100000);
  ns = Math.max(minSpan, Math.min(fullSpan(), ns));
  var anchor = view.start + frac * span;
  var s = anchor - frac * ns;
  var e = s + ns;
  if (s < fullStart) { e += fullStart - s; s = fullStart; }
  if (e > fullEnd) { s -= e - fullEnd; e = fullEnd; }
  view.start = Math.max(fullStart, s);
  view.end = Math.min(fullEnd, e);
  hoverIndex = -1;
  tip.style.display = "none";
  drawBase(); draw();
}

function resize() {
  var wrap = canvas.parentElement;
  W = Math.max(320, wrap.clientWidth);
  H = Math.max(260, wrap.clientHeight);
  dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  drawBase(); draw();
}

canvas.addEventListener("wheel", function (e) {
  e.preventDefault();
  var rect = canvas.getBoundingClientRect();
  var x = e.clientX - rect.left;
  var frac = Math.max(0, Math.min(1, (x - M.left) / plotWidth()));
  zoomAt(frac, e.deltaY < 0 ? 1 / 1.3 : 1.3);
}, { passive: false });

canvas.addEventListener("pointerdown", function (e) {
  if (e.button !== 0) return;
  drag = { x: e.clientX, start: view.start, end: view.end };
  canvas.setPointerCapture(e.pointerId);
  canvas.style.cursor = "grabbing";
});

canvas.addEventListener("pointermove", function (e) {
  if (drag) {
    var dt = -(e.clientX - drag.x) / plotWidth() * (drag.end - drag.start);
    var s = drag.start + dt;
    var en = drag.end + dt;
    if (s < fullStart) { en += fullStart - s; s = fullStart; }
    if (en > fullEnd) { s -= en - fullEnd; en = fullEnd; }
    view.start = Math.max(fullStart, s);
    view.end = Math.min(fullEnd, en);
    hoverIndex = -1;
    tip.style.display = "none";
    drawBase(); draw();
    return;
  }
  updateHover(e.clientX, e.clientY);
});

canvas.addEventListener("pointerup", function () {
  drag = null;
  canvas.style.cursor = "crosshair";
});

canvas.addEventListener("pointercancel", function () {
  drag = null;
  canvas.style.cursor = "crosshair";
});

canvas.addEventListener("pointerleave", function () {
  if (drag) return;
  hoverIndex = -1;
  tip.style.display = "none";
  draw();
});

canvas.addEventListener("dblclick", resetView);

document.getElementById("zoomIn").addEventListener("click", function () { zoomAt(0.5, 1 / 1.6); });
document.getElementById("zoomOut").addEventListener("click", function () { zoomAt(0.5, 1.6); });
document.getElementById("reset").addEventListener("click", resetView);
clampBox.addEventListener("change", function () { hoverIndex = -1; tip.style.display = "none"; drawBase(); draw(); });
window.addEventListener("resize", resize);

if (DATA.length === 0) {
  document.body.innerHTML = "<p style='padding:20px'>no data</p>";
} else {
  resize();
}
</script>
</body>
</html>
"""


def crc8(data):
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def open_serial(port, baud):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    speed = getattr(termios, "B{}".format(baud))
    attrs[4] = speed
    attrs[5] = speed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def extract_frames(buffer):
    frames = []
    errors = 0
    while True:
        index = buffer.find(HEADER)
        if index < 0:
            if buffer and buffer[-1] == HEADER[0]:
                del buffer[:-1]
            else:
                buffer.clear()
            return frames, errors
        if index:
            del buffer[:index]
        if len(buffer) < FRAME_SIZE:
            return frames, errors
        frame = bytes(buffer[:FRAME_SIZE])
        if crc8(frame[:CRC_OFFSET]) == frame[CRC_OFFSET]:
            frames.append(frame)
            del buffer[:FRAME_SIZE]
        else:
            errors += 1
            del buffer[0]


def clamp_w(value, limit):
    if limit <= 0:
        return value
    return max(-limit, min(limit, value))


def save_interactive_html(history, save_dir, stamp, w_limit):
    data = [[point[0], point[1]] for point in history]
    duration = history[-1][0] - history[0][0]
    note = "共 {} 个样本，时长 {:.2f} s。".format(len(history), duration)
    clipped = 0
    if w_limit > 0:
        clipped = sum(1 for point in history if abs(point[1]) > w_limit)
    if clipped:
        note += (
            " 原始数据中有 {} 个点超出 ±{:g}，图中画在上下边缘，"
            "纵轴按正常数据自适应；悬停可查看原始值。"
        ).format(clipped, w_limit)
    html = (
        HTML_TEMPLATE
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
        .replace("__LIMIT__", "{:g}".format(w_limit))
        .replace("__NOTE__", note)
    )
    path = os.path.join(save_dir, "w_{}.html".format(stamp))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return path


def save_session(history, save_dir, save_csv, w_limit, dpi):
    if not history:
        print("no data received, nothing saved", flush=True)
        return
    os.makedirs(save_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xs = [point[0] for point in history]
    ys = [clamp_w(point[1], w_limit) for point in history]
    clipped = 0
    if w_limit > 0:
        clipped = sum(1 for point in history if abs(point[1]) > w_limit)

    fig = Figure(figsize=(10.5, 5.0))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.plot(xs, ys, linewidth=1.0)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("w (rad/s)")
    title = "6020 angular velocity: {} samples, {:.1f} s".format(
        len(history), xs[-1] - xs[0]
    )
    if clipped:
        title += "  ({} outliers, red lines)".format(clipped)
    ax.set_title(title)
    ax.grid(True)
    ax.margins(x=0.01)

    if w_limit > 0:
        normal = [point[1] for point in history if abs(point[1]) <= w_limit]
    else:
        normal = [point[1] for point in history]
    if normal:
        low = min(normal)
        high = max(normal)
    else:
        low, high = -w_limit, w_limit
    pad = max(0.05, (high - low) * 0.12)
    ax.set_ylim(low - pad, high + pad)
    if clipped:
        for point in history:
            if abs(point[1]) > w_limit:
                ax.axvline(point[0], color="#d62728", linewidth=0.8, alpha=0.7)

    fig.tight_layout()

    png_path = os.path.join(save_dir, "w_{}.png".format(stamp))
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight")
    print("saved plot: {}".format(png_path), flush=True)

    html_path = save_interactive_html(history, save_dir, stamp, w_limit)
    print("saved interactive: {}".format(html_path), flush=True)

    if save_csv:
        csv_path = os.path.join(save_dir, "w_{}.csv".format(stamp))
        with open(csv_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", "w_rad_s"])
            writer.writerows(history)
        print("saved csv: {}".format(csv_path), flush=True)


def draw_plot(fd, window_seconds, save_dir, save_csv, w_limit, dpi):
    plt.ion()
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    fig.canvas.manager.set_window_title("Serial w monitor")
    line, = ax.plot([], [], linewidth=1.2)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("w (rad/s)")
    ax.set_title("6020 angular velocity: waiting for AF FA")
    ax.grid(True)
    ax.set_xlim(0, window_seconds)
    ax.set_ylim(-10, 10)
    fig.tight_layout()
    fig.show()
    fig.canvas.draw()
    fig.canvas.flush_events()

    buffer = bytearray()
    points = deque(maxlen=20000)
    history = []
    frame_times = deque(maxlen=10000)
    start = time.monotonic()
    last_draw = 0.0
    last_flush = start
    last_log = start
    last_w = math.nan
    frame_count = 0
    crc_errors = 0

    try:
        while plt.fignum_exists(fig.number):
            readable, _, _ = select.select([fd], [], [], 0.02)
            if readable:
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    buffer.extend(chunk)
                    frames, errors = extract_frames(buffer)
                    crc_errors += errors
                    for frame in frames:
                        value = struct.unpack_from("<f", frame, W_OFFSET)[0]
                        if not math.isfinite(value):
                            continue
                        now = time.monotonic()
                        frame_count += 1
                        frame_times.append(now)
                        point = (now - start, value)
                        points.append((point[0], clamp_w(value, w_limit)))
                        history.append(point)
                        last_w = value

            now = time.monotonic()
            while frame_times and frame_times[0] < now - 2.0:
                frame_times.popleft()

            if points and now - last_draw >= DRAW_INTERVAL:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                line.set_data(xs, ys)
                newest = xs[-1]
                left = max(0.0, newest - window_seconds)
                ax.set_xlim(left, max(window_seconds, newest))
                visible = [y for x, y in points if x >= left]
                low = min(visible)
                high = max(visible)
                margin = max(0.5, (high - low) * 0.1)
                ax.set_ylim(low - margin, high + margin)
                rate = len(frame_times) / 2.0
                ax.set_title(
                    "6020 w={:+.3f} rad/s   rate={:.1f} Hz".format(last_w, rate)
                )
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                last_draw = now
                last_flush = now

            if now - last_flush >= 0.05:
                fig.canvas.flush_events()
                last_flush = now

            if now - last_log >= 2.0:
                print(
                    "frames={} crc_errors={} last_w={:+.3f} buffered={}".format(
                        frame_count, crc_errors, last_w, len(buffer)
                    ),
                    flush=True,
                )
                last_log = now

            time.sleep(0.005)
    finally:
        save_session(history, save_dir, save_csv, w_limit, dpi)
        plt.close(fig)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="/dev/ttyACM0")
    parser.add_argument("baud", nargs="?", type=int, default=115200)
    parser.add_argument("--window", type=float, default=30.0)
    parser.add_argument(
        "--save-dir",
        default=os.path.join(script_dir, "w_plots"),
        help="directory for saved plot, default: tools/w_plots",
    )
    parser.add_argument("--csv", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--no-csv", action="store_true", help="do not save raw samples as csv"
    )
    parser.add_argument(
        "--w-limit",
        type=float,
        default=DEFAULT_W_LIMIT,
        help="plot values are clamped to +/- this range, default: 20",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help="resolution of the saved png, default: {}".format(DEFAULT_DPI),
    )
    args = parser.parse_args()

    fd = None
    try:
        fd = open_serial(args.port, args.baud)
        print(
            "listening on {} at {} baud, showing last {:.0f}s".format(
                args.port, args.baud, args.window
            ),
            flush=True,
        )
        draw_plot(
            fd,
            args.window,
            args.save_dir,
            not args.no_csv,
            args.w_limit,
            args.dpi,
        )
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print("error: {}".format(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        if fd is not None:
            os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
