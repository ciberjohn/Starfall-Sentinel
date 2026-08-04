#!/usr/bin/env python3
"""Ping-curve PNG renderer - pure stdlib, no dependencies.

Reads a curve CSV (t_ms,db,floor) written by detector.py and renders a
dark-themed strip-chart PNG (signal line + dashed noise floor) for Discord
webhook attachments. PNG encoding reuses sstv_decoder.write_png (stdlib
zlib/struct). No text is drawn (no font in stdlib) - the webhook message
text carries the stats.

Usage:
  python3 curve_plot.py curve.csv [out.png]
  (importable: plot_curve(csv_path, title=..., out_path=None) -> png path)
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sstv_decoder import write_png  # noqa: E402

BG = (14, 14, 14)
GRID = (40, 40, 40)
SIGNAL = (57, 135, 229)     # #3987e5
FLOOR = (150, 150, 150)
FRAME = (80, 80, 80)
MARGIN = 28


def parse_curve(path):
    ts, dbs, floors = [], [], []
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) < 3 or not row[0].lstrip("-").isdigit():
                continue
            ts.append(float(row[0]))
            dbs.append(float(row[1]))
            floors.append(float(row[2]))
    return ts, dbs, floors


def smooth_vals(ts, vals):
    """Display-only moving average (peak-preserving) so echo envelopes read
    smoothly. The window scales with the median sample interval so a 100 Hz
    curve gets the same ~250 ms smoothing as a legacy 20 Hz one. Stats are
    never computed from this."""
    if len(vals) < 3:
        return vals
    dt = 50.0
    if len(ts) >= 2:
        dts = sorted(ts[i + 1] - ts[i] for i in range(len(ts) - 1))
        dt = dts[len(dts) // 2] or 50.0
    w = max(3, int(round(5 * 50.0 / dt)))
    out = vals[:]
    win = w // 2
    for i in range(len(vals)):
        lo, hi = max(0, i - win), min(len(vals) - 1, i + win)
        out[i] = sum(vals[lo:hi + 1]) / (hi - lo + 1)
    pk = max(vals)
    out[vals.index(pk)] = max(out[vals.index(pk)], pk)
    return out


def spline_samples(xs, ys, steps=8):
    """Catmull-Rom spline, densely sampled (for smooth curve rendering)."""
    out = []
    n = len(xs)
    if n < 2:
        return list(zip(xs, ys))
    if n < 4:
        return list(zip(xs, ys))
    for i in range(n - 1):
        x0, y0 = xs[max(i - 1, 0)], ys[max(i - 1, 0)]
        x1, y1 = xs[i], ys[i]
        x2, y2 = xs[i + 1], ys[i + 1]
        x3, y3 = xs[min(i + 2, n - 1)], ys[min(i + 2, n - 1)]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * x1) + (-x0 + x2) * t +
                       (2 * x0 - 5 * x1 + 4 * x2 - x3) * t2 +
                       (-x0 + 3 * x1 - 3 * x2 + x3) * t3)
            y = 0.5 * ((2 * y1) + (-y0 + y2) * t +
                       (2 * y0 - 5 * y1 + 4 * y2 - y3) * t2 +
                       (-y0 + 3 * y1 - 3 * y2 + y3) * t3)
            out.append((x, y))
    out.append((xs[-1], ys[-1]))
    return out


def plot_curve(csv_path, title="", out_path=None, width=640, height=240):
    ts, dbs, floors = parse_curve(csv_path)
    if len(dbs) < 2:
        raise ValueError(f"curve {csv_path} has no data")
    if out_path is None:
        out_path = os.path.splitext(csv_path)[0] + ".png"

    img = bytearray(width * height * 3)

    def px(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            img[i] = color[0]
            img[i + 1] = color[1]
            img[i + 2] = color[2]

    def fill(c):
        for i in range(0, len(img), 3):
            img[i] = c[0]
            img[i + 1] = c[1]
            img[i + 2] = c[2]

    fill(BG)

    # scale
    t0, t1 = ts[0], ts[-1]
    if t1 - t0 < 1:
        t1 = t0 + 1
    lo = min(min(dbs), min(floors)) - 2.0
    hi = max(max(dbs), max(floors)) + 2.0
    if hi - lo < 4:
        hi = lo + 4
    iw, ih = width - 2 * MARGIN, height - 2 * MARGIN

    def tx(t):
        return MARGIN + (t - t0) / (t1 - t0) * iw

    def ty(d):
        return MARGIN + (hi - d) / (hi - lo) * ih

    # grid (4 x-lines, 4 y-lines)
    for k in range(1, 5):
        x = MARGIN + iw * k // 5
        for y in range(MARGIN, MARGIN + ih):
            px(int(x), y, GRID)
        y = MARGIN + ih * k // 5
        for x in range(MARGIN, MARGIN + iw):
            px(x, int(y), GRID)

    # frame
    for x in range(MARGIN, MARGIN + iw):
        px(x, MARGIN, FRAME)
        px(x, MARGIN + ih - 1, FRAME)
    for y in range(MARGIN, MARGIN + ih):
        px(MARGIN, y, FRAME)
        px(MARGIN + iw - 1, y, FRAME)

    # noise floor - dashed (smoothed)
    prev = None
    for (t, d) in spline_samples(ts, floors):
        x, y = tx(t), ty(d)
        if prev is not None:
            x0, y0, x1, y1 = prev[0], prev[1], x, y
            n = max(1, int(abs(x1 - x0)))
            for i in range(1, n + 1):
                if (i // 6) % 2 == 0:
                    xx = x0 + (x1 - x0) * i / n
                    yy = y0 + (y1 - y0) * i / n
                    px(int(xx), int(yy), FLOOR)
        prev = (x, y)

    # signal polyline (Catmull-Rom smoothed so echoes look curved)
    dbs_disp = smooth_vals(ts, dbs)
    prev = None
    for (t, d) in spline_samples(ts, dbs_disp):
        x, y = tx(t), ty(d)
        if prev is not None:
            x0, y0, x1, y1 = prev[0], prev[1], x, y
            n = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
            for i in range(0, n + 1):
                xx = x0 + (x1 - x0) * i / n
                yy = y0 + (y1 - y0) * i / n
                px(int(xx), int(yy), SIGNAL)
        prev = (x, y)

    write_png(out_path, width, height, bytes(img))
    return out_path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    out = plot_curve(sys.argv[1], out_path=sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"[curve-plot] {out}")


if __name__ == "__main__":
    main()
