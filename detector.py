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
import io
import json
import math
import os
import subprocess
import sys
import tarfile
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
    p.add_argument("--live-max-hours", type=float, default=argparse.SUPPRESS,
                   help="trim live_out to the last N hours (0 = never trim, keep forever)")
    p.add_argument("--webhook", default=argparse.SUPPRESS, help="Discord webhook URL for alerts")
    p.add_argument("--curve-dir", default=argparse.SUPPRESS,
                   help="save per-ping waveform curves to this dir (CSV: t_ms,db,floor)")
    p.add_argument("--curve-pre-s", type=float, default=argparse.SUPPRESS,
                   help="pre-roll seconds of curve before the event (default 1.0)")
    p.add_argument("--curve-post-s", type=float, default=argparse.SUPPRESS,
                   help="post-roll seconds of curve after the event (default 1.0)")
    p.add_argument("--curve-retention-days", type=float, default=argparse.SUPPRESS,
                   help="keep curves live this long before archiving (default 182 = 6 months)")
    p.add_argument("--curve-archive-days", type=float, default=argparse.SUPPRESS,
                   help="keep archived curve tarballs this long (default 730 = 2 years)")
    p.add_argument("--curve-archive-dir", default=argparse.SUPPRESS,
                   help="archive dir for old curves (default <curve_dir>_archive)")
    p.add_argument("--test-webhook", action="store_true", default=argparse.SUPPRESS,
                   help="send a test message to the configured webhook and exit")
    p.add_argument("--archive-only", action="store_true", default=argparse.SUPPRESS,
                   help="run the curve archive sweep once and exit (for a systemd timer)")
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
        "live_max_hours": 12.0,
        "webhook": None,
        "test_webhook": False,
        "webhook_long": False,
        "curve_dir": None,
        "curve_pre_s": 1.0,
        "curve_post_s": 1.0,
        "curve_retention_days": 182.0,
        "curve_archive_days": 730.0,
        "curve_archive_dir": None,
        "archive_only": False,
        "name": "starfall-1",
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
              "window_ms", "floor_percentile", "live_max_hours",
              "curve_pre_s", "curve_post_s",
              "curve_retention_days", "curve_archive_days"):
        cfg[k] = float(cfg[k])
    cfg["webhook_long"] = str(cfg["webhook_long"]).lower() in ("1", "true", "yes")
    cfg["calibrate"] = str(cfg["calibrate"]).lower() in ("1", "true", "yes")
    cfg["test_webhook"] = str(cfg["test_webhook"]).lower() in ("1", "true", "yes")

    if cfg["log"] is None and cfg["source"] == "rtl" and not cfg["calibrate"]:
        cfg["log"] = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "pings.csv")
    if cfg["curve_dir"] is None and cfg["log"] and not cfg["calibrate"]:
        cfg["curve_dir"] = os.path.join(
            os.path.dirname(os.path.abspath(cfg["log"])), "ping_curves")
    if cfg["curve_archive_dir"] is None and cfg["curve_dir"]:
        cfg["curve_archive_dir"] = cfg["curve_dir"] + "_archive"
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


def post_webhook(url, text, image_path=None):
    """Discord webhook POST via curl subprocess (urllib is unreliable here).
    With image_path, posts a multipart message with the file attached.
    Returns True on success so --test-webhook can report failure."""
    try:
        if image_path and os.path.exists(image_path):
            r = subprocess.run(["curl", "-s", "-m", "15", "-X", "POST", url,
                                "-F", f"content={text}",
                                "-F", f"file=@{image_path}"],
                               capture_output=True)
        else:
            payload = json.dumps({"content": text})
            r = subprocess.run(["curl", "-s", "-m", "10", "-X", "POST", url,
                                "-H", "Content-Type: application/json", "-d", payload],
                               capture_output=True)
        return r.returncode == 0
    except Exception as exc:  # alerting must never kill the detector
        print(f"WARN: webhook failed: {exc}", file=sys.stderr)
        return False


def archive_curves(curve_dir, archive_dir, retention_days, archive_days):
    """Move curves older than retention_days into monthly .tar.gz archives,
    and delete archive tarballs older than archive_days (2-year story).

    Idempotent and crash-safe: the monthly tarball is rebuilt from its
    existing members plus the newly-expired CSVs, then the source CSVs are
    deleted - a failed run leaves everything in place for the next sweep.
    Curves cost ~1.5 KB each, so a month is small; rebuilding the tarball
    is cheap and avoids gzip-append pitfalls."""
    if not os.path.isdir(curve_dir) or retention_days <= 0:
        return 0
    os.makedirs(archive_dir, exist_ok=True)
    now = time.time()
    cutoff = now - retention_days * 86400.0
    prune_cutoff = now - archive_days * 86400.0

    expired = {}  # month_key "YYYY-MM" -> [csv paths]
    for name in sorted(os.listdir(curve_dir)):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(curve_dir, name)
        try:
            if os.path.getmtime(path) < cutoff:
                month = name[:4] + "-" + name[4:6] if len(name) >= 6 else "unknown"
                expired.setdefault(month, []).append(path)
        except OSError:
            continue
    if not expired:
        return 0

    archived = 0
    for month, paths in expired.items():
        tar_path = os.path.join(archive_dir, f"ping_curves_{month}.tar.gz")
        members = {}
        if os.path.exists(tar_path):
            try:
                with tarfile.open(tar_path, "r:gz") as tf:
                    for m in tf.getmembers():
                        f = tf.extractfile(m)
                        if f is not None:
                            members[m.name] = f.read()
            except tarfile.TarError:
                members = {}  # corrupt tarball: rebuild from scratch
        for p in paths:
            try:
                with open(p, "rb") as f:
                    members[os.path.basename(p)] = f.read()
            except OSError:
                continue
        with tarfile.open(tar_path, "w:gz") as tf:
            for name, data in sorted(members.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = int(now)
                tf.addfile(info, io.BytesIO(data))
        for p in paths:
            try:
                os.remove(p)
                archived += 1
            except OSError:
                pass

    pruned = 0
    for name in os.listdir(archive_dir):
        if not name.endswith(".tar.gz"):
            continue
        path = os.path.join(archive_dir, name)
        try:
            if os.path.getmtime(path) < prune_cutoff:
                os.remove(path)
                pruned += 1
        except OSError:
            pass
    return archived


def trim_live_csv(path, max_hours):
    """Keep only the last max_hours of rows in the live-samples CSV. Called
    periodically, not per-row - at 1 row/s this file is small (~26 bytes/row,
    well under a year to reach even 10 MB), so trimming isn't urgent, but an
    unbounded file is still bad hygiene and this keeps dashboard.py's /api/live
    (which reads the file's tail) bounded regardless of uptime."""
    if max_hours <= 0:
        return  # 0 = keep forever, opt-in only
    cutoff_ms = int((time.time() - max_hours * 3600) * 1000)
    try:
        with open(path, "r", newline="") as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return
    if len(rows) < 2:
        return
    header, body = rows[0], rows[1:]
    kept = [r for r in body if r and r[0].isdigit() and int(r[0]) >= cutoff_ms]
    if len(kept) == len(body):
        return  # nothing to trim yet
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(kept)
    os.replace(tmp_path, path)
    return True


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
    # per-ping waveform capture: ring buffer of (idx, db, floor) at window
    # rate (~20 Hz); keeps pre/post roll + up to 30 s of the event itself
    curve_keep = int((cfg["curve_pre_s"] + cfg["curve_post_s"] + 30.0)
                     * 1000.0 / max(1.0, cfg["window_ms"])) + 10
    curve_buf = deque(maxlen=max(50, curve_keep))
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
                            "kind", "note", "curve_file"])
        csv_f.flush()
        print(f"[graves-detector] logging events to {path}", flush=True)

    live_f = None
    live_w = None
    next_live = None
    next_trim = None
    next_archive = None
    live_path = None
    TRIM_INTERVAL_S = 1800  # check every 30 min; the file is small, no rush
    ARCHIVE_INTERVAL_S = 21600  # archive sweep every 6 h
    if cfg["live_out"] and not cfg["calibrate"]:
        live_path = os.path.abspath(cfg["live_out"])
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        new_file = not os.path.exists(live_path) or os.path.getsize(live_path) == 0
        live_f = open(live_path, "a", newline="")
        live_w = csv.writer(live_f)
        if new_file:
            live_w.writerow(["epoch_ms", "db", "floor"])
        live_f.flush()
        next_live = time.time() + 1.0
        next_trim = time.time() + TRIM_INTERVAL_S
        print(f"[graves-detector] live samples to {live_path} "
              f"(keeping last {cfg['live_max_hours']:.0f}h)"
              if cfg["live_max_hours"] > 0 else
              f"[graves-detector] live samples to {live_path} (unbounded)",
              flush=True)

    if cfg["curve_dir"] and not cfg["calibrate"]:
        next_archive = time.time() + ARCHIVE_INTERVAL_S
        print(f"[graves-detector] ping curves to {cfg['curve_dir']} "
              f"(live {cfg['curve_retention_days']:.0f}d, archive "
              f"{cfg['curve_archive_days']:.0f}d to "
              f"{cfg['curve_archive_dir']})", flush=True)

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

        # waveform capture: slice the ring buffer around the event and save
        # it as a small curve CSV (t_ms,db,floor) for the dashboard + Discord
        curve_file = ""
        if cfg["curve_dir"] and curve_buf:
            pre = int(cfg["curve_pre_s"] * 1000.0 / cfg["window_ms"])
            post = int(cfg["curve_post_s"] * 1000.0 / cfg["window_ms"])
            lo = max(0, start_idx - pre)
            hi = last_active_idx + post
            rows = [e for e in curve_buf if lo <= e[0] <= hi]
            if len(rows) >= 5:
                os.makedirs(cfg["curve_dir"], exist_ok=True)
                stamp = utc_now().replace("-", "").replace(":", "").replace(".", "")
                curve_file = f"{stamp}_{int(start_idx * cfg['window_ms'])}.csv"
                with open(os.path.join(cfg["curve_dir"], curve_file),
                          "w", newline="") as cf:
                    cw = csv.writer(cf)
                    cw.writerow(["t_ms", "db", "floor"])
                    for (i, d, fl) in rows:
                        cw.writerow([int(i * cfg["window_ms"]),
                                     f"{d:.2f}", f"{fl:.2f}"])

        row = [utc_now(), local_now(), int(start_idx * cfg["window_ms"]),
               f"{dur_ms:.0f}", f"{above:.1f}", f"{peak_level:.1f}",
               f"{floor_at_peak:.1f}", kind, note, curve_file]
        if csv_w is not None:
            csv_w.writerow(row)
            csv_f.flush()
        print(f"[{kind}] {row[0]} start {row[2]} ms dur {row[3]} ms "
              f"+{row[4]} dB over floor"
              + (f" curve {curve_file}" if curve_file else ""), flush=True)
        if cfg["webhook"] and (kind == "PING" or cfg["webhook_long"]):
            icon = "⚡" if kind == "PING" else "⏳"
            msg = (f"{icon} **{cfg['name']}** {kind} @ {row[0]}\n"
                   f"`{row[3]} ms · +{row[4]} dB over floor · peak {row[5]} dB`")
            png_path = None
            if curve_file:
                try:
                    import curve_plot
                    png_path = curve_plot.plot_curve(
                        os.path.join(cfg["curve_dir"], curve_file),
                        title=f"{cfg['name']} {kind} {row[0]}")
                except Exception as exc:
                    print(f"WARN: curve render failed: {exc}", file=sys.stderr)
            post_webhook(cfg["webhook"], msg, png_path)
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
                curve_buf.append((idx, db, floor))
                above = db - floor

                if live_w is not None and time.time() >= next_live:
                    live_w.writerow([int(time.time() * 1000),
                                     f"{db:.1f}", f"{floor:.1f}"])
                    live_f.flush()
                    next_live = time.time() + 1.0

                if live_w is not None and time.time() >= next_trim:
                    # os.replace() inside trim_live_csv swaps the file's
                    # inode out from under any already-open handle, so the
                    # existing live_f/live_w would silently start writing
                    # into a deleted, unreachable file - reopen every time
                    # a trim actually ran to keep writing to the real path.
                    if trim_live_csv(live_path, cfg["live_max_hours"]):
                        live_f.close()
                        live_f = open(live_path, "a", newline="")
                        live_w = csv.writer(live_f)
                    next_trim = time.time() + TRIM_INTERVAL_S

                if next_archive is not None and time.time() >= next_archive:
                    try:
                        n = archive_curves(cfg["curve_dir"],
                                           cfg["curve_archive_dir"],
                                           cfg["curve_retention_days"],
                                           cfg["curve_archive_days"])
                        if n:
                            print(f"[graves-detector] archived {n} curve(s)",
                                  flush=True)
                    except Exception as exc:  # never let housekeeping kill us
                        print(f"WARN: curve archive sweep failed: {exc}",
                              file=sys.stderr)
                    next_archive = time.time() + ARCHIVE_INTERVAL_S

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
    if cfg["archive_only"]:
        # standalone archive sweep (graves-archive.timer) - independent of
        # the detector process so housekeeping survives detector downtime
        n = archive_curves(cfg["curve_dir"], cfg["curve_archive_dir"],
                           cfg["curve_retention_days"],
                           cfg["curve_archive_days"])
        print(f"[graves-detector] archived {n} curve(s) "
              f"-> {cfg['curve_archive_dir']}", flush=True)
        sys.exit(0)
    run(cfg)


if __name__ == "__main__":
    main()
