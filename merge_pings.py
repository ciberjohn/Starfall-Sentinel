#!/usr/bin/env python3
"""Merge pings that belong to one fading echo (threshold-split artifacts).

The detector's hangover fix (end_margin_db + hangover_windows) prevents
future splits. This tool repairs already-logged rows: consecutive pings
whose inter-event signal stayed elevated (>= floor + end_margin_db) are
merged into one event, with a combined curve file. Rows without curves
(e.g. logged before curve capture went live) are merged from the 1 Hz
live.csv reconstruction when --assume-cluster is set.

NOTE: pings.csv start_ms is detector-run-relative and RESETS on detector
restarts, so all gap/duration math uses the UTC timestamp column; start_ms
is kept from the first row of each merged group.

Usage:
  python3 tools/merge_pings.py --pings data/pings.csv --data-dir data
  python3 tools/merge_pings.py --pings data/pings.csv --data-dir data --dry-run
"""
import argparse
import csv
import datetime
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def parse_utc(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_pings(path):
    rows = []
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        for r in rd:
            if len(r) >= 4:
                try:
                    r.append(parse_utc(r[0]))  # row[-1] = utc datetime
                except ValueError:
                    r.append(None)
            rows.append(r)
    return header, rows


def curve_rows(path):
    out = []
    if path and os.path.isfile(path):
        with open(path, newline="") as f:
            for r in csv.reader(f):
                if len(r) >= 3 and r[0].lstrip("-").isdigit():
                    out.append((int(r[0]), float(r[1]), float(r[2])))
    return out


def live_samples(path):
    """All (epoch_ms, db, floor) rows; cached per call."""
    out = []
    if path and os.path.isfile(path):
        with open(path, newline="") as f:
            for line in f:
                p = line.strip().split(",")
                if len(p) >= 3 and p[0].isdigit():
                    out.append((int(p[0]), float(p[1]), float(p[2])))
    return out


def live_between(live, t0_epoch, t1_epoch):
    return [x for x in live if t0_epoch <= x[0] <= t1_epoch]


def row_offset(row, live):
    """Detector-relative-ms offset for this row's run: live epoch closest
    to the row's UTC minus the row's start_ms."""
    if not row[-1]:
        return None
    target = row[-1].timestamp() * 1000.0
    best = min(live, key=lambda x: abs(x[0] - target)) if live else None
    if best is None:
        return None
    return int(best[0]) - int(row[2])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pings", default="data/pings.csv")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--end-margin-db", type=float, default=3.0)
    ap.add_argument("--max-gap-s", type=float, default=5.0,
                    help="never merge events more than this apart (UTC)")
    ap.add_argument("--assume-cluster", action="store_true",
                    help="merge no-curve clusters from live.csv (1 Hz)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    header, rows = parse_pings(args.pings)
    cdir = os.path.join(args.data_dir, "ping_curves")
    live = live_samples(os.path.join(args.data_dir, "live.csv"))
    if not rows:
        print("no pings")
        return

    def end_epoch(r):
        return r[-1].timestamp() * 1000.0 + float(r[3])

    def elevated(lr, floor):
        return [x for x in lr if x[1] >= floor + args.end_margin_db]

    groups = []
    cur = [rows[0]]
    for r in rows[1:]:
        prev = cur[-1]
        gap_s = None
        if prev[-1] and r[-1]:
            gap_s = (r[-1].timestamp() - end_epoch(prev) / 1000.0)
        merge = False
        if gap_s is not None and gap_s <= args.max_gap_s:
            p_has_curve = len(prev) >= 11 and prev[9]
            r_has_curve = len(r) >= 11 and r[9]
            if p_has_curve and r_has_curve:
                a = curve_rows(os.path.join(cdir, prev[9]))
                b = curve_rows(os.path.join(cdir, r[9]))
                allc = sorted(a + b)
                p_end_t = int(prev[2]) + int(float(prev[3]))
                inter = [x for x in allc if p_end_t < x[0] < int(r[2])]
                if inter:
                    floor = sum(x[2] for x in allc) / len(allc)
                    if max(x[1] for x in inter) >= floor + args.end_margin_db:
                        merge = True
            elif args.assume_cluster and prev[-1] and r[-1]:
                # continuity check on 1 Hz live data between the two events
                t0 = end_epoch(prev)
                t1 = r[-1].timestamp() * 1000.0
                lr = live_between(live, int(t0), int(t1))
                if lr:
                    floor = sum(x[2] for x in lr) / len(lr)
                    if elevated(lr, floor):
                        merge = True
        if merge:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    if cur:
        groups.append(cur)

    merged_out = []
    for g in groups:
        if len(g) == 1:
            merged_out.append(g[0][:10])
            continue
        first = g[0]
        start_ms = int(first[2])
        peak = max(float(x[4]) for x in g)
        peak_row = max(g, key=lambda x: float(x[4]))
        end_ms = None
        curve_path = None
        note = f"merged {len(g)} pings (fading echo)"
        if all(len(x) >= 11 and x[9] for x in g):
            allc = []
            for x in g:
                allc.extend(curve_rows(os.path.join(cdir, x[9])))
            allc.sort()
            if allc:
                floor = sum(x[2] for x in allc) / len(allc)
                elev = elevated(allc, floor)
                end_ms = max(elev)[0] if elev else start_ms
                os.makedirs(cdir, exist_ok=True)
                stamp = datetime.datetime.now(
                    datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
                curve_path = f"{stamp}_{start_ms}.csv"
                with open(os.path.join(cdir, curve_path), "w", newline="") as f:
                    cw = csv.writer(f)
                    cw.writerow(["t_ms", "db", "floor"])
                    seen = set()
                    for (t, d, fl) in allc:
                        if t in seen:
                            continue
                        seen.add(t)
                        cw.writerow([t, f"{d:.2f}", f"{fl:.2f}"])
        elif args.assume_cluster and first[-1]:
            off = row_offset(first, live)
            if off is not None:
                t0 = int(first[-1].timestamp() * 1000.0) - 2000
                t1 = int((g[-1][-1].timestamp() + float(g[-1][3]) / 1000.0) * 1000.0) + 5000
                lr = live_between(live, t0, t1)
                if lr:
                    floor = sum(x[2] for x in lr) / len(lr)
                    elev = elevated(lr, floor)
                    end_ms = (max(elev)[0] - off) if elev else (start_ms + int(float(g[-1][3])))
                    os.makedirs(cdir, exist_ok=True)
                    stamp = datetime.datetime.now(
                        datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
                    curve_path = f"{stamp}_{start_ms}.csv"
                    with open(os.path.join(cdir, curve_path), "w", newline="") as f:
                        cw = csv.writer(f)
                        cw.writerow(["t_ms", "db", "floor"])
                        for (t, d, fl) in lr:
                            cw.writerow([t - off, f"{d:.2f}", f"{fl:.2f}"])
                    note += "; reconstructed from live.csv (1 Hz)"
        if end_ms is None:
            end_ms = int(g[-1][2]) + int(float(g[-1][3]))
        dur_ms = max(1, end_ms - start_ms)
        kind = "PING" if dur_ms <= 8000 else "LONG"
        newrow = [first[0], first[1], str(start_ms), f"{dur_ms:.0f}",
                  f"{peak:.1f}", peak_row[5], peak_row[6], kind, note,
                  curve_path or ""]
        merged_out.append(newrow)
        print(f"[merge] {len(g)} pings -> {kind} dur {dur_ms} ms "
              f"peak +{peak:.1f} dB curve {curve_path or '-'}")

    if args.dry_run:
        print(f"\n[dry-run] would merge {len(rows) - len(merged_out)} row(s) "
              f"into {len(groups)} group(s)")
        return

    shutil.copy(args.pings, args.pings + ".bak")
    with open(args.pings, "w", newline="") as f:
        cw = csv.writer(f)
        if header:
            cw.writerow(header)
        for r in merged_out:
            cw.writerow(r)
    print(f"[merge] wrote {args.pings} ({len(merged_out)} rows; "
          f"backup at {args.pings}.bak)")


if __name__ == "__main__":
    main()
