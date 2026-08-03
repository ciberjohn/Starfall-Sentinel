#!/usr/bin/env python3
"""Synthetic GRAVES audio generator + end-to-end detector test.

Generates Gaussian noise with injected meteor-ping bursts (fast rise,
exponential decay, random SNR/duration) plus one LONG interference event,
then verifies the detector reports exactly what was injected.

Usage:
  python3 simulate.py --out /tmp/test.pcm     # write raw 16-bit LE PCM
  python3 simulate.py --test                  # pipe into detector.py and verify
"""

import argparse
import array
import csv
import math
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def gen_audio(cfg):
    rate = cfg["sample_rate"]
    total = int(rate * cfg["duration"])
    sigma = cfg["noise_sigma"]
    rng = random.Random(cfg["seed"])
    samples = [rng.gauss(0.0, sigma) for _ in range(total)]

    events = []  # injected ground truth

    # meteor pings - sequential placement, never overlapping in time
    max_dur = cfg["max_ms"] / 1000.0
    gap = max_dur + 0.6  # min gap between END of one ping and start of next
    n = cfg["n_pings"]
    s0 = rng.uniform(1.0, max(2.0, 15.0 - (n - 1) * gap))
    starts = [s0]
    for _ in range(n - 1):
        starts.append(starts[-1] + rng.uniform(gap, gap + 0.4))
    for start in starts:
        dur = rng.uniform(cfg["min_ms"], cfg["max_ms"]) / 1000.0
        snr_db = rng.uniform(cfg["snr_min"], cfg["snr_max"])
        amp = sigma * (10.0 ** (snr_db / 20.0))
        events.append({"kind": "PING", "start": start, "dur": dur,
                       "snr_db": snr_db})
        s0 = int(start * rate)
        n = int(dur * rate)
        rise = max(1, int(0.02 * rate))
        for j in range(n):
            if j < rise:
                env = j / rise
            else:
                env = math.exp(-(j - rise) / rate / (dur * 0.5))
            idx = s0 + j
            if idx < total:
                samples[idx] += amp * env * rng.gauss(0.0, 1.0)

    # one LONG interference event (e.g. sporadic-E / aircraft scatter)
    long_dur = cfg["long_dur"]
    last_ping_end = starts[-1] + max_dur + 1.0
    start = rng.uniform(max(16.0, last_ping_end),
                        max(17.0, cfg["duration"] - long_dur - 1.0))
    amp = sigma * (10.0 ** (cfg["long_snr_db"] / 20.0))
    events.append({"kind": "LONG", "start": start, "dur": long_dur,
                   "snr_db": cfg["long_snr_db"]})
    s0 = int(start * rate)
    n = int(long_dur * rate)
    for j in range(n):
        idx = s0 + j
        if idx < total:
            samples[idx] += amp * rng.gauss(0.0, 1.0)

    return samples, events


def to_pcm(samples):
    a = array.array("h")
    for s in samples:
        v = int(round(s))
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        a.append(v)
    return a.tobytes()


def parse_csv(path):
    rows = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    return rows


def run_test(cfg):
    samples, events = gen_audio(cfg)
    events = sorted(events, key=lambda e: e["start"])  # chronological
    pcm = to_pcm(samples)
    print(f"[simulate] generated {cfg['duration']} s of {cfg['sample_rate']} Hz "
          f"audio: {cfg['n_pings']} pings + 1 LONG, seed {cfg['seed']}")

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "pings.csv")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "detector.py"),
             "--source", "stdin",
             "--log", csv_path,
             "--window-ms", str(cfg["window_ms"]),
             "--threshold-db", str(cfg["threshold_db"]),
             "--min-ms", str(cfg["detector_min_ms"]),
             "--max-ms", str(cfg["detector_max_ms"]),
             "--sample-rate", str(cfg["sample_rate"])],
            input=pcm, capture_output=True, timeout=120)
        if proc.returncode != 0:
            print("DETECTOR FAILED:", proc.stderr.decode(errors="replace"))
            return False
        rows = parse_csv(csv_path)
    rows = sorted(rows, key=lambda r: int(r["start_ms"]))

    pings = [r for r in rows if r["kind"] == "PING"]
    longs = [r for r in rows if r["kind"] == "LONG"]
    exp_pings = [e for e in events if e["kind"] == "PING"]
    exp_longs = [e for e in events if e["kind"] == "LONG"]

    ok = True
    print(f"\nDetected: {len(pings)} PING rows, {len(longs)} LONG rows\n")
    print(f"{'detected':>10} {'start_ms':>10} {'dur_ms':>8} {'+dB':>7}  truth(start,dur,snr)")
    for i, r in enumerate(rows):
        e = events[i] if i < len(events) else None
        t = (f"({e['start']:.1f}s,{e['dur']*1000:.0f}ms,{e['snr_db']:.0f}dB)"
             if e else "?")
        print(f"{r['kind']:>10} {r['start_ms']:>10} {r['duration_ms']:>8} "
              f"{r['peak_db_over_floor']:>7}  {t}")

    # assertions
    if len(pings) != len(exp_pings):
        print(f"\nFAIL: expected {len(exp_pings)} PING rows, got {len(pings)}")
        ok = False
    for i, e in enumerate(exp_pings):
        if i < len(pings):
            d = float(pings[i]["duration_ms"])
            if not (0.3 * e["dur"] * 1000 <= d <= 1.3 * e["dur"] * 1000):
                print(f"FAIL: ping #{i} duration {d:.0f} ms outside "
                      f"tolerance of truth {e['dur']*1000:.0f} ms")
                ok = False
        else:
            ok = False
    if len(longs) != len(exp_longs):
        print(f"FAIL: expected {len(exp_longs)} LONG rows, got {len(longs)}")
        ok = False
    for i, e in enumerate(exp_longs):
        if i < len(longs):
            d = float(longs[i]["duration_ms"])
            if not (0.8 * e["dur"] * 1000 <= d <= 1.2 * e["dur"] * 1000):
                print(f"FAIL: long event duration {d:.0f} ms outside tolerance "
                      f"of truth {e['dur']*1000:.0f} ms")
                ok = False
        else:
            ok = False

    # false positives: any row not attributable (window edge effects only)
    if len(rows) > len(exp_pings) + len(exp_longs):
        extra = len(rows) - len(exp_pings) - len(exp_longs)
        print(f"WARN: {extra} extra rows (may be window-edge splits)")
    print(f"\n{'PASS' if ok else 'FAIL'}: end-to-end detector test")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="write raw PCM to file")
    ap.add_argument("--test", action="store_true", help="run detector test")
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--n-pings", type=int, default=6)
    ap.add_argument("--min-ms", type=float, default=250.0)
    ap.add_argument("--max-ms", type=float, default=2500.0)
    ap.add_argument("--snr-min", type=float, default=16.0)
    ap.add_argument("--snr-max", type=float, default=24.0)
    ap.add_argument("--long-snr-db", type=float, default=18.0)
    ap.add_argument("--long-dur", type=float, default=10.0)
    ap.add_argument("--detector-max-ms", type=float, default=8000.0,
                    help="detector LONG threshold (independent of ping max-ms)")
    ap.add_argument("--detector-min-ms", type=float, default=80.0,
                    help="detector minimum event duration")
    ap.add_argument("--noise-sigma", type=float, default=200.0)
    ap.add_argument("--threshold-db", type=float, default=10.0)
    ap.add_argument("--window-ms", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    cfg = vars(args)
    if cfg["out"]:
        samples, events = gen_audio(cfg)
        with open(cfg["out"], "wb") as f:
            f.write(to_pcm(samples))
        print(f"wrote {cfg['out']} ({cfg['duration']} s raw 16-bit LE PCM)")
        return 0
    if cfg["test"]:
        return 0 if run_test(cfg) else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
