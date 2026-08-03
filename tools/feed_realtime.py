#!/usr/bin/env python3
"""Feed a raw 16-bit LE PCM file to stdout at real-time rate.

Used to test detector.py's 1 Hz live-sample stream with recorded or
simulated audio, emulating the rate at which rtl_fm produces audio.

Usage:
  python3 tools/feed_realtime.py audio.pcm \
    | python3 detector.py --source stdin --live-out data/live.csv --log data/pings.csv
"""

import argparse
import sys
import time


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcm", help="raw 16-bit LE mono PCM file")
    ap.add_argument("--rate", type=int, default=48000,
                    help="sample rate of the PCM (bytes/s = 2*rate)")
    ap.add_argument("--chunk-ms", type=float, default=200.0,
                    help="chunk size in ms")
    args = ap.parse_args()

    chunk = max(1, int(args.rate * 2 * args.chunk_ms / 1000.0))
    delay = args.chunk_ms / 1000.0
    out = sys.stdout.buffer
    with open(args.pcm, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            out.write(b)
            out.flush()
            time.sleep(delay)
    print(f"[feed_realtime] done: {args.pcm}", file=sys.stderr)


if __name__ == "__main__":
    main()
