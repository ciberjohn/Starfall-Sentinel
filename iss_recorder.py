#!/usr/bin/env python3
"""ISS pass listener: retunes the RTL-SDR to an ISS ham frequency for a fixed
window, saves any above-floor audio (voice, SSTV tone, APRS packet bursts -
whatever's actually on the air) as short WAV clips, and logs each one.

Same windowed RMS-envelope technique as detector.py (percentile noise floor,
threshold-above-floor, hangover to close an event), but retuned for FM
instead of AM/CW. That retuning matters: raw FM demodulation is *loud and
noisy with no carrier present* (discriminator noise, not silence) and only a
few dB *louder* when a real signal keys up - nothing like GRAVES' AM channel,
which sits near-silent between pings. Measured on this station's own dongle:
idle 2 m NBFM floor sits in a tight ~76-78 dB band; a solidly modulated
signal (tested against a strong FM broadcast station) reads ~2-3 dB above
that. `rtl_fm`'s own `-l` squelch was tried first and rejected - on this
hardware it opened and closed with no reliable relationship to actual signal
strength. threshold_db defaults low (see load_config) to match that small
real-world margin; use --calibrate against a real pass to retune it.

Meant to be launched by iss_scheduler.py for the duration of one ISS pass,
with the GRAVES detector paused for that window - one dongle, one channel at
a time. Exits on its own once --duration-s elapses.

Usage:
  python3 iss_recorder.py --duration-s 300 --config config.ini
  python3 iss_recorder.py --calibrate --frequency 145.825M   # live tuning
  python3 iss_recorder.py --duration-s 20 --source stdin < test.pcm
"""

import argparse
import array
import configparser
import csv
import datetime
import math
import os
import subprocess
import sys
import time
import wave
from collections import deque

DEFAULT_FREQ = "145.825M"  # ISS APRS digipeater - documented as the most
                            # consistently-active ISS ham frequency, so it's
                            # the best single-channel bet for "probably
                            # captures something" (vs. 145.800 voice, which
                            # is silent outside a scheduled ARISS contact)
DEFAULT_RATE = 48000


def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="INI file with an [iss] section")
    p.add_argument("--frequency", default=argparse.SUPPRESS, help="RX frequency, rtl_fm syntax (145.825M)")
    p.add_argument("--sample-rate", type=int, default=argparse.SUPPRESS, help="audio sample rate Hz")
    p.add_argument("--gain", type=int, default=argparse.SUPPRESS, help="SDR gain dB (0=auto)")
    p.add_argument("--ppm", type=int, default=argparse.SUPPRESS, help="SDR frequency correction ppm")
    p.add_argument("--threshold-db", type=float, default=argparse.SUPPRESS,
                   help="event threshold above noise floor (dB) - FM's margin is much smaller than AM")
    p.add_argument("--min-ms", type=float, default=argparse.SUPPRESS, help="min event duration (ms)")
    p.add_argument("--hangover-windows", type=int, default=argparse.SUPPRESS,
                   help="consecutive below-threshold windows before closing an event")
    p.add_argument("--preroll-s", type=float, default=argparse.SUPPRESS,
                   help="audio to keep before the trigger, so a clip doesn't start mid-word")
    p.add_argument("--clip-max-s", type=float, default=argparse.SUPPRESS,
                   help="force-close/save a clip at this length (long voice contacts)")
    p.add_argument("--noise-history-s", type=float, default=argparse.SUPPRESS, help="noise floor averaging window (s)")
    p.add_argument("--floor-percentile", type=float, default=argparse.SUPPRESS, help="low percentile of recent power used as noise floor")
    p.add_argument("--window-ms", type=float, default=argparse.SUPPRESS, help="power window (ms)")
    p.add_argument("--duration-s", type=float, default=argparse.SUPPRESS, help="total run length (the pass window)")
    p.add_argument("--source", choices=["rtl", "stdin"], default=argparse.SUPPRESS)
    p.add_argument("--hits-log", default=argparse.SUPPRESS, help="CSV log path")
    p.add_argument("--clips-dir", default=argparse.SUPPRESS, help="directory for saved WAV clips")
    p.add_argument("--pass-aos", default=argparse.SUPPRESS, help="AOS timestamp of this pass, logged as context only")
    p.add_argument("--calibrate", action="store_true", default=argparse.SUPPRESS,
                   help="live level monitor, no logging/recording")
    return p


def load_config(argv):
    cfg = {
        "frequency": DEFAULT_FREQ,
        "sample_rate": DEFAULT_RATE,
        "gain": 40,
        "ppm": 0,
        # FM's idle-vs-signal margin is a few dB, not GRAVES' 10+ - see the
        # module docstring for the measurement this default is based on.
        "threshold_db": 4.0,
        "min_ms": 150.0,          # a full AFSK packet burst is well under 1s
        "hangover_windows": 3,
        "preroll_s": 0.5,
        "clip_max_s": 45.0,       # cap a long voice contact to a listenable clip
        "noise_history_s": 8.0,   # a pass is only minutes long - short history is fine
        "floor_percentile": 20.0,
        "window_ms": 50.0,
        "duration_s": 360.0,
        "source": "rtl",
        "hits_log": None,
        "clips_dir": None,
        "pass_aos": "",
        "calibrate": False,
    }
    args = vars(build_parser().parse_args(argv))

    ini_path = args.get("config")
    if ini_path and os.path.exists(ini_path):
        cp = configparser.ConfigParser()
        cp.read(ini_path)
        if cp.has_section("iss"):
            for k, v in cp["iss"].items():
                if k in cfg:
                    cfg[k] = v

    for k, v in args.items():
        if k == "config":
            continue
        cfg[k] = v

    for k in ("sample_rate", "gain", "ppm", "hangover_windows"):
        cfg[k] = int(cfg[k])
    for k in ("threshold_db", "min_ms", "preroll_s", "clip_max_s",
              "noise_history_s", "window_ms", "floor_percentile", "duration_s"):
        cfg[k] = float(cfg[k])
    cfg["calibrate"] = str(cfg["calibrate"]).lower() in ("1", "true", "yes")

    here = os.path.dirname(os.path.abspath(__file__))
    if cfg["hits_log"] is None:
        cfg["hits_log"] = os.path.join(here, "data", "iss_hits.csv")
    if cfg["clips_dir"] is None:
        cfg["clips_dir"] = os.path.join(here, "data", "iss_clips")
    return cfg


def window_db(samples):
    """RMS power of one window in dB (raw ADC units, ref = 1) - identical
    formula to detector.py's, kept as its own copy: different signal domain,
    different tuning, and each script stays a self-contained CLI tool."""
    if not samples:
        return -200.0
    acc = 0.0
    for s in samples:
        acc += s * s
    rms = math.sqrt(acc / len(samples))
    return 20.0 * math.log10(rms) if rms > 1e-9 else -200.0


def percentile_sorted(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    k = int(math.ceil(pct / 100.0 * len(sorted_vals))) - 1
    k = max(0, min(len(sorted_vals) - 1, k))
    return sorted_vals[k]


def spawn_rtl_fm(cfg):
    cmd = ["rtl_fm", "-f", cfg["frequency"], "-s", str(cfg["sample_rate"]),
           "-M", "fm", "-g", str(cfg["gain"]), "-p", str(cfg["ppm"])]
    try:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("ERROR: rtl_fm not found. Install with: sudo apt install rtl-sdr", file=sys.stderr)
        sys.exit(1)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_wav(path, sample_rate, int_samples):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(array.array("h", int_samples).tobytes())


def run(cfg):
    W = max(1, int(round(cfg["sample_rate"] * cfg["window_ms"] / 1000.0)))
    hist_windows = max(5, int(round(cfg["noise_history_s"] * 1000.0 / cfg["window_ms"])))
    preroll_windows = max(0, int(round(cfg["preroll_s"] * 1000.0 / cfg["window_ms"])))
    clip_max_windows = max(1, int(round(cfg["clip_max_s"] * 1000.0 / cfg["window_ms"])))
    print(f"[iss-recorder] {cfg['frequency']} @ {cfg['sample_rate']} Hz, gain {cfg['gain']} dB, "
          f"ppm {cfg['ppm']}, threshold +{cfg['threshold_db']:.1f} dB, "
          f"duration {cfg['duration_s']:.0f}s", flush=True)

    proc = None
    if cfg["source"] == "rtl":
        proc = spawn_rtl_fm(cfg)
        stream = proc.stdout
    else:
        stream = sys.stdin.buffer

    floor_hist = deque(maxlen=hist_windows)
    preroll = deque(maxlen=preroll_windows) if preroll_windows else deque(maxlen=1)
    buf = []

    csv_f = csv_w = None
    if not cfg["calibrate"]:
        path = os.path.abspath(cfg["hits_log"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        csv_f = open(path, "a", newline="")
        csv_w = csv.writer(csv_f)
        if new_file:
            csv_w.writerow(["utc", "local", "pass_aos_utc", "duration_ms",
                            "peak_db_over_floor", "peak_level_db", "floor_db",
                            "frequency", "clip_file"])
        os.makedirs(cfg["clips_dir"], exist_ok=True)

    active = False
    event_samples = []
    peak_above = 0.0
    peak_level = -200.0
    floor_at_peak = -200.0
    below_count = 0
    idx = 0
    hit_count = 0

    def emit():
        nonlocal active, event_samples, hit_count
        dur_ms = len(event_samples) / cfg["sample_rate"] * 1000.0
        if dur_ms < cfg["min_ms"]:
            active = False
            event_samples = []
            return
        ts = utc_now()
        # millisecond resolution + a running counter: back-to-back bursts
        # (e.g. successive AFSK packets) can land in the same wall-clock
        # second, and a filename collision would silently overwrite a clip
        stamp = ts.replace(":", "").replace("-", "").replace(".", "").rstrip("Z")
        fname = f"iss_{stamp}Z_{hit_count:03d}.wav"
        fpath = os.path.join(cfg["clips_dir"], fname)
        write_wav(fpath, cfg["sample_rate"], event_samples)
        row = [ts, local_now(), cfg["pass_aos"], f"{dur_ms:.0f}",
               f"{peak_above:.1f}", f"{peak_level:.1f}", f"{floor_at_peak:.1f}",
               cfg["frequency"], fname]
        csv_w.writerow(row)
        csv_f.flush()
        hit_count += 1
        print(f"[ISS HIT] {ts} dur {dur_ms:.0f} ms +{peak_above:.1f} dB over floor -> {fname}", flush=True)
        active = False
        event_samples = []

    deadline = time.time() + cfg["duration_s"]
    try:
        while time.time() < deadline:
            chunk = stream.read(4096)
            if not chunk:
                break
            arr = array.array("h")
            try:
                arr.frombytes(chunk)
            except ValueError:
                break
            buf.extend(arr.tolist())
            while len(buf) >= W:
                win = buf[:W]
                del buf[:W]
                db = window_db(win)
                floor = percentile_sorted(sorted(floor_hist), cfg["floor_percentile"]) if floor_hist else db
                floor_hist.append(db)
                above = db - floor

                if cfg["calibrate"]:
                    if idx % max(1, int(cfg["sample_rate"] / W)) == 0:
                        print(f"[{local_now()}] level {db:+.1f} dB | floor {floor:+.1f} | "
                              f"delta {above:+.1f} dB | win {idx}", flush=True)
                    idx += 1
                    continue

                if not active:
                    preroll.append(win)
                    if above >= cfg["threshold_db"]:
                        active = True
                        event_samples = []
                        for w_ in preroll:
                            event_samples.extend(w_)
                        peak_above, peak_level, floor_at_peak = above, db, floor
                        below_count = 0
                else:
                    event_samples.extend(win)
                    if above >= cfg["threshold_db"]:
                        below_count = 0
                        if above > peak_above:
                            peak_above, peak_level, floor_at_peak = above, db, floor
                    else:
                        below_count += 1
                        if below_count >= cfg["hangover_windows"]:
                            emit()
                    if active and len(event_samples) >= clip_max_windows * W:
                        emit()  # force-close a runaway-long transmission, chain into a new clip
                idx += 1
        if active:
            emit()
    except KeyboardInterrupt:
        print("\n[iss-recorder] stopped by operator", flush=True)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if csv_f is not None:
            csv_f.close()

    if not cfg["calibrate"]:
        print(f"[iss-recorder] done - {hit_count} hit(s) this pass", flush=True)


def main():
    cfg = load_config(sys.argv[1:])
    run(cfg)


if __name__ == "__main__":
    main()
