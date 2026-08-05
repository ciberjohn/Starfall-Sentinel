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

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HITS = os.path.join(HERE, "..", "data", "iss_hits.csv")
DEFAULT_EVENTS = os.path.join(HERE, "..", "data", "iss_events.csv")


def parse_utc(ts):
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hits", default=os.path.abspath(DEFAULT_HITS))
    ap.add_argument("--events", default=os.path.abspath(DEFAULT_EVENTS))
    ap.add_argument("--gap-s", type=float, default=5.0,
                    help="cluster gap in seconds (default 5.0, must match iss_recorder event_gap_s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the events that WOULD be written, don't write")
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
    new_file = not os.path.exists(args.events) or os.path.getsize(args.events) == 0
    with open(args.events, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["utc_start", "utc_end", "pass_aos_utc", "duration_s",
                        "n_hits", "peak_db_over_floor", "peak_level_db",
                        "floor_db", "frequency", "clip_files", "note"])
        for ev in events:
            n = ev["n_hits"]
            note = f"merged {n} hits (ISS burst)" if n > 1 else ""
            dur = (parse_utc(ev["end"]) - parse_utc(ev["start"])).total_seconds()
            w.writerow([ev["start"], ev["end"], rows[0].get("pass_aos_utc", ""),
                        f"{dur:.1f}", str(n), f"{ev['peak_above']:.1f}",
                        f"{ev['peak_level']:.1f}", f"{ev['floor']:.1f}",
                        rows[0].get("frequency", ""), ",".join(ev["clips"]), note])
    print(f"wrote {len(events)} event(s) to {args.events}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
