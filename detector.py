#!/usr/bin/env python3
"""GRAVES meteor-scatter ping detector for RTL-SDR.

Streams 16-bit mono PCM from rtl_fm (or stdin) and logs short-duration signal
bursts ("pings") characteristic of meteor forward-scatter reflections from the
GRAVES radar at 143.050 MHz.

Modes:
  --source rtl      (default) spawn rtl_fm and read its audio output
  --source stdin    read raw 16-bit LE mono PCM from stdin (test/pipe mode)
  --calibrate       live level monitor once per second, no logging

Examples:
  python3 detector.py --calibrate
  python3 detector.py --log data/pings.csv
  python3 detector.py --webhook https://discord.com/api/webhooks/... 
  python3 simulate.py --test
"""

import argparse
import array
import configparser
import csv
import datetime
import json
import math
import os
import subprocess
import sys
import time
from collections import deque

DEFAULT_FREQ = "143.050M"
DEFAULT_RATE = 48000


def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # default=argparse.SUPPRESS so INI provides defaults and explicit CLI wins
    p.add_argument("--config", default=None, help="INI file with [detector] section")
    p.add_argument("--frequency", default=argparse.SUPPRESS, help="RX frequency, rtl_fm syntax (143.050M)")
    p.add_argument("--sample-rate", type=int, default=argparse.SUPPRESS, help="audio sample rate Hz")
    p.add_argument("--gain", type=int, default=argparse.SUPPRESS, help="SDR gain dB (0=auto)")
    p.add_argument("--ppm", type=int, default=argparse.SUPPRESS, help="SDR frequency correction ppm")
    p.add_argument("--threshold-db", type=float, default=argparse.SUPPRESS,
                   help="ping threshold above noise floor (dB)")
    p.add_argument("--min-ms", type=float, default=argparse.SUPPRESS, help="min ping duration (ms)")
    p.add_argument("--max-ms", type=float, default=argparse.SUPPRESS,
                   help="events longer than this are flagged LONG (sporadic-E / interference)")
    p.add_argument("--noise-history-s", type=float, default=argparse.SUPPRESS,
                   help="noise floor averaging window (s)")
    p.add_argument("--floor-percentile", type=float, default=argparse.SUPPRESS,
                   help="low percentile of recent power used as noise floor (default 10)")
    p.add_argument("--window-ms", type=float, default=argparse.SUPPRESS, help="power window (ms)")
    p.add_argument("--source", choices=["rtl", "stdin"], default=argparse.SUPPRESS)
    p.add_argument("--log", default=argparse.SUPPRESS, help="CSV log path")
    p.add_argument("--live-out", default=argparse.SUPPRESS,
                   help="append 1 Hz live samples CSV: epoch_ms,db,floor")
    p.add_argument("--webhook", default=argparse.SUPPRESS, help="Discord webhook URL for alerts")
    p.add_argument("--test-webhook", action="store_true", default=argparse.SUPPRESS,
                   help="send a test message to the configured webhook and exit")
    p.add_argument("--webhook-long", action="store_true", default=argparse.SUPPRESS,
                   help="also alert on LONG events")
    p.add_argument("--name", default=argparse.SUPPRESS, help="station name")
    p.add_argument("--calibrate", action="store_true", default=argparse.SUPPRESS,
                   help="live level monitor, no logging")
    return p


def load_config(argv):
    """Merge: built-in defaults < INI < explicit CLI flags."""
    cfg = {
        "frequency": DEFAULT_FREQ,
        "sample_rate": DEFAULT_RATE,
        "gain": 40,
        "ppm": 0,
        "threshold_db": 10.0,
        "min_ms": 80.0,
        "max_ms": 8000.0,
        "noise_history_s": 25.0,
        "floor_percentile": 10.0,
        "window_ms": 50.0,
        "source": "rtl",
        "log": None,
        "live_out": None,
        "webhook": None,
        "test_webhook": False,
        "webhook_long": False,
        "name": "my-station-1",
        "calibrate": False,
    }
    args = vars(build_parser().parse_args(argv))

    ini_path = args.get("config")
    if ini_path and os.path.exists(ini_path):
        cp = configparser.ConfigParser()
        cp.read(ini_path)
        if cp.has_section("detector"):
            for k, v in cp["detector"].items():
                if k in cfg:
                    cfg[k] = v

    # explicit CLI flags (present because of SUPPRESS) override everything
    for k, v in args.items():
        if k == "config":
            continue
        cfg[k] = v

    # coerce types (INI values are strings; CLI values arrive typed)
    for k in ("sample_rate", "gain", "ppm"):
        cfg[k] = int(cfg[k])
    for k in ("threshold_db", "min_ms", "max_ms", "noise_history_s",
              "window_ms", "floor_percentile"):
        cfg[k] = float(cfg[k])
    cfg["webhook_long"] = str(cfg["webhook_long"]).lower() in ("1", "true", "yes")
    cfg["calibrate"] = str(cfg["calibrate"]).lower() in ("1", "true", "yes")
    cfg["test_webhook"] = str(cfg["test_webhook"]).lower() in ("1", "true", "yes")

    if cfg["log"] is None and cfg["source"] == "rtl" and not cfg["calibrate"]:
        cfg["log"] = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "pings.csv")
    return cfg


def window_db(samples):
    """RMS power of one window in dB (raw ADC units, ref = 1)."""
    if not samples:
        return -200.0
    acc = 0.0
    for s in samples:
        acc += s * s
    rms = math.sqrt(acc / len(samples))
    return 20.0 * math.log10(rms) if rms > 1e-9 else -200.0


def percentile_sorted(sorted_vals, pct):
    """Low percentile of a sorted list (robust noise-floor estimator)."""
    if not sorted_vals:
        return 0.0
    k = int(math.ceil(pct / 100.0 * len(sorted_vals))) - 1
    k = max(0, min(len(sorted_vals) - 1, k))
    return sorted_vals[k]


def spawn_rtl_fm(cfg):
    cmd = ["rtl_fm", "-f", cfg["frequency"], "-s", str(cfg["sample_rate"]),
           "-M", "am", "-g", str(cfg["gain"]), "-p", str(cfg["ppm"])]
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("ERROR: rtl_fm not found. Install with: sudo apt install rtl-sdr", file=sys.stderr)
        sys.exit(1)


def post_webhook(url, text):
    """Discord webhook POST via curl subprocess (urllib is unreliable here).
    Returns True on success so --test-webhook can report failure."""
    try:
        payload = json.dumps({"content": text})
        r = subprocess.run(["curl", "-s", "-m", "10", "-X", "POST", url,
                            "-H", "Content-Type: application/json", "-d", payload],
                           capture_output=True)
        return r.returncode == 0
    except Exception as exc:  # alerting must never kill the detector
        print(f"WARN: webhook failed: {exc}", file=sys.stderr)
        return False


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(cfg):
    W = max(1, int(round(cfg["sample_rate"] * cfg["window_ms"] / 1000.0)))
    hist_windows = max(10, int(round(cfg["noise_history_s"] * 1000.0 / cfg["window_ms"])))
    print(f"[graves-detector] {cfg['frequency']} @ {cfg['sample_rate']} Hz, "
          f"gain {cfg['gain']} dB, ppm {cfg['ppm']}, window {W} samples "
          f"({cfg['window_ms']:.0f} ms), threshold +{cfg['threshold_db']:.0f} dB, "
          f"min {cfg['min_ms']:.0f} ms / max {cfg['max_ms']:.0f} ms", flush=True)

    proc = None
    if cfg["source"] == "rtl":
        proc = spawn_rtl_fm(cfg)
        stream = proc.stdout
    else:
        stream = sys.stdin.buffer

    floor_hist = deque(maxlen=hist_windows)
    buf = []

    csv_f = None
    csv_w = None
    if cfg["log"] and not cfg["calibrate"]:
        path = os.path.abspath(cfg["log"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        csv_f = open(path, "a", newline="")
        csv_w = csv.writer(csv_f)
        if new_file:
            csv_w.writerow(["utc", "local", "start_ms", "duration_ms",
                            "peak_db_over_floor", "peak_level_db", "floor_db",
                            "kind", "note"])
        csv_f.flush()
        print(f"[graves-detector] logging events to {path}", flush=True)

    live_f = None
    live_w = None
    next_live = None
    if cfg["live_out"] and not cfg["calibrate"]:
        path = os.path.abspath(cfg["live_out"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        live_f = open(path, "a", newline="")
        live_w = csv.writer(live_f)
        if new_file:
            live_w.writerow(["epoch_ms", "db", "floor"])
        live_f.flush()
        next_live = time.time() + 1.0
        print(f"[graves-detector] live samples to {path}", flush=True)

    # detection state
    active = False
    start_idx = 0
    last_active_idx = 0
    peak_above = 0.0
    peak_level = -200.0
    floor_at_peak = -200.0
    below_count = 0
    idx = 0

    def emit(_end_idx):
        nonlocal active
        # duration measured to the LAST above-threshold window; the trailing
        # confirmation windows (hysteresis) must not inflate short pings
        dur_ms = (last_active_idx - start_idx + 1) * cfg["window_ms"]
        above = peak_above
        if dur_ms < cfg["min_ms"]:
            active = False
            return  # sub-threshold blip: ignore entirely
        kind = "PING" if dur_ms <= cfg["max_ms"] else "LONG"
        note = "sporadic-E / interference?" if kind == "LONG" else ""
        row = [utc_now(), local_now(), int(start_idx * cfg["window_ms"]),
               f"{dur_ms:.0f}", f"{above:.1f}", f"{peak_level:.1f}",
               f"{floor_at_peak:.1f}", kind, note]
        if csv_w is not None:
            csv_w.writerow(row)
            csv_f.flush()
        print(f"[{kind}] {row[0]} start {row[2]} ms dur {row[3]} ms "
              f"+{row[4]} dB over floor", flush=True)
        if cfg["webhook"] and (kind == "PING" or cfg["webhook_long"]):
            icon = "⚡" if kind == "PING" else "⏳"
            msg = (f"{icon} **{cfg['name']}** {kind} @ {row[0]}\n"
                   f"`{row[3]} ms · +{row[4]} dB over floor · peak {row[5]} dB`")
            post_webhook(cfg["webhook"], msg)
        active = False

    try:
        db = None
        floor = None
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            arr = array.array("h")
            try:
                arr.frombytes(chunk)
            except ValueError:
                break  # truncated final chunk
            buf.extend(arr.tolist())
            while len(buf) >= W:
                win = buf[:W]
                del buf[:W]
                db = window_db(win)
                if floor_hist:
                    # robust floor: low percentile, NOT the median - a strong
                    # long-lived signal (sporadic-E / interference) must not
                    # raise the floor and mask subsequent meteor pings
                    floor = percentile_sorted(sorted(floor_hist),
                                              cfg["floor_percentile"])
                else:
                    floor = db
                floor_hist.append(db)
                above = db - floor

                if live_w is not None and time.time() >= next_live:
                    live_w.writerow([int(time.time() * 1000),
                                     f"{db:.1f}", f"{floor:.1f}"])
                    live_f.flush()
                    next_live = time.time() + 1.0

                if cfg["calibrate"]:
                    if idx % max(1, int(cfg["sample_rate"] / W)) == 0:
                        print(f"[{local_now()}] level {db:+.1f} dB | floor "
                              f"{floor:+.1f} | delta {above:+.1f} dB | "
                              f"win {idx}", flush=True)
                else:
                    if not active:
                        if above >= cfg["threshold_db"]:
                            active = True
                            start_idx = idx
                            last_active_idx = idx
                            peak_above = above
                            peak_level = db
                            floor_at_peak = floor
                            below_count = 0
                    else:
                        if above >= cfg["threshold_db"]:
                            below_count = 0
                            last_active_idx = idx
                            if above > peak_above:
                                peak_above = above
                                peak_level = db
                                floor_at_peak = floor
                        else:
                            below_count += 1
                            if below_count >= 2:  # two consecutive quiet windows => end
                                emit(idx)
                idx += 1
        if active:
            emit(idx - 1)  # close trailing event at stream end
        if live_w is not None and db is not None:  # final state sample
            live_w.writerow([int(time.time() * 1000),
                             f"{db:.1f}", f"{floor:.1f}"])
            live_f.flush()
    except KeyboardInterrupt:
        print("\n[graves-detector] stopped by operator", flush=True)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr_tail = (proc.stderr.read() or b"").decode(errors="replace")[-2000:]
            if proc.returncode not in (0, -15) and stderr_tail.strip():
                print("rtl_fm stderr tail:", stderr_tail, file=sys.stderr)
        if csv_f is not None:
            csv_f.close()
        if live_f is not None:
            live_f.close()


def main():
    cfg = load_config(sys.argv[1:])
    if cfg["test_webhook"]:
        if not cfg["webhook"]:
            print("ERROR: --test-webhook needs a webhook URL "
                  "(--webhook or webhook= in config.ini)", file=sys.stderr)
            sys.exit(2)
        ok = post_webhook(cfg["webhook"],
                          f"✅ **{cfg['name']}** webhook test — GRAVES detector online")
        print("Test message sent — check your Discord channel." if ok
              else "FAILED to send test message.", flush=True)
        sys.exit(0 if ok else 1)
    run(cfg)


if __name__ == "__main__":
    main()
