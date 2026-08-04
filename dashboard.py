#!/usr/bin/env python3
"""GRAVES meteor-scatter real-time dashboard - zero-dependency web UI.

Reads the detector's outputs (data/live.csv 1 Hz samples + data/pings.csv
events) and serves a live strip chart + ping log over HTTP. Python stdlib
only - runs on the Pi/laptop acquisition host next to detector.py.

Endpoints:
  GET /              dashboard page (uPlot strip chart, auto-refresh)
  GET /api/live      latest power/floor samples (last ~12 min)
  GET /api/pings     recent events (last 50)
  GET /api/quadrants space weather + meteor shower forecast + station bearing
                     + next ISS pass
  GET /api/iss-hits  recent ISS audio captures (last 20) + clip URLs
  GET /iss-clips/*   WAV clips saved by iss_recorder.py
  GET /api/rate      ping-rate monitor: hourly PING counts (last 48h) + totals
  GET /api/curves    recent ping-curve files + shape stats (rise/decay/class)
  GET /api/curve     one curve's data + stats (?file=NAME.csv, traversal-safe)
  GET /ping-curves   ping-shape gallery page (underdense vs overdense)
  GET /api/sporadic-e  LONG events (sporadic-E / interference catalog)
  GET /sporadic-e    sporadic-E / propagation log page (rate chart + LONG
                     events); alias /sporadic
  GET /api/sstv      decoded ISS SSTV image list
  GET /sstv-images/* decoded SSTV PNGs
  GET /iss-sstv      ISS SSTV gallery page
  GET /api/health    JSON status for external monitors
  GET /status        plain-text health line for cron/scripts

Usage:
  python3 dashboard.py --port 8090 --data-dir data
"""

import argparse
import configparser
import csv
import datetime
import io
import json
import math
import os
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
LIVE_N = 720          # ~12 min at 1 Hz
PINGS_N = 50
TAIL_CHUNK = 256 * 1024

sys.path.insert(0, HERE)
from bearing import bearing_deg, compass_name, distance_km  # noqa: E402 - needs HERE on sys.path first
import satpass  # noqa: E402 - needs HERE on sys.path first

DATA_DIR = os.path.join(HERE, "data")  # replaced by --data-dir in main()

# Station coordinates come from config.ini's [station] section (gitignored,
# per-deployment) - never hardcoded here, so this file is identical whether
# it's run from a private checkout or a shared/public one. GRAVES's own
# coordinates are public knowledge and stay as the default target.
STATION_DEFAULTS = {
    "lat": 0.0, "lon": 0.0,
    "region": "set [station] region in config.ini",
    "target_name": "GRAVES radar",
    "target_lat": 47.351, "target_lon": 5.515,
}
STATION = dict(STATION_DEFAULTS)
BEARING_DEG = 0
DISTANCE_KM = 0
COMPASS_POINT = "N"


def load_station_config(config_path):
    cfg = dict(STATION_DEFAULTS)
    if config_path and os.path.exists(config_path):
        cp = configparser.ConfigParser()
        cp.read(config_path)
        if cp.has_section("station"):
            for k, v in cp["station"].items():
                if k in cfg:
                    cfg[k] = v
    for k in ("lat", "lon", "target_lat", "target_lon"):
        cfg[k] = float(cfg[k])
    return cfg


def apply_station_config(cfg):
    """Set STATION + the derived, deliberately-coarsened bearing/distance.

    Coarsened to the nearest 5 degrees / 100 km: a precise bearing + distance
    from a target whose own coordinates are public (GRAVES, or any other
    well-known radar/beacon) is enough to reverse-geolocate a station to
    street level - a real risk once this page is on a 24/7 public stream.
    Don't "fix" this back to exact values; verified in review that this
    coarsening still leaves a wide (~thousands of km^2) uncertainty region.
    """
    global STATION, BEARING_DEG, DISTANCE_KM, COMPASS_POINT
    STATION = cfg
    exact_bearing = bearing_deg(cfg["lat"], cfg["lon"], cfg["target_lat"], cfg["target_lon"])
    exact_distance = distance_km(cfg["lat"], cfg["lon"], cfg["target_lat"], cfg["target_lon"])
    BEARING_DEG = round(exact_bearing / 5.0) * 5
    DISTANCE_KM = round(exact_distance / 100.0) * 100
    COMPASS_POINT = compass_name(BEARING_DEG)

# (name, month, day, ZHR, radiant constellation) - major annual showers only.
METEOR_SHOWERS = [
    ("Quadrantids", 1, 4, 120, "Boötes"),
    ("Lyrids", 4, 22, 18, "Lyra"),
    ("Eta Aquariids", 5, 5, 50, "Aquarius"),
    ("Delta Aquariids", 7, 30, 25, "Aquarius"),
    ("Perseids", 8, 12, 100, "Perseus"),
    ("Orionids", 10, 21, 20, "Orion"),
    ("Leonids", 11, 17, 15, "Leo"),
    ("Geminids", 12, 14, 150, "Gemini"),
    ("Ursids", 12, 22, 10, "Ursa Minor"),
]

# Space weather is the one thing on this page that needs the internet - it
# only exists on NOAA's servers. Fetched in a background thread and cached;
# every other feature of this station stays fully offline-capable.
SPACE_WEATHER_URLS = {
    "kp": "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
    "wind": "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json",
}
SPACE_WEATHER_INTERVAL_S = 300
SPACE_WEATHER_STALE_S = 1800  # older than this reads as "no data", not stale

_sw_lock = threading.Lock()
_sw_cache = {"kp": None, "wind_speed": None, "fetched_at": None}


def next_shower(now=None):
    """Nearest shower to `now`: either currently active (within +-1.5 days
    of peak) or the soonest upcoming one. Pure calendar math, no internet."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    candidates = []
    for name, month, day, zhr, radiant in METEOR_SHOWERS:
        for year in (now.year, now.year + 1):
            peak = datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)
            delta_days = (peak - now).total_seconds() / 86400.0
            if delta_days >= -1.5:
                candidates.append((delta_days, name, peak, zhr, radiant))
                break  # nearest occurrence of this shower is enough
    if not candidates:
        return None
    delta_days, name, peak, zhr, radiant = min(candidates, key=lambda c: c[0])
    return {
        "name": name,
        "radiant": radiant,
        "zhr": zhr,
        "peak_utc": peak.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_until": round(delta_days, 1),
        "active": -1.5 <= delta_days <= 1.5,
    }


def kp_condition(kp):
    """NOAA G-scale style label + severity bucket for a Kp index value."""
    if kp < 5:
        return ("Unsettled" if kp >= 4 else "Quiet"), "good"
    if kp < 6:
        return "Minor storm (G1)", "warning"
    if kp < 7:
        return "Moderate storm (G2)", "serious"
    if kp < 8:
        return "Strong storm (G3)", "critical"
    if kp < 9:
        return "Severe storm (G4)", "critical"
    return "Extreme storm (G5)", "critical"


SPACE_WEATHER_MAX_BYTES = 1024 * 1024  # 1 MiB - NOAA responses are a few KB


def _fetch_json(url):
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read(SPACE_WEATHER_MAX_BYTES))


def fetch_space_weather():
    """Best-effort refresh of the NOAA SWPC cache. Failures (including an
    unexpected response shape - not just network errors) are swallowed so a
    NOAA hiccup can never take down this background thread; the previous
    good value, if any, just ages until it reads as unavailable."""
    kp = wind = None
    try:
        data = _fetch_json(SPACE_WEATHER_URLS["kp"])
        if data:
            last = data[-1]
            kp = float(last.get("kp_index", last.get("estimated_kp")))
    except Exception as exc:
        print(f"[graves-dashboard] space weather (kp) fetch failed: {exc}", file=sys.stderr)
    try:
        data = _fetch_json(SPACE_WEATHER_URLS["wind"])
        if data:
            wind = float(data[-1].get("proton_speed"))
    except Exception as exc:
        print(f"[graves-dashboard] space weather (wind) fetch failed: {exc}", file=sys.stderr)
    if kp is not None or wind is not None:
        with _sw_lock:
            if kp is not None:
                _sw_cache["kp"] = kp
            if wind is not None:
                _sw_cache["wind_speed"] = wind
            _sw_cache["fetched_at"] = time.time()


def space_weather_loop():
    while True:
        try:
            fetch_space_weather()
        except Exception as exc:  # never let this daemon thread die silently
            print(f"[graves-dashboard] space weather loop error: {exc}", file=sys.stderr)
        time.sleep(SPACE_WEATHER_INTERVAL_S)


def space_weather_payload():
    with _sw_lock:
        kp, wind, fetched_at = (_sw_cache["kp"], _sw_cache["wind_speed"],
                                 _sw_cache["fetched_at"])
    age = (time.time() - fetched_at) if fetched_at else None
    available = age is not None and age < SPACE_WEATHER_STALE_S and kp is not None
    condition, severity = kp_condition(kp) if available else (None, None)
    return {
        "available": available,
        "kp": kp if available else None,
        "kp_condition": condition,
        "severity": severity,
        "wind_speed_km_s": wind if available else None,
        "age_s": round(age, 0) if age is not None else None,
    }


ISS_INTERVAL_S = 60  # cheap to recompute (TLE itself is disk-cached for hours);
                      # this cadence just keeps the AOS countdown feeling live

_iss_lock = threading.Lock()
_iss_cache = {"payload": {"available": False}}


def iss_loop():
    """Background refresh of the ISS next-pass prediction, same shape as
    space_weather_loop() - keeps the ~300ms orbital-math call off request
    threads, and a Celestrak/math hiccup just leaves the last good value in
    place instead of taking the tile down."""
    while True:
        try:
            payload = satpass.next_pass_payload(
                "iss", STATION["lat"], STATION["lon"], cache_dir=DATA_DIR)
            with _iss_lock:
                _iss_cache["payload"] = payload
        except Exception as exc:  # never let this daemon thread die silently
            print(f"[graves-dashboard] ISS pass loop error: {exc}", file=sys.stderr)
        time.sleep(ISS_INTERVAL_S)


def iss_payload():
    with _iss_lock:
        return _iss_cache["payload"]


def quadrants_payload():
    return {
        "space_weather": space_weather_payload(),
        "meteor_shower": next_shower(),
        "bearing_deg": BEARING_DEG,
        "iss_pass": iss_payload(),
    }

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>STARFALL SENTINEL — GRAVES Meteor Watch</title>
<link rel="stylesheet" href="/static/uPlot.min.css">
<script src="/static/uPlot.iife.min.js"></script>
<style>
/* Dark-only by deliberate choice: this page is designed as a 24/7 stream
   overlay / OBS browser source, not a general document, so it commits to one
   look rather than following the viewer's light/dark preference. */
:root{
  --page:      #0d0d0d;
  --surface:   #1a1a19;
  --ink:       #ffffff;
  --ink-2:     #c3c2b7;
  --ink-muted: #898781;
  --grid:      #2c2c2a;
  --baseline:  #383835;
  --border:    rgba(255,255,255,0.10);
  --series-level: #3987e5;  /* categorical slot 1 - blue */
  --series-floor: #199e70;  /* categorical slot 3 - aqua */
  --good:     #0ca30c;
  --warning:  #fab219;
  --serious:  #ec835a;
  --critical: #d03b3b;
  /* LCARS-lite decorative accent only — never used to encode data, so it
     doesn't need CVD validation against the series/status palette above. */
  --lcars-amber: #d9963c;
  --mono: ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
}
*{box-sizing:border-box}
body{background:var(--page);color:var(--ink-2);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;padding:16px 18px 24px}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;align-items:center;gap:14px;margin-bottom:6px}
.headline{flex:1;min-width:0}
.head-controls{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:6px}
.stardate{font-family:var(--mono);font-size:10.5px;color:var(--ink-muted);letter-spacing:1px;white-space:nowrap}
#sound-btn{background:none;border:1px solid var(--border);color:var(--ink-2);
  border-radius:12px;padding:3px 11px;font-size:11px;letter-spacing:.5px;cursor:pointer;
  font-family:var(--mono)}
#sound-btn:hover{border-color:var(--series-level)}
.radar{position:relative;width:34px;height:34px;border-radius:50%;flex:none;
  background:radial-gradient(circle at center, rgba(57,135,229,.15) 0%, transparent 70%);
  border:1px solid var(--border)}
.radar::before{content:"";position:absolute;inset:0;border-radius:50%;
  background:conic-gradient(from 0deg, rgba(57,135,229,.9), transparent 35%);
  animation:sweep 3s linear infinite}
.radar::after{content:"";position:absolute;inset:9px;border-radius:50%;background:var(--series-level);
  box-shadow:0 0 8px 2px var(--series-level)}
@keyframes sweep{to{transform:rotate(360deg)}}
h1{font-size:26px;letter-spacing:4px;color:var(--ink);margin:0;font-weight:800;
  text-shadow:0 0 18px rgba(57,135,229,.45)}
.subtitle{color:var(--ink-muted);font-size:12.5px;letter-spacing:1px;margin:2px 0 0}
.meta{color:var(--ink-2);font-size:12.5px;margin:8px 0 6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;box-shadow:0 0 6px currentColor}
.ok{background:var(--good);color:var(--good)}
.bad{background:var(--critical);color:var(--critical)}
.warn{background:var(--warning);color:var(--warning)}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px}
#chart-wrap{position:relative}
#chart{max-width:100%}
#tooltip{position:absolute;display:none;pointer-events:none;background:#000;border:1px solid var(--border);
  border-radius:5px;padding:6px 9px;font-size:11.5px;color:var(--ink);white-space:nowrap;z-index:5}
#iss-hits audio{height:28px;max-width:230px;vertical-align:middle}
.legend-row{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--ink-2)}
.chip{display:inline-flex;align-items:center;gap:6px}
.swatch{width:14px;height:3px;border-radius:2px;display:inline-block}
.swatch.dash{background:repeating-linear-gradient(90deg,var(--series-floor) 0 5px,transparent 5px 8px)}

.quadrants{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-top:6px}
.quad{display:flex;flex-direction:column;gap:6px;min-height:135px;padding:10px 14px;transition:box-shadow .6s}
.quad-label{border-left:3px solid var(--lcars-amber);padding-left:8px;color:var(--ink-muted);
  text-transform:uppercase;font-size:10.5px;letter-spacing:1.5px;font-weight:600}
.quad-body{display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px;flex:1;justify-content:center}
.quad-num{font-family:var(--mono);font-size:26px;font-weight:700;color:var(--ink);font-variant-numeric:tabular-nums}
.quad-unit{font-size:12px;color:var(--ink-muted);margin-left:3px}
.quad-desc{font-size:12.5px;color:var(--ink-2)}
.quad-sub{font-size:11px;color:var(--ink-muted);font-family:var(--mono)}
.quad-badge{display:inline-block;background:rgba(217,150,60,.18);color:var(--lcars-amber);
  border-radius:10px;padding:1px 8px;font-size:10.5px;font-weight:700;letter-spacing:.5px}

/* Kp gauge: 9 segments, filled/colored up to the current reading */
.kp-gauge{display:flex;gap:3px;width:100%;max-width:200px}
.kp-seg{flex:1;height:14px;border-radius:2px;background:var(--grid);transition:background .5s}
@keyframes solarPulse{0%,100%{box-shadow:0 0 0 0 rgba(250,178,25,0)}50%{box-shadow:0 0 14px 2px rgba(250,178,25,.35)}}
.quad.severity-warning{animation:solarPulse 3s ease-in-out infinite}
@keyframes solarPulseSerious{0%,100%{box-shadow:0 0 0 0 rgba(236,131,90,0)}50%{box-shadow:0 0 16px 3px rgba(236,131,90,.4)}}
.quad.severity-serious{animation:solarPulseSerious 2.4s ease-in-out infinite}
@keyframes solarPulseCritical{0%,100%{box-shadow:0 0 0 0 rgba(208,59,59,0)}50%{box-shadow:0 0 20px 4px rgba(208,59,59,.5)}}
.quad.severity-critical{animation:solarPulseCritical 1.6s ease-in-out infinite}

/* Meteor shower radiant: simple rotating/pulsing burst, pure CSS */
.shower-burst{width:64px;height:64px;opacity:.85}
.shower-burst line{stroke:var(--lcars-amber);stroke-width:2;stroke-linecap:round;transform-origin:32px 32px;
  animation:radiantPulse 2.4s ease-in-out infinite}
.shower-burst circle{fill:var(--lcars-amber)}
@keyframes radiantPulse{0%,100%{opacity:.35;stroke-dashoffset:0}50%{opacity:1;stroke-dashoffset:-3}}
.shower-burst line:nth-of-type(1){animation-delay:0s}
.shower-burst line:nth-of-type(2){animation-delay:.3s}
.shower-burst line:nth-of-type(3){animation-delay:.6s}
.shower-burst line:nth-of-type(4){animation-delay:.9s}
.shower-burst line:nth-of-type(5){animation-delay:1.2s}
.shower-burst line:nth-of-type(6){animation-delay:1.5s}
.shower-burst line:nth-of-type(7){animation-delay:1.8s}
.shower-burst line:nth-of-type(8){animation-delay:2.1s}

/* ISS pass icon: a dot orbiting a "planet", pure CSS/SVG - same trick as
   the header radar sweep (rotate a child element on a timer) */
.sat-orbit{width:64px;height:64px;opacity:.9}
.sat-orbit .planet{fill:var(--surface);stroke:var(--ink-muted);stroke-width:1.5}
.sat-orbit .orbit-path{fill:none;stroke:var(--border);stroke-width:1;stroke-dasharray:2 3}
.sat-orbit .orbit-spin{transform-origin:32px 32px;animation:issOrbit 6s linear infinite}
.sat-orbit .sat-dot{fill:var(--lcars-amber)}
@keyframes issOrbit{to{transform:rotate(360deg)}}

/* GRAVES bearing compass */
.compass{width:88px;height:88px}
.compass circle.face{fill:none;stroke:var(--border);stroke-width:1.5}
.compass text{fill:var(--ink-muted);font-size:8px;font-family:var(--mono)}
.compass-needle{transform-origin:50px 50px;animation:needleWobble 4s ease-in-out infinite}
.compass-needle polygon{fill:var(--series-level)}
.compass-needle circle{fill:var(--ink)}
@keyframes needleWobble{
  0%,100%{transform:rotate(calc(var(--base-rot) - 1.1deg))}
  50%{transform:rotate(calc(var(--base-rot) + 1.1deg))}
}

/* LCARS-style swept-corner section tag: light Trek touch, decorative only */
h2{display:inline-block;background:var(--lcars-amber);color:#1a1200;font-weight:800;
  font-size:11.5px;letter-spacing:1.5px;text-transform:uppercase;
  padding:3px 14px 3px 18px;border-radius:12px 4px 4px 12px;margin:14px 0 6px}
.explainer p{margin:0 0 6px;font-size:12.5px;line-height:1.45}
.explainer p:last-child{margin-bottom:0}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:700;letter-spacing:.5px}
.badge.PING{background:rgba(12,163,12,.18);color:#5fe15f}
.badge.LONG{background:rgba(250,178,25,.18);color:var(--warning)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid)}
th{color:var(--ink-muted);font-weight:600;font-size:11px;letter-spacing:.5px;text-transform:uppercase}
td.num{font-variant-numeric:tabular-nums;font-family:var(--mono)}
.empty{color:var(--ink-muted);padding:10px 0;font-size:12px}
footer{margin-top:16px;color:var(--ink-muted);font-size:11px;line-height:1.5}
footer a{color:var(--series-level)}
/* brief amber pulse on the chart panel when a new ping is detected */
@keyframes contactFlash{0%{box-shadow:0 0 0 0 rgba(217,150,60,.65)}100%{box-shadow:0 0 0 16px rgba(217,150,60,0)}}
.contact-flash{animation:contactFlash .8s ease-out}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="radar"></div>
  <div class="headline">
    <h1>STARFALL SENTINEL</h1>
    <!-- __REGION__ comes from config.ini's [station] section. Keep it
         region-level only (e.g. "Example Region, Country"), never a postcode or
         house-level detail: this page runs 24/7 on a public stream, and
         anything more precise permanently doxxes the residence. -->
    <div class="subtitle">GRAVES meteor-scatter watch · __REGION__ · 143.050 MHz forward-scatter</div>
  </div>
  <div class="head-controls">
    <div class="stardate" id="stardate" title="A fan-style stardate, for flavor only — the timestamps below are always real UTC">&nbsp;</div>
    <button id="sound-btn" type="button">🔊 SOUND ON</button>
  </div>
</header>
<div class="meta" id="meta"><span class="dot bad"></span>connecting…</div>

<div class="panel">
  <div id="chart-wrap">
    <div id="chart"></div>
    <div id="tooltip"></div>
  </div>
  <div class="legend-row">
    <span class="chip"><span class="swatch" style="background:var(--series-level)"></span>signal level (dB)</span>
    <span class="chip"><span class="swatch dash"></span>noise floor (dB)</span>
  </div>
</div>

<h2>Sensor Quadrants</h2>
<div class="quadrants">
  <div class="panel quad" id="quad-solar">
    <div class="quad-label">Solar Weather</div>
    <div class="quad-body">
      <div class="kp-gauge" id="kp-gauge">
        <div class="kp-seg"></div><div class="kp-seg"></div><div class="kp-seg"></div>
        <div class="kp-seg"></div><div class="kp-seg"></div><div class="kp-seg"></div>
        <div class="kp-seg"></div><div class="kp-seg"></div><div class="kp-seg"></div>
      </div>
      <div><span class="quad-num" id="kp-value">--</span><span class="quad-unit">Kp index</span></div>
      <div class="quad-desc" id="kp-condition">no data</div>
      <div class="quad-sub" id="wind-speed">solar wind: -- km/s</div>
    </div>
  </div>
  <div class="panel quad" id="quad-shower">
    <div class="quad-label">Meteor Shower Forecast</div>
    <div class="quad-body">
      <svg class="shower-burst" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r="3"/>
        <line x1="32" y1="32" x2="32" y2="10"/>
        <line x1="32" y1="32" x2="47.6" y2="16.4"/>
        <line x1="32" y1="32" x2="54" y2="32"/>
        <line x1="32" y1="32" x2="47.6" y2="47.6"/>
        <line x1="32" y1="32" x2="32" y2="54"/>
        <line x1="32" y1="32" x2="16.4" y2="47.6"/>
        <line x1="32" y1="32" x2="10" y2="32"/>
        <line x1="32" y1="32" x2="16.4" y2="16.4"/>
      </svg>
      <div class="quad-num" id="shower-name" style="font-size:18px">--</div>
      <div class="quad-desc" id="shower-detail">loading…</div>
    </div>
  </div>
  <div class="panel quad" id="quad-bearing">
    <div class="quad-label">__TARGET_NAME__ Bearing</div>
    <div class="quad-body">
      <svg class="compass" viewBox="0 0 100 100">
        <circle class="face" cx="50" cy="50" r="46"/>
        <text x="50" y="11" text-anchor="middle">N</text>
        <text x="90" y="53.5" text-anchor="middle">E</text>
        <text x="50" y="97" text-anchor="middle">S</text>
        <text x="10" y="53.5" text-anchor="middle">W</text>
        <g class="compass-needle" id="compass-needle" style="--base-rot:__BEARING_DEG__deg">
          <polygon points="50,13 45,50 50,42 55,50"/>
          <circle cx="50" cy="50" r="3.5"/>
        </g>
      </svg>
      <div><span class="quad-num" style="font-size:20px">~__BEARING_DEG__°</span><span class="quad-unit">true</span></div>
      <div class="quad-desc">__TARGET_NAME__ · ~__DISTANCE_KM__ km __COMPASS_NAME__</div>
    </div>
  </div>
  <div class="panel quad" id="quad-iss">
    <div class="quad-label">ISS Next Pass</div>
    <div class="quad-body">
      <svg class="sat-orbit" viewBox="0 0 64 64">
        <circle class="planet" cx="32" cy="32" r="8"/>
        <circle class="orbit-path" cx="32" cy="32" r="24"/>
        <g class="orbit-spin">
          <circle class="sat-dot" cx="56" cy="32" r="2.6"/>
        </g>
      </svg>
      <div class="quad-num" id="iss-countdown" style="font-size:18px">--</div>
      <div class="quad-desc" id="iss-detail">loading…</div>
      <div class="quad-sub">listen: 145.800 MHz FM (voice/SSTV) · 145.825 FM (APRS)</div>
    </div>
  </div>
  <div class="panel quad" id="quad-rate">
    <div class="quad-label">Ping Rate</div>
    <div class="quad-body">
      <div><span class="quad-num" id="rate-today" style="font-size:22px">--</span><span class="quad-unit">today</span></div>
      <div class="quad-desc" id="rate-detail">loading…</div>
      <div class="quad-sub" id="rate-last">last hour -- · last 48h --</div>
    </div>
  </div>
</div>

<h2>How to read this</h2>
<div class="panel explainer">
  <p><b>What this is:</b> a passive receiver listening for the <b>GRAVES
  military radar</b> (near Dijon, France) reflecting off meteor trails 80–110 km
  up — no transmitter, only listening. Blue = signal level, dashed aqua =
  noise floor; a brief spike above the floor is an echo off a meteor.</p>
  <p><b>Recent Events:</b> <span class="badge PING">PING</span> = a real
  meteor (under 8s). <span class="badge LONG">LONG</span> = sporadic-E,
  aircraft, or interference — not a meteor. Rates climb during showers
  (Perseids mid-Aug, Geminids mid-Dec), but a quiet sky still pings a few
  times an hour.</p>
</div>

<h2>Recent Events</h2>
<div class="panel">
<table id="events">
 <thead><tr><th>Time (UTC)</th><th>Start</th><th>Duration</th><th>Strength</th><th>Kind</th><th>Shape</th></tr></thead>
 <tbody><tr><td colspan="6" class="empty">no events yet</td></tr></tbody>
</table>
</div>

<h2>ISS Audio Log</h2>
<div class="panel">
<p class="quad-sub" style="margin:0 0 8px">Whenever a pass clears the elevation
threshold, GRAVES pauses and the dongle retunes to listen for the ISS - any
above-floor audio during that window (voice, SSTV, APRS packet bursts) is
saved here.</p>
<table id="iss-hits">
 <thead><tr><th>Time (UTC)</th><th>Duration</th><th>Strength</th><th>Freq</th><th>Listen</th></tr></thead>
 <tbody><tr><td colspan="5" class="empty">no ISS audio captured yet</td></tr></tbody>
</table>
</div>

<footer>
  Starfall Sentinel — GRAVES meteor-scatter station · build your own from
  <a href="https://github.com/ciberjohn/Starfall-Sentinel" target="_blank" rel="noopener">the public repo on GitHub</a>
  · <a href="/ping-curves">Ping curves</a> · <a href="/sporadic-e">Sporadic-E log</a> · <a href="/iss-sstv">ISS SSTV gallery</a>
</footer>
</div>
<script>
const CHART_EL = document.getElementById('chart');
const TOOLTIP = document.getElementById('tooltip');
const META = document.getElementById('meta');
const TBODY = document.querySelector('#events tbody');
const ISS_TBODY = document.querySelector('#iss-hits tbody');
const STARDATE = document.getElementById('stardate');
const SOUND_BTN = document.getElementById('sound-btn');
let plot = null;

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtUTC(ms) {
  const d = new Date(ms);
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mi = String(d.getUTCMinutes()).padStart(2, '0');
  const ss = String(d.getUTCSeconds()).padStart(2, '0');
  return `${dd} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${hh}:${mi}:${ss} UTC`;
}

// Fan-style stardate — flavor text only, purely decorative, never a real
// timestamp; all actual times on this page are explicit UTC (see fmtUTC).
function updateStardate() {
  const d = new Date();
  const startOfYear = Date.UTC(d.getUTCFullYear(), 0, 1);
  const dayOfYear = Math.floor((d.getTime() - startOfYear) / 86400000) + 1;
  const fracOfDay = (d.getUTCHours() * 3600 + d.getUTCMinutes() * 60 + d.getUTCSeconds()) / 86400;
  const yy = d.getUTCFullYear() % 100;
  STARDATE.textContent = `STARDATE ${yy}${String(dayOfYear).padStart(3, '0')}.${Math.floor(fracOfDay * 10)}`;
}

// ---- Ambient audio: synthesized in-browser, no external assets ----
// A quiet "console idle" hum (two slightly detuned low tones, slowly
// breathing) plus an occasional soft blip, and a distinct two-tone chime
// when a genuinely new meteor ping is detected. Attempts to start on load;
// if the browser's autoplay policy blocks it, the first click/keypress
// anywhere resumes it. The mute toggle is independent and persists.
const SOUND_KEY = 'starfall-sentinel-muted';
let audioCtx = null, masterGain = null, muted = false;
try { muted = localStorage.getItem(SOUND_KEY) === '1'; } catch (e) { /* storage blocked, default unmuted */ }

function blip(freq, gainAmt, dur) {
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime;
  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.type = 'sine'; o.frequency.value = freq;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(gainAmt, t0 + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  o.connect(g); g.connect(masterGain);
  o.start(t0); o.stop(t0 + dur + 0.05);
}

// A modern radar-sweep ping: a quick rising chirp, then a fainter, lower
// "return echo" a beat later - which is thematically exactly what this
// station does (sends nothing, listens for the echo a meteor sends back).
function radarSweepPing() {
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime;

  const o = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  o.type = 'sine';
  o.frequency.setValueAtTime(520, t0);
  o.frequency.exponentialRampToValueAtTime(1500, t0 + 0.1);
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(0.075, t0 + 0.015);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.45);
  o.connect(g); g.connect(masterGain);
  o.start(t0); o.stop(t0 + 0.5);

  const t1 = t0 + 0.19;
  const echo = audioCtx.createOscillator();
  const eg = audioCtx.createGain();
  echo.type = 'sine';
  echo.frequency.setValueAtTime(430, t1);
  echo.frequency.exponentialRampToValueAtTime(950, t1 + 0.09);
  eg.gain.setValueAtTime(0, t1);
  eg.gain.linearRampToValueAtTime(0.028, t1 + 0.02);
  eg.gain.exponentialRampToValueAtTime(0.0001, t1 + 0.35);
  echo.connect(eg); eg.connect(masterGain);
  echo.start(t1); echo.stop(t1 + 0.4);
}

function scheduleAmbientBlip() {
  setTimeout(() => { radarSweepPing(); scheduleAmbientBlip(); }, 4500 + Math.random() * 2500);
}

function startHum() {
  const g = audioCtx.createGain();
  g.gain.value = 0.055;
  g.connect(masterGain);
  const o1 = audioCtx.createOscillator();
  o1.type = 'sine'; o1.frequency.value = 110;
  const o2 = audioCtx.createOscillator();
  o2.type = 'sine'; o2.frequency.value = 111.5; // slow beat against o1
  o1.connect(g); o2.connect(g);
  o1.start(); o2.start();
  const lfo = audioCtx.createOscillator();       // slow "breathing" on the hum
  lfo.type = 'sine'; lfo.frequency.value = 0.08;
  const lfoGain = audioCtx.createGain();
  lfoGain.gain.value = 0.022;
  lfo.connect(lfoGain); lfoGain.connect(g.gain);
  lfo.start();
  scheduleAmbientBlip();
}

function initAudio() {
  if (audioCtx) return;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  audioCtx = new Ctx();
  masterGain = audioCtx.createGain();
  masterGain.gain.value = muted ? 0 : 1;
  masterGain.connect(audioCtx.destination);
  startHum();
  audioCtx.resume().catch(() => {});
}

function pingChime() {
  if (!audioCtx) return;
  blip(880, 0.09, 0.18);
  setTimeout(() => blip(1320, 0.08, 0.22), 110);
}

function setMuted(m) {
  muted = m;
  if (masterGain) masterGain.gain.setTargetAtTime(m ? 0 : 1, audioCtx.currentTime, 0.05);
  try { localStorage.setItem(SOUND_KEY, m ? '1' : '0'); } catch (e) { /* storage blocked, in-memory only */ }
  SOUND_BTN.textContent = m ? '🔇 SOUND OFF' : '🔊 SOUND ON';
}

initAudio();
setMuted(muted);
SOUND_BTN.addEventListener('click', () => setMuted(!muted));
// Fallback for browsers that blocked autoplay: resume on first interaction.
['pointerdown', 'keydown'].forEach(evt => window.addEventListener(evt, () => {
  if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
}, { once: true }));

let knownLatestPingUtc = null;
function flashContact() {
  const panels = document.querySelectorAll('.panel');
  if (panels[0]) {
    panels[0].classList.add('contact-flash');
    setTimeout(() => panels[0].classList.remove('contact-flash'), 800);
  }
}

const opts = {
  width: Math.min(window.innerWidth - 72, 1160),
  height: 230,
  scales: { x: { time: true }, y: { auto: true } },
  series: [
    {},
    { label: 'level dB', stroke: '#3987e5', width: 1.4 },
    { label: 'floor dB', stroke: '#199e70', width: 1, dash: [4, 4] }
  ],
  axes: [
    { stroke: '#898781', grid: { stroke: '#2c2c2a' } },
    { stroke: '#898781', grid: { stroke: '#2c2c2a' } }
  ],
  cursor: { points: { size: 6, fill: '#ffffff' } },
  legend: { show: false },
  hooks: {
    setCursor: [(u) => {
      const idx = u.cursor.idx;
      if (idx == null || u.data[1][idx] == null) { TOOLTIP.style.display = 'none'; return; }
      const t = u.data[0][idx], level = u.data[1][idx], floor = u.data[2][idx];
      TOOLTIP.style.display = 'block';
      TOOLTIP.style.left = Math.min(u.cursor.left + 14, opts.width - 170) + 'px';
      TOOLTIP.style.top = (u.cursor.top + 10) + 'px';
      TOOLTIP.innerHTML = `${fmtUTC(t * 1000)}<br>level ${level.toFixed(1)} dB · floor ${floor.toFixed(1)} dB · ` +
        `&Delta; ${(level - floor).toFixed(1)} dB`;
    }]
  }
};

async function loadLive() {
  const r = await fetch('/api/live');
  const d = await r.json();
  if (!d.samples || d.samples.length < 2) {
    META.innerHTML = '<span class="dot bad"></span>no live data — detector offline';
    return;
  }
  const times = d.samples.map(s => s[0] / 1000);
  const dbs = d.samples.map(s => s[1]);
  const floors = d.samples.map(s => s[2]);
  if (!plot) {
    plot = new uPlot(opts, [times, dbs, floors], CHART_EL);
  } else {
    plot.setData([times, dbs, floors]);
  }
  const l = d.latest;
  const age = (Date.now() - l.t_ms) / 1000;
  const dotClass = age < 30 ? 'ok' : (age < 120 ? 'warn' : 'bad');
  META.innerHTML =
    `<span class="dot ${dotClass}"></span>level ${l.db.toFixed(1)} dB · floor ${l.floor.toFixed(1)} dB · ` +
    `&Delta; ${(l.db - l.floor).toFixed(1)} dB · ${d.samples.length} samples · updated ${fmtUTC(l.t_ms)} (${age.toFixed(0)}s ago)`;
}

async function loadEvents() {
  const r = await fetch('/api/pings');
  const d = await r.json();
  if (!d.pings || d.pings.length === 0) {
    TBODY.innerHTML = '<tr><td colspan="6" class="empty">no events yet</td></tr>';
    return;
  }
  // pings[0] is the newest (pings_payload reverses the tail). Establishes a
  // silent baseline on first load instead of replaying the whole history;
  // after that, alert if ANY row newer than the last-seen one is a real
  // PING (not just the single newest row — two events can land in the same
  // 5s poll window, and checking only the latest would miss an earlier one).
  if (knownLatestPingUtc === null) {
    knownLatestPingUtc = d.pings[0].utc;
  } else {
    const idx = d.pings.findIndex(p => p.utc === knownLatestPingUtc);
    const freshRows = idx === -1 ? d.pings : d.pings.slice(0, idx);
    if (freshRows.length > 0) {
      knownLatestPingUtc = d.pings[0].utc;
      if (freshRows.some(p => p.kind === 'PING')) { pingChime(); flashContact(); }
    }
  }
  TBODY.innerHTML = d.pings.map(p =>
    `<tr><td class="num">${fmtUTC(new Date(p.utc).getTime())}</td><td class="num">${(p.start_ms / 1000).toFixed(1)} s</td>` +
    `<td class="num">${p.duration_ms} ms</td><td class="num">+${p.peak_db_over_floor} dB</td>` +
    `<td><span class="badge ${p.kind}">${p.kind}</span></td>` +
    `<td>${p.curve_file ? `<button class="curve-btn" data-file="${p.curve_file}" style="cursor:pointer;background:#1e2a3a;color:#7ab8ff;border:none;border-radius:3px;padding:1px 8px;font-size:12px">⤢</button>` : ''}</td></tr>`
  ).join('');
  document.querySelectorAll('.curve-btn').forEach(b => {
    b.onclick = async () => {
      const tr = b.closest('tr');
      const file = b.dataset.file;
      const existing = tr.nextElementSibling;
      if (existing && existing.classList.contains('curve-row')) {
        existing.remove(); b.textContent = '⤢'; return;
      }
      const data = await (await fetch('/api/curve?file=' + encodeURIComponent(file))).json();
      if (!data || !data.db || data.db.length < 2) return;
      const row2 = document.createElement('tr');
      row2.className = 'curve-row';
      tr.after(row2);
      const cell = row2.insertCell();
      cell.colSpan = 6;
      const st = data.stats || {};
      const W2 = 640, H2 = 140;
      let lo = Math.min(...data.db, ...data.floor), hi = Math.max(...data.db, ...data.floor);
      if (hi - lo < 4) { hi = lo + 4; }
      const t0 = data.t_ms[0], t1 = data.t_ms[data.t_ms.length - 1] || t0 + 1;
      const X = t => 8 + (t - t0) / (t1 - t0) * (W2 - 16);
      const Y = d => 8 + (hi - d) / (hi - lo) * (H2 - 16);
      let fp = '', sp = '';
      data.floor.forEach((d, i) => { const x = X(data.t_ms[i]), y = Y(d); fp += (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' '; });
      data.db.forEach((d, i) => { const x = X(data.t_ms[i]), y = Y(d); sp += (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' '; });
      cell.innerHTML = '<svg width="100%" viewBox="0 0 ' + W2 + ' ' + H2 + '" style="background:#0e0e0e;border-radius:4px">' +
        '<rect width="' + W2 + '" height="' + H2 + '" fill="#0e0e0e"/>' +
        '<path d="' + fp + '" stroke="#999" stroke-width="1" fill="none" stroke-dasharray="4 4" opacity="0.7"/>' +
        '<path d="' + sp + '" stroke="#3987e5" stroke-width="1.6" fill="none"/></svg>' +
        '<div class="num" style="margin-top:4px">rise ' + (st.rise_ms != null ? st.rise_ms + ' ms' : '—') +
        ' · peak +' + (st.peak_over_floor != null ? st.peak_over_floor : '—') + ' dB' +
        (st.decay_s != null ? ' · decay τ=' + st.decay_s + ' s' : '') +
        (st.oscillations ? ' · osc ' + st.oscillations : '') +
        ' · <b>' + (st.size_class || '—') + '</b></div>';
      b.textContent = '⤓';
    };
  });
}

async function loadIssHits() {
  let d;
  try {
    d = await (await fetch('/api/iss-hits')).json();
  } catch (e) {
    return; // transient network hiccup - keep showing the last good state
  }
  if (!d.hits || d.hits.length === 0) {
    ISS_TBODY.innerHTML = '<tr><td colspan="5" class="empty">no ISS audio captured yet</td></tr>';
    return;
  }
  ISS_TBODY.innerHTML = d.hits.map(h =>
    `<tr><td class="num">${fmtUTC(new Date(h.utc).getTime())}</td>` +
    `<td class="num">${(h.duration_ms / 1000).toFixed(1)} s</td>` +
    `<td class="num">+${h.peak_db_over_floor} dB</td>` +
    `<td class="num">${h.frequency}</td>` +
    `<td><audio controls preload="none" src="${h.clip_url}"></audio></td></tr>`
  ).join('');
}

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (!plot) return;
    opts.width = Math.min(window.innerWidth - 72, 1160);
    plot.setSize({ width: opts.width, height: opts.height });
  }, 150);
});

const KP_GAUGE = document.querySelectorAll('#kp-gauge .kp-seg');
const KP_VALUE = document.getElementById('kp-value');
const KP_CONDITION = document.getElementById('kp-condition');
const WIND_SPEED = document.getElementById('wind-speed');
const QUAD_SOLAR = document.getElementById('quad-solar');
const SHOWER_NAME = document.getElementById('shower-name');
const SHOWER_DETAIL = document.getElementById('shower-detail');
const ISS_COUNTDOWN = document.getElementById('iss-countdown');
const ISS_DETAIL = document.getElementById('iss-detail');
const RATE_TODAY = document.getElementById('rate-today');
const RATE_DETAIL = document.getElementById('rate-detail');
const RATE_LAST = document.getElementById('rate-last');

async function loadQuadrants() {
  let d;
  try {
    d = await (await fetch('/api/quadrants')).json();
  } catch (e) {
    return; // transient network hiccup - keep showing the last good state
  }

  const sw = d.space_weather;
  QUAD_SOLAR.className = 'panel quad' + (sw.severity ? ` severity-${sw.severity}` : '');
  if (sw.available) {
    KP_VALUE.textContent = sw.kp.toFixed(1);
    KP_CONDITION.textContent = sw.kp_condition;
    WIND_SPEED.textContent = `solar wind: ${Math.round(sw.wind_speed_km_s)} km/s`;
    // All filled segments share ONE color, taken directly from the same
    // server-computed `severity` that drives the condition label and the
    // panel's pulse animation - a single source of truth, so the gauge can
    // never disagree with the labeled condition (it used to, at fractional
    // Kp values near a threshold, when each segment had its own color band).
    const filled = Math.min(9, Math.max(0, Math.round(sw.kp)));
    const fillColor = `var(--${sw.severity})`;
    KP_GAUGE.forEach((seg, i) => {
      seg.style.background = i < filled ? fillColor : 'var(--grid)';
    });
  } else {
    KP_VALUE.textContent = '--';
    KP_CONDITION.textContent = 'no data (offline?)';
    WIND_SPEED.textContent = 'solar wind: -- km/s';
    KP_GAUGE.forEach(seg => { seg.style.background = 'var(--grid)'; });
  }

  const sh = d.meteor_shower;
  if (sh) {
    SHOWER_NAME.textContent = sh.name;
    const badge = sh.active ? '<span class="quad-badge">ACTIVE NOW</span> ' : '';
    const when = sh.active ? 'peaking now' : `in ${Math.ceil(sh.days_until)} day${Math.ceil(sh.days_until) === 1 ? '' : 's'}`;
    SHOWER_DETAIL.innerHTML = `${badge}radiant ${sh.radiant} · ZHR ~${sh.zhr} · ${when}`;
  }

  const iss = d.iss_pass;
  if (iss && iss.available) {
    const mins = iss.minutes_until;
    const overhead = mins <= 0;
    const badge = overhead ? '<span class="quad-badge">OVERHEAD NOW</span> ' : '';
    ISS_COUNTDOWN.textContent = overhead ? 'OVERHEAD'
      : mins < 60 ? `in ${Math.round(mins)}m` : `in ${(mins / 60).toFixed(1)}h`;
    const durMin = Math.floor(iss.duration_s / 60), durSec = iss.duration_s % 60;
    ISS_DETAIL.innerHTML = `${badge}rise ${iss.aos_az_compass} → set ${iss.los_az_compass} `
      + `· max el ${Math.round(iss.max_el_deg)}° · ${durMin}m${String(durSec).padStart(2, '0')}s`;
  } else if (iss) {
    ISS_COUNTDOWN.textContent = '--';
    ISS_DETAIL.textContent = iss.no_pass_in_window ? 'no pass in the next 72h' : 'no data (offline?)';
  }
}

async function loadRate() {
  let d;
  try { d = await (await fetch('/api/rate')).json(); } catch (e) { return; }
  RATE_TODAY.textContent = d.today;
  RATE_DETAIL.textContent = d.today > 0 ? 'meteor echoes detected today' : 'no echoes yet today';
  RATE_LAST.textContent = `last hour ${d.last_hour} · last 48h ${d.last_48h}`;
}

setInterval(loadLive, 1000);
setInterval(loadEvents, 5000);
setInterval(updateStardate, 1000);
setInterval(loadQuadrants, 60000);
setInterval(loadIssHits, 30000);
setInterval(loadRate, 60000);
loadLive();
loadEvents();
updateStardate();
loadQuadrants();
loadIssHits();
loadRate();
</script>
</body>
</html>
"""

PING_CURVES_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ping Curves — Starfall Sentinel</title>
<style>
 body{background:#0d0d0d;color:#c3c2b7;font-family:system-ui,sans-serif;margin:0;padding:18px}
 h1{font-size:20px;letter-spacing:3px;color:#fff;margin:0 0 4px}
 .sub{color:#898781;font-size:12px;margin-bottom:12px}
 a{color:#3987e5}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
 .card{background:#1a1a19;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:10px}
 .card svg{width:100%;height:120px;display:block;background:#0e0e0e;border-radius:4px}
 .head{font-size:12px;color:#898781;margin-bottom:6px;font-family:ui-monospace,Menlo,monospace}
 .stats{font-size:11.5px;color:#c3c2b7;margin-top:6px;line-height:1.5}
 .class-tag{display:inline-block;background:#1e2a3a;color:#7ab8ff;border-radius:3px;padding:1px 6px;font-size:10.5px;margin-left:6px}
 .empty{color:#898781;font-size:13px}
</style>
</head>
<body>
<h1>PING CURVES</h1>
<div class="sub">Echo profiles (dB vs time) for recent detections. Sharp rise + short exponential decay = underdense (small/faint); long or irregular with fading = overdense (large/bright). <a href="/">← back to the dashboard</a></div>
<div class="sub"><label>Month: <select id="month"><option value="">live (6 months)</option></select></label></div>
<div class="grid" id="grid"><div class="empty">no ping curves yet — they appear after the first detections</div></div>
<script>
const W=600,H=120;
let curMonth='';
async function load(){
  const url='/api/curves'+(curMonth?'?archive='+curMonth:'');
  const d=await (await fetch(url)).json();
  const g=document.getElementById('grid');
  if(!d.curves.length){g.innerHTML='<div class="empty">'+(curMonth?'no curves archived for '+curMonth:'no ping curves yet')+'</div>';return;}
  g.innerHTML=d.curves.map(c=>{
    const st=c.stats||{};
    const cls=c.kind||'';
    return '<div class="card"><div class="head">'+(c.utc||'—')+' · '+cls+' · '+(c.duration_ms||'—')+' ms · +'+(c.peak_db_over_floor||'—')+' dB</div>'+
      '<svg id="svg-'+c.file.replace(/[^a-z0-9]/gi,'')+'"></svg>'+
      '<div class="stats">rise '+(st.rise_ms!=null?st.rise_ms+' ms':'—')+' · peak +'+(st.peak_over_floor!=null?st.peak_over_floor:'—')+' dB · half-width '+(st.duration_ms!=null?st.duration_ms:'—')+' ms'+
      (st.decay_s!=null?' · decay τ='+st.decay_s+' s':'')+(st.oscillations?' · osc '+st.oscillations:'')+
      '<span class="class-tag">'+(st.size_class||'—')+'</span></div></div>';
  }).join('');
  d.curves.forEach(c=>{ if(c.file) draw(c); });
}
async function draw(c){
  const f=c.file.replace(/[^a-z0-9]/gi,'');
  const svg=document.getElementById('svg-'+f);
  if(!svg) return;
  const url='/api/curve?file='+encodeURIComponent(c.file)+(c.archive?'&archive='+c.archive:'');
  const data=await (await fetch(url)).json();
  if(!data||!data.db||data.db.length<2) return;
  let lo=Math.min(...data.db,...data.floor), hi=Math.max(...data.db,...data.floor);
  if(hi-lo<4){hi=lo+4;}
  const t0=data.t_ms[0], t1=data.t_ms[data.t_ms.length-1]||t0+1;
  const X=t=>8+(t-t0)/(t1-t0)*(W-16), Y=d=>8+(hi-d)/(hi-lo)*(H-16);
  let floorPath='', sigPath='';
  data.floor.forEach((d,i)=>{const x=X(data.t_ms[i]),y=Y(d); floorPath+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' ';});
  data.db.forEach((d,i)=>{const x=X(data.t_ms[i]),y=Y(d); sigPath+=(i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)+' ';});
  svg.setAttribute('viewBox','0 0 '+W+' '+H);
  svg.innerHTML='<rect width="'+W+'" height="'+H+'" fill="#0e0e0e"/>'+
    '<path d="'+floorPath+'" stroke="#999" stroke-width="1" fill="none" stroke-dasharray="4 4" opacity="0.7"/>'+
    '<path d="'+sigPath+'" stroke="#3987e5" stroke-width="1.6" fill="none"/>';
}
setInterval(load,60000);load();
(async function initMonths(){
  try{
    const a=await (await fetch('/api/archives')).json();
    const sel=document.getElementById('month');
    a.archives.forEach(m=>{
      const o=document.createElement('option');
      o.value=m.month; o.textContent=m.month+' ('+m.count+' echoes)';
      sel.appendChild(o);
    });
    sel.onchange=()=>{curMonth=sel.value; load();};
  }catch(e){}
})();
</script>
</body>
</html>
"""

SPORADIC_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Propagation Log — Starfall Sentinel</title>
<link rel="stylesheet" href="/static/uPlot.min.css">
<script src="/static/uPlot.iife.min.js"></script>
<style>
 body{background:#0d0d0d;color:#c3c2b7;font-family:system-ui,sans-serif;margin:0;padding:18px}
 h1{font-size:20px;letter-spacing:3px;color:#fff;margin:0 0 4px}
 .sub{color:#898781;font-size:12px;margin-bottom:12px}
 a{color:#3987e5}
 .panel{background:#1a1a19;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:12px;margin-bottom:12px}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #2c2c2a}
 th{color:#898781;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
 .num{font-family:ui-monospace,Menlo,monospace}
 .empty{color:#898781}
</style>
</head>
<body>
<h1>PROPAGATION LOG</h1>
<div class="sub">Ping rate (PING echoes per UTC hour, last 48h) and LONG events — sporadic-E, aircraft, or interference. <a href="/">← back to the dashboard</a></div>
<div class="panel"><div id="chart"></div></div>
<div class="panel"><table id="events"><thead><tr><th>UTC</th><th>Duration</th><th>Peak +dB</th><th>Level</th><th>Floor</th></tr></thead><tbody><tr><td colspan="5" class="empty">loading…</td></tr></tbody></table></div>
<script>
const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmt(ms){const d=new Date(ms);return String(d.getUTCDate()).padStart(2,'0')+' '+MONTHS[d.getUTCMonth()]+' '+d.getUTCFullYear()+' '+String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0')+':'+String(d.getUTCSeconds()).padStart(2,'0')+' UTC';}
let plot=null;
async function loadRate(){
  const d=await (await fetch('/api/rate')).json();
  const times=d.series.map(s=>s[0]/1000);
  const counts=d.series.map(s=>s.count);
  if(!plot){plot=new uPlot({width:Math.min(window.innerWidth-60,1160),height:220,scales:{x:{time:true},y:{auto:true}},series:[{}, {label:'echoes/hour',stroke:'#3987e5',width:2,fill:'rgba(57,135,229,.15)'}],axes:[{stroke:'#898781',grid:{stroke:'#2c2c2a'}},{stroke:'#898781',grid:{stroke:'#2c2c2a'}}]},[times,counts],document.getElementById('chart'));}
  else{plot.setData([times,counts]);}
}
async function loadEvents(){
  const d=await (await fetch('/api/sporadic-e')).json();
  const tb=document.querySelector('#events tbody');
  if(!d.events.length){tb.innerHTML='<tr><td colspan="5" class="empty">no LONG events logged</td></tr>';return;}
  tb.innerHTML=d.events.map(e=>'<tr><td class="num">'+fmt(new Date(e.utc).getTime())+'</td><td class="num">'+e.duration_ms+' ms</td><td class="num">+'+e.peak_db_over_floor+' dB</td><td class="num">'+e.peak_level_db+' dB</td><td class="num">'+e.floor_db+' dB</td></tr>').join('');
}
setInterval(loadRate,60000);setInterval(loadEvents,30000);loadRate();loadEvents();
</script>
</body>
</html>
"""

SSTV_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ISS SSTV Gallery — Starfall Sentinel</title>
<style>
 body{background:#0d0d0d;color:#c3c2b7;font-family:system-ui,sans-serif;margin:0;padding:18px}
 h1{font-size:20px;letter-spacing:3px;color:#fff;margin:0 0 4px}
 .sub{color:#898781;font-size:12px;margin-bottom:12px}
 a{color:#3987e5}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
 .card{background:#1a1a19;border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:10px}
 .card img{width:100%;border-radius:4px;display:block}
 .cap{font-size:11.5px;color:#898781;margin-top:6px;font-family:ui-monospace,Menlo,monospace}
 .empty{color:#898781;font-size:13px}
</style>
</head>
<body>
<h1>ISS SSTV GALLERY</h1>
<div class="sub">Images decoded from ISS audio clips (Robot 36 / Martin / Scottie SSTV on 145.800 MHz). <a href="/">← back to the dashboard</a></div>
<div class="grid" id="grid"><div class="empty">no decoded images yet — they appear here after each ISS pass</div></div>
<script>
async function load(){
  const d=await (await fetch('/api/sstv')).json();
  const g=document.getElementById('grid');
  if(!d.images.length){g.innerHTML='<div class="empty">no decoded images yet</div>';return;}
  g.innerHTML=d.images.map(i=>'<div class="card"><img src="'+i.image_url+'" alt="SSTV '+i.utc+'"><div class="cap">'+i.utc+' · '+i.mode+'</div></div>').join('');
}
setInterval(load,60000);load();
</script>
</body>
</html>
"""

RENDERED_PAGE = PAGE  # replaced by render_page() in main(), once config is loaded


def render_page():
    """Substitute the station-specific placeholders into PAGE. Called once
    at startup (after apply_station_config), not per-request - the station's
    location and target don't change while the process is running."""
    page = PAGE
    page = page.replace("__REGION__", STATION["region"])
    page = page.replace("__TARGET_NAME__", STATION["target_name"])
    page = page.replace("__BEARING_DEG__", str(int(BEARING_DEG)))
    page = page.replace("__DISTANCE_KM__", str(int(DISTANCE_KM)))
    page = page.replace("__COMPASS_NAME__", COMPASS_POINT)
    return page.encode()


def tail_lines(path, n):
    """Return the last n complete lines of a file (efficient, no full read)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            chunk = min(size, TAIL_CHUNK)
            f.seek(size - chunk)
            data = f.read().decode(errors="replace")
    except FileNotFoundError:
        return []
    nl = data.find("\n")
    if nl != -1:
        data = data[nl + 1:]  # start at a complete line
    return [ln for ln in data.splitlines() if ln.strip()]


def live_payload(data_dir):
    rows = []
    for ln in tail_lines(os.path.join(data_dir, "live.csv"), LIVE_N):
        p = ln.split(",")
        if len(p) >= 3:
            try:
                rows.append([int(p[0]), float(p[1]), float(p[2])])
            except ValueError:
                continue
    latest = rows[-1] if rows else None
    return {
        "samples": rows,
        "latest": ({"t_ms": latest[0], "db": latest[1], "floor": latest[2]}
                   if latest else None),
    }


def pings_payload(data_dir):
    pings = []
    path = os.path.join(data_dir, "pings.csv")
    for ln in reversed(tail_lines(path, 2000)):
        row = next(csv.reader([ln]))
        if len(row) >= 8 and row[0].startswith("20"):
            pings.append({
                "utc": row[0], "local": row[1], "start_ms": row[2],
                "duration_ms": row[3], "peak_db_over_floor": row[4],
                "peak_level_db": row[5], "floor_db": row[6],
                "kind": row[7], "note": row[8] if len(row) > 8 else "",
                "curve_file": row[9] if len(row) > 9 else "",
            })
        if len(pings) >= PINGS_N:
            break
    return {"pings": pings}


def rate_payload(data_dir):
    """Ping-rate monitor: hourly PING counts for the last 48h + totals
    (UTC hours), for the rate quadrant and the propagation log page."""
    now = datetime.datetime.now(datetime.timezone.utc)
    hours = {}
    path = os.path.join(data_dir, "pings.csv")
    for ln in tail_lines(path, 200000):
        row = next(csv.reader([ln]))
        if len(row) >= 8 and row[0].startswith("20") and row[7] == "PING":
            try:
                dt = datetime.datetime.strptime(
                    row[0][:19], "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                continue
            if (now - dt).total_seconds() <= 48 * 3600:
                key = dt.replace(minute=0, second=0, microsecond=0)
                hours[key] = hours.get(key, 0) + 1
    series = [{"t_ms": int(k.timestamp() * 1000), "count": v}
              for k, v in sorted(hours.items())]
    today = now.strftime("%Y-%m-%d")
    today_count = sum(v for k, v in hours.items()
                      if k.strftime("%Y-%m-%d") == today)
    last_hour = sum(v for k, v in hours.items()
                    if (now - k).total_seconds() <= 3600)
    return {
        "series": series,
        "today": today_count,
        "last_hour": last_hour,
        "last_48h": sum(hours.values()),
        "utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def sporadic_e_payload(data_dir, limit=100):
    """LONG events - the sporadic-E / aircraft / interference catalog."""
    events = []
    path = os.path.join(data_dir, "pings.csv")
    for ln in reversed(tail_lines(path, 50000)):
        row = next(csv.reader([ln]))
        if len(row) >= 8 and row[0].startswith("20") and row[7] == "LONG":
            events.append({
                "utc": row[0], "local": row[1], "duration_ms": row[3],
                "peak_db_over_floor": row[4], "peak_level_db": row[5],
                "floor_db": row[6],
            })
        if len(events) >= limit:
            break
    return {"events": events}


def sstv_payload(data_dir, limit=24):
    """Decoded ISS SSTV images (newest first)."""
    imgs = []
    path = os.path.join(data_dir, "iss_sstv.csv")
    for ln in reversed(tail_lines(path, 500)):
        row = next(csv.reader([ln]))
        if len(row) >= 4 and row[0].startswith("20"):
            imgs.append({
                "utc": row[0], "clip": row[1], "mode": row[2],
                "image": row[3], "image_url": f"/sstv-images/{row[3]}",
            })
        if len(imgs) >= limit:
            break
    return {"images": imgs}


def _parse_curve_rows(f):
    """Parse curve CSV rows (t_ms,db,floor) from an open file/iterable."""
    ts, dbs, floors = [], [], []
    for row in csv.reader(f):
        if len(row) < 3 or not row[0].lstrip("-").isdigit():
            continue
        ts.append(float(row[0]))
        dbs.append(float(row[1]))
        floors.append(float(row[2]))
    return ts, dbs, floors


def _curve_stats(ts, dbs, floors):
    """Shape statistics from parsed curve arrays.

    The profile shape is how radio observers estimate meteor size: a sharp
    rise + short exponential decay = small underdense trail; long/irregular
    echoes with 5-10 Hz fading = large overdense trail (IMO theory)."""
    if len(dbs) < 3:
        return None
    t0 = ts[0]
    floor = sum(floors) / len(floors)
    peak = max(dbs)
    peak_over = peak - floor
    half = floor + (peak - floor) * 0.5
    idxs = [i for i, d in enumerate(dbs) if d >= half]
    dur_ms = (ts[idxs[-1]] - ts[idxs[0]]) if idxs else 0.0
    rise_ms = 0.0
    p10, p90 = floor + (peak - floor) * 0.1, floor + (peak - floor) * 0.9
    i10 = next((i for i, d in enumerate(dbs) if d >= p10), None)
    i90 = next((i for i, d in enumerate(dbs) if d >= p90), None)
    if i10 is not None and i90 is not None and i90 >= i10:
        rise_ms = ts[i90] - ts[i10]
    # decay time constant: log-linear fit on the descending tail
    decay_s = None
    ip = max(range(len(dbs)), key=lambda i: dbs[i])
    xs, ys = [], []
    for i in range(ip + 1, len(dbs)):
        v = dbs[i] - floor
        if v > 1.0:
            xs.append((ts[i] - t0) / 1000.0)
            ys.append(math.log(v))
    if len(xs) >= 3:
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if denom != 0:
            slope = (n * sxy - sx * sy) / denom
            if slope < -0.01:
                decay_s = round(-1.0 / slope, 2)
    # oscillation: sign changes of (db - 3-window smoothed), swings > 0.6 dB
    sm = dbs[:]
    for i in range(1, len(dbs) - 1):
        sm[i] = (dbs[i - 1] + dbs[i] + dbs[i + 1]) / 3.0
    osc, prev_sign = 0, 0
    for i in range(1, len(dbs)):
        d = dbs[i] - sm[i]
        if abs(d) < 0.6:
            continue
        s = 1 if d > 0 else -1
        if prev_sign and s != prev_sign:
            osc += 1
        prev_sign = s
    if dur_ms < 1200 and osc < 3:
        size_class = "underdense (small/faint)"
    else:
        size_class = "overdense (large/bright)" + (" / wind-fading" if osc >= 5 else "")
    return {
        "rise_ms": round(rise_ms, 0), "peak_db": round(peak, 1),
        "peak_over_floor": round(peak_over, 1), "duration_ms": round(dur_ms, 0),
        "decay_s": decay_s, "oscillations": osc, "size_class": size_class,
    }


def curve_stats(path):
    """Parse a ping curve CSV (t_ms,db,floor) into shape statistics."""
    try:
        ts, dbs, floors = _parse_curve_rows(open(path))
    except OSError:
        return None
    return _curve_stats(ts, dbs, floors)


def _tar_member_data(archive_path, member_name):
    """Parse one curve CSV out of a monthly archive tarball."""
    with tarfile.open(archive_path, "r:gz") as tf:
        f = tf.extractfile(member_name)
        if f is None:
            return None
        return _parse_curve_rows(io.StringIO(f.read().decode(errors="replace")))


def curve_payload(data_dir, limit=30, archive=None):
    """Recent ping-curve files with stats (for /api/curves + /ping-curves).

    With archive='YYYY-MM', reads the monthly tarball instead of the live
    dir (one sequential pass, keeping the newest `limit` members)."""
    items = []
    cdir = os.path.join(data_dir, "ping_curves")
    adir = os.path.join(data_dir, "ping_curves_archive")
    if archive:
        apath = os.path.join(adir, f"ping_curves_{archive}.tar.gz")
        if not os.path.isfile(apath):
            return {"curves": []}
        try:
            with tarfile.open(apath, "r:gz") as tf:
                keep = []
                for m in tf.getmembers():
                    if not m.name.endswith(".csv"):
                        continue
                    f = tf.extractfile(m)
                    if f is None:
                        continue
                    parsed = _parse_curve_rows(
                        io.StringIO(f.read().decode(errors="replace")))
                    keep.append((m.name, parsed))
                for name, parsed in keep[-limit:]:
                    ts, dbs, floors = parsed
                    st = _curve_stats(ts, dbs, floors) if len(dbs) >= 3 else None
                    items.append({"file": name, "archive": archive,
                                  "utc": "", "kind": "", "duration_ms": "",
                                  "peak_db_over_floor": "", "size": 0,
                                  "stats": st})
        except tarfile.TarError:
            return {"curves": []}
    elif os.path.isdir(cdir):
        for name in sorted(os.listdir(cdir), reverse=True)[:limit]:
            if not name.endswith(".csv"):
                continue
            p = os.path.join(cdir, name)
            try:
                st = curve_stats(p)
            except Exception:
                st = None
            items.append({"file": name, "archive": "", "utc": "", "kind": "",
                          "duration_ms": "", "peak_db_over_floor": "",
                          "size": os.path.getsize(p), "stats": st})
    else:
        return {"curves": items}
    # match utc/kind from pings.csv so cards can show the event time
    path = os.path.join(data_dir, "pings.csv")
    for ln in tail_lines(path, 50000):
        row = next(csv.reader([ln]))
        if len(row) > 9 and row[9]:
            for it in items:
                if it["file"] == row[9]:
                    it["utc"] = row[0]
                    it["kind"] = row[7]
                    it["duration_ms"] = row[3]
                    it["peak_db_over_floor"] = row[4]
    return {"curves": items}


def curve_data_payload(data_dir, fname, archive=None):
    """Full curve data for one file. Resolves from the live dir, or - if it
    has been archived - from the monthly tarball (month auto-derived from
    the filename unless `archive` is given). Traversal-safe: caller
    basenames."""
    cdir = os.path.join(data_dir, "ping_curves")
    adir = os.path.join(data_dir, "ping_curves_archive")
    path = os.path.join(cdir, fname)
    if os.path.isfile(path):
        ts, dbs, floors = _parse_curve_rows(open(path))
        resolved_archive = ""
    else:
        month = archive or (fname[:4] + "-" + fname[4:6] if len(fname) >= 6 else None)
        if not month:
            return None
        apath = os.path.join(adir, f"ping_curves_{month}.tar.gz")
        if not os.path.isfile(apath):
            return None
        try:
            parsed = _tar_member_data(apath, fname)
        except (tarfile.TarError, KeyError):
            return None
        if parsed is None:
            return None
        ts, dbs, floors = parsed
        resolved_archive = month
    return {"file": fname, "archive": resolved_archive,
            "t_ms": ts, "db": dbs, "floor": floors,
            "stats": _curve_stats(ts, dbs, floors)}


def archive_payload(data_dir):
    """Monthly archive tarballs (for the /ping-curves month selector)."""
    months = []
    adir = os.path.join(data_dir, "ping_curves_archive")
    if os.path.isdir(adir):
        for name in sorted(os.listdir(adir), reverse=True):
            if not name.endswith(".tar.gz"):
                continue
            month = name[len("ping_curves_"):-len(".tar.gz")]
            size = os.path.getsize(os.path.join(adir, name))
            count = 0
            try:
                with tarfile.open(os.path.join(adir, name), "r:gz") as tf:
                    count = sum(1 for m in tf.getmembers()
                                if m.name.endswith(".csv"))
            except tarfile.TarError:
                pass
            months.append({"month": month, "size": size, "count": count})
    return {"archives": months}


def iss_hits_payload(data_dir):
    hits = []
    path = os.path.join(data_dir, "iss_hits.csv")
    for ln in reversed(tail_lines(path, 1000)):
        row = next(csv.reader([ln]))
        if len(row) >= 9 and row[0].startswith("20"):
            hits.append({
                "utc": row[0], "local": row[1], "pass_aos_utc": row[2],
                "duration_ms": row[3], "peak_db_over_floor": row[4],
                "peak_level_db": row[5], "floor_db": row[6],
                "frequency": row[7], "clip_url": f"/iss-clips/{row[8]}",
            })
        if len(hits) >= 20:
            break
    return {"hits": hits}


def health_payload(data_dir):
    live_path = os.path.join(data_dir, "live.csv")
    age = None
    alive = False
    try:
        mtime = os.path.getmtime(live_path)
        age = time.time() - mtime
        alive = age < 30
    except FileNotFoundError:
        pass
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    pings_today = 0
    for ln in tail_lines(os.path.join(data_dir, "pings.csv"), 5000):
        if ln.startswith(today):
            pings_today += 1
    return {
        "detector_alive": alive,
        "live_age_s": round(age, 1) if age is not None else None,
        "pings_today": pings_today,
        "utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "GravesDash/1.0"

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        self._send(200, json.dumps(obj).encode(), "application/json")

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, RENDERED_PAGE, "text/html; charset=utf-8")
        elif route == "/api/live":
            self._send_json(live_payload(self.server.data_dir))
        elif route == "/api/pings":
            self._send_json(pings_payload(self.server.data_dir))
        elif route == "/api/iss-hits":
            self._send_json(iss_hits_payload(self.server.data_dir))
        elif route.startswith("/iss-clips/"):
            # basename() strips any path components (traversal-proof); the
            # prefix/suffix check confines this to files iss_recorder.py
            # actually writes, not an arbitrary-file-read primitive
            fname = os.path.basename(route[len("/iss-clips/"):])
            if fname.startswith("iss_") and fname.endswith(".wav"):
                self._send_file(os.path.join(self.server.data_dir, "iss_clips", fname), "audio/wav")
            else:
                self.send_error(404)
        elif route == "/api/quadrants":
            self._send_json(quadrants_payload())
        elif route == "/api/rate":
            self._send_json(rate_payload(self.server.data_dir))
        elif route == "/api/curves":
            qs = parse_qs(urlparse(self.path).query)
            archive = qs.get("archive", [None])[0]
            if archive is not None and not (
                    len(archive) == 7 and archive[4] == "-"
                    and archive[:4].isdigit() and archive[5:].isdigit()):
                self.send_error(404)
            else:
                self._send_json(curve_payload(self.server.data_dir,
                                              archive=archive))
        elif route == "/api/archives":
            self._send_json(archive_payload(self.server.data_dir))
        elif route == "/api/curve":
            qs = parse_qs(urlparse(self.path).query)
            fname = os.path.basename(qs.get("file", [""])[0])
            archive = qs.get("archive", [None])[0]
            if archive is not None and not (
                    len(archive) == 7 and archive[4] == "-"
                    and archive[:4].isdigit() and archive[5:].isdigit()):
                self.send_error(404)
            elif fname.endswith(".csv") and ".." not in fname:
                payload = curve_data_payload(self.server.data_dir, fname,
                                             archive=archive)
                if payload:
                    self._send_json(payload)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)
        elif route == "/ping-curves":
            self._send(200, PING_CURVES_PAGE.encode(), "text/html; charset=utf-8")
        elif route == "/api/sporadic-e":
            self._send_json(sporadic_e_payload(self.server.data_dir))
        elif route in ("/sporadic-e", "/sporadic"):
            self._send(200, SPORADIC_PAGE.encode(), "text/html; charset=utf-8")
        elif route == "/api/sstv":
            self._send_json(sstv_payload(self.server.data_dir))
        elif route == "/iss-sstv":
            self._send(200, SSTV_PAGE.encode(), "text/html; charset=utf-8")
        elif route.startswith("/sstv-images/"):
            fname = os.path.basename(route[len("/sstv-images/"):])
            if fname.endswith(".png"):
                self._send_file(os.path.join(self.server.data_dir, "iss_sstv", fname),
                                "image/png")
            else:
                self.send_error(404)
        elif route == "/api/health":
            self._send_json(health_payload(self.server.data_dir))
        elif route == "/status":
            h = health_payload(self.server.data_dir)
            text = (f"{'OK' if h['detector_alive'] else 'OFFLINE'} | "
                    f"live_age_s {h['live_age_s']} | pings_today {h['pings_today']}")
            self._send(200, text.encode(), "text/plain")
        elif route == "/static/uPlot.iife.min.js":
            self._send_file(os.path.join(STATIC, "uPlot.iife.min.js"),
                            "application/javascript")
        elif route == "/static/uPlot.min.css":
            self._send_file(os.path.join(STATIC, "uPlot.min.css"),
                            "text/css")
        elif route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def main():
    global RENDERED_PAGE, DATA_DIR
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"),
                    help="INI file with a [station] section (lat/lon/region/target)")
    args = ap.parse_args()

    apply_station_config(load_station_config(args.config))
    RENDERED_PAGE = render_page()
    DATA_DIR = args.data_dir

    os.makedirs(args.data_dir, exist_ok=True)
    threading.Thread(target=space_weather_loop, daemon=True).start()
    threading.Thread(target=iss_loop, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.data_dir = args.data_dir
    print(f"[graves-dashboard] serving on http://{args.host}:{args.port} "
          f"(data: {args.data_dir})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[graves-dashboard] stopped", flush=True)


if __name__ == "__main__":
    main()
