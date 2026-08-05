#!/usr/bin/env python3
"""Backfill iss_events.csv from an existing iss_hits.csv.

One-time migration / re-cluster utility: reads a hits log (the atomic
per-burst captures) and writes the clustered events log exactly the way
iss_recorder.py's real-time clustering would have, using the same
event_gap_s rule (hits whose start times are closer than the gap belong
to the same transmission).

Usage:
  python3 tools/iss_events_backfill.py [--hits data/iss_hits.csv] [--events data/iss_events.csv] [--gap-s 5.0]
"""

import argparse
import csv
import datetime
import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HITS = os.path.join(HERE, "..", "data", "iss_hits.csv")
DEFAULT_EVENTS = os.path.join(HERE, "..", "data", "iss_events.csv")
DEFAULT_CLIPS = os.path.join(HERE, "..", "data", "iss_clips")


def parse_utc(ts):
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")


def merge_clips(clips_dir, clip_names):
    """Concatenate atomic WAV clips (same rate, mono, 16-bit) into one WAV.
    Returns (merged_filename, merged_bytes) or (None, None) if empty."""
    frames = []
    rate = None
    for name in clip_names:
        path = os.path.join(clips_dir, name)
        if not os.path.exists(path):
            continue
        with wave.open(path, "rb") as w:
            if rate is None:
                rate = w.getframerate()
            frames.append(w.readframes(w.getnframes()))
    if not frames:
        return None, None
    return b"".join(frames), rate


def write_merged(clips_dir, ev, frames, rate):
    stamp = ev["start"].replace(":", "").replace("-", "").replace(".", "").rstrip("Z")
    name = f"iss_{stamp}Z_ev.wav"
    with wave.open(os.path.join(clips_dir, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate or 48000)
        w.writeframes(frames)
    return name


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hits", default=os.path.abspath(DEFAULT_HITS))
    ap.add_argument("--events", default=os.path.abspath(DEFAULT_EVENTS))
    ap.add_argument("--passes", default=os.path.join(os.path.dirname(os.path.abspath(DEFAULT_EVENTS)), "iss_passes.csv"),
                    help="pass-level log path (default: alongside --events)")
    ap.add_argument("--clips-dir", default=os.path.abspath(DEFAULT_CLIPS),
                    help="directory containing the atomic WAV clips to merge")
    ap.add_argument("--gap-s", type=float, default=5.0,
                    help="cluster gap in seconds (default 5.0, must match iss_recorder event_gap_s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the events that WOULD be written, don't write")
    ap.add_argument("--replace", action="store_true",
                    help="truncate the events log before writing (regenerate "
                         "from hits; use after changing the schema, e.g. the "
                         "merged_clip column)")
    args = ap.parse_args()

    rows = []
    with open(args.hits, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("utc", "").startswith("20"):
                rows.append(row)
    rows.sort(key=lambda x: x["utc"])

    if not rows:
        print(f"no hits in {args.hits}")
        return 0

    events = []
    cur = None
    for row in rows:
        ts = row["utc"]
        now = parse_utc(ts)
        if cur is not None:
            gap = (now - cur["last_dt"]).total_seconds()
        else:
            gap = float("inf")
        if cur is None or gap >= args.gap_s:
            if cur is not None:
                events.append(cur)
            cur = {"start": ts, "end": ts, "last_dt": now, "n_hits": 0,
                   "peak_above": -200.0, "peak_level": -200.0,
                   "floor": -200.0, "clips": []}
        cur["end"] = ts
        cur["n_hits"] += 1
        cur["clips"].append(row.get("clip_file", ""))
        pa, pl, fl = (float(row.get("peak_db_over_floor", 0) or 0),
                      float(row.get("peak_level_db", 0) or 0),
                      float(row.get("floor_db", 0) or 0))
        if pa > cur["peak_above"]:
            cur["peak_above"], cur["peak_level"], cur["floor"] = pa, pl, fl
        cur["last_dt"] = now
    if cur is not None:
        events.append(cur)

    if args.dry_run:
        for ev in events:
            n = ev["n_hits"]
            print(f"{ev['start']} -> {ev['end']}  {n} hit(s)  "
                  f"peak +{ev['peak_above']:.1f} dB  "
                  f"{'merged %d hits (ISS burst)' % n if n > 1 else ''}")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.events)), exist_ok=True)
    new_file = (args.replace or not os.path.exists(args.events)
                or os.path.getsize(args.events) == 0)
    with open(args.events, "w" if args.replace else "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["utc_start", "utc_end", "pass_aos_utc", "duration_s",
                        "n_hits", "peak_db_over_floor", "peak_level_db",
                        "floor_db", "frequency", "clip_files", "merged_clip",
                        "note"])
        for ev in events:
            n = ev["n_hits"]
            note = f"merged {n} hits (ISS burst)" if n > 1 else ""
            dur = (parse_utc(ev["end"]) - parse_utc(ev["start"])).total_seconds()
            merged_name = ""
            if n > 1:
                frames, rate = merge_clips(args.clips_dir, ev["clips"])
                if frames:
                    merged_name = write_merged(args.clips_dir, ev, frames, rate)
            ev["merged_clip"] = merged_name  # pass builder reads this
            w.writerow([ev["start"], ev["end"], rows[0].get("pass_aos_utc", ""),
                        f"{dur:.1f}", str(n), f"{ev['peak_above']:.1f}",
                        f"{ev['peak_level']:.1f}", f"{ev['floor']:.1f}",
                        rows[0].get("frequency", ""), ",".join(ev["clips"]),
                        merged_name, note])
    print(f"wrote {len(events)} event(s) to {args.events}")

    # ── pass-level aggregation: group events by pass_aos_utc, build one
    # pass clip per pass (merged event clips + real silence gaps) ──────
    if not args.dry_run:
        pass_by_aos = {}
        # all events in a single hits log come from one recorder run => one
        # pass_aos_utc; fall back to the first event start if it's empty
        shared_aos = rows[0].get("pass_aos_utc", "") or events[0]["start"]
        for ev in events:
            key = shared_aos
            pass_by_aos.setdefault(key, []).append(ev)

        with open(args.passes, "a", newline="") as f:
            w = csv.writer(f)
            new_passes = not os.path.exists(args.passes) or os.path.getsize(args.passes) == 0
            if new_passes:
                w.writerow(["pass_aos_utc", "pass_start", "pass_end",
                            "duration_s", "n_events", "n_hits",
                            "peak_db_over_floor", "peak_level_db",
                            "floor_db", "frequency", "pass_clip"])
            for aos, evs in pass_by_aos.items():
                evs.sort(key=lambda e: e["start"])
                pass_start = evs[0]["start"]
                pass_end = evs[-1]["end"]
                dur = (parse_utc(pass_end) - parse_utc(pass_start)).total_seconds()
                n_events = len(evs)
                n_hits = sum(int(e["n_hits"]) for e in evs)
                peak = max(float(e["peak_above"]) for e in evs)
                peak_ev = max(evs, key=lambda e: float(e["peak_above"]))
                # build pass clip: merged event clips + silence gaps
                pass_frames = []
                rate = None
                prev_end = None
                for ev in evs:
                    merged = ev.get("merged_clip", "")
                    path = os.path.join(args.clips_dir, merged) if merged else ""
                    if not path or not os.path.exists(path):
                        continue
                    with wave.open(path, "rb") as wf:
                        if rate is None:
                            rate = wf.getframerate()
                        if prev_end is not None:
                            gap = (parse_utc(ev["start"]) - prev_end).total_seconds()
                            if gap > 0:
                                pass_frames.append(b"\x00\x00" * int(gap * rate))
                        pass_frames.append(wf.readframes(wf.getnframes()))
                        prev_end = parse_utc(ev["end"])
                pass_name = ""
                if pass_frames and rate:
                    stamp = aos.replace(":", "").replace("-", "").replace(".", "").rstrip("Z")
                    pass_name = f"iss_{stamp}Z_pass.wav"
                    with wave.open(os.path.join(args.clips_dir, pass_name), "wb") as wf:
                        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
                        wf.writeframes(b"".join(pass_frames))
                w.writerow([aos, pass_start, pass_end, f"{dur:.1f}",
                            str(n_events), str(n_hits), f"{peak:.1f}",
                            peak_ev["peak_level"], peak_ev["floor"],
                            rows[0].get("frequency", ""), pass_name])
        print(f"wrote {len(pass_by_aos)} pass(es) to {args.passes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
