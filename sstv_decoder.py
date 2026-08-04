#!/usr/bin/env python3
"""Robot 36 SSTV decoder - pure Python stdlib, no dependencies.

Decodes a 16-bit mono WAV of a Robot 36 (VIS 0x08) Slow-Scan Television
transmission - the mode ISS astronauts use most often for SSTV events -
into a 320x240 RGB PNG.

Design:
  - Frequency tracking: zero-crossing count over short windows (~2 ms).
  - Segment detection is ADAPTIVE: sync pulses (1200 Hz runs) mark line
    boundaries; the luminance and colour segments are located by their
    markers (1500 Hz sync-porch / 1900 Hz porch) and sampled across their
    ACTUAL measured durations. This decodes both textbook 30 ms/line
    signals and the slower timings some encoders use.
  - PNG output is written with stdlib zlib/struct - no Pillow required.

Usage:
  python3 sstv_decoder.py clip.wav out.png
  (also importable: decode_robot36(wav_path) -> (mode, width, height, bytes))
"""

import array
import math
import os
import struct
import sys
import wave
import zlib

# --- Robot 36 constants (per pysstv, the de-facto encoder reference) -------
WIDTH, HEIGHT = 320, 240
FREQ_SYNC, FREQ_BLACK, FREQ_WHITE, FREQ_PORCH = 1200, 1500, 2300, 1900
FREQ_RANGE = FREQ_WHITE - FREQ_BLACK

WINDOW = 96            # frequency-estimation window (~2 ms at 48 kHz); below
                       # ~2 sine cycles the estimator stops seeing syncs
MIN_SYNC_MS = 3.0      # a 1200 Hz run this long counts as a line sync
MIN_GAP_MS = 1.5       # a 1500/2300 Hz run this long closes the Y segment


def _freq_estimate(block, rate):
    """Frequency estimate of a sample block (Hz) via interpolated
    zero-crossing periods - sub-Hz resolution on clean tones."""
    zc = []
    prev = block[0]
    for i in range(1, len(block)):
        s = block[i]
        if (prev < 0 <= s) or (prev >= 0 > s):
            denom = s - prev
            t = i - prev / denom if denom != 0 else i
            zc.append(t)
        prev = s
    if len(zc) < 2:
        return 0.0
    intervals = [zc[i + 1] - zc[i] for i in range(len(zc) - 1)]
    half_period = sum(intervals) / len(intervals)
    if half_period <= 0:
        return 0.0
    return rate / (2.0 * half_period)


def _classify(freq):
    """Nearest tone class: 'sync','black','white','porch','vis1','vis0','none'."""
    best, best_d = None, 1e9
    for name, f in (("sync", FREQ_SYNC), ("black", FREQ_BLACK),
                    ("white", FREQ_WHITE), ("porch", FREQ_PORCH),
                    ("vis1", 1100), ("vis0", 1300)):
        d = abs(freq - f)
        if d < best_d:
            best, best_d = name, d
    return best if best_d < 140 else "none"


def _windows(samples, rate):
    step = WINDOW
    for i in range(0, len(samples) - step, step):
        yield i, _classify(_freq_estimate(samples[i:i + step], rate)), \
            _freq_estimate(samples[i:i + step], rate)


def _find_vis(samples, rate):
    """Return True if a Robot 36 VIS (0x08) header is found before image data."""
    # crude but effective: look for the 1900 Hz start run then 1200 sync then
    # parse the 30 ms bit cells after the second start.
    wins = list(_windows(samples, rate))
    step = WINDOW
    for i in range(len(wins) - 40):
        # 300 ms start ~= 150 windows at 2 ms
        if wins[i][1] != "porch":
            continue
        run = 0
        while i + run < len(wins) and wins[i + run][1] == "porch":
            run += 1
        if run < 60:
            continue
        # after start: 1200 sync (10 ms) then another 300 ms start
        j = i + run
        if j >= len(wins) or wins[j][1] != "sync":
            continue
        k = j + 1
        while k < len(wins) and wins[k][1] != "porch":
            k += 1
        if k >= len(wins):
            continue
        run2 = 0
        while k + run2 < len(wins) and wins[k + run2][1] == "porch":
            run2 += 1
        if run2 < 60:
            continue
        # parse 10 bit cells (30 ms each): start, 7 VIS bits LSB-first,
        # parity, stop - consume ALL so image data starts at the stop edge
        p = k + run2
        bits = []
        for _ in range(10):
            cell = wins[p:p + 15]
            if len(cell) < 8:
                return False
            ones = sum(1 for w in cell if w[1] == "vis1")
            zeros = sum(1 for w in cell if w[1] == "vis0")
            bits.append(1 if ones > zeros else 0)
            p += 15
        vis = 0
        for b in range(7):
            vis |= bits[1 + b] << b  # bits[0] is start bit
        return vis == 0x08, p * step
    return False, 0


def decode_robot36(wav_path):
    """Decode a Robot 36 WAV to (mode, width, height, RGB bytes)."""
    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        nch = w.getnchannels()
        sampwidth = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if sampwidth != 2:
        raise ValueError("expected 16-bit PCM")
    raw = array.array("h")
    raw.frombytes(frames)
    if nch == 2:
        raw = raw[0::2]  # keep left channel
    samples = raw.tolist()

    vis, vis_end = _find_vis(samples, rate)
    # Even if VIS parse failed, attempt Robot 36 - ISS events are almost
    # always Robot 36 and sync-based parsing tolerates a missed VIS header.
    start = vis_end if vis else 0

    # Robot 36 line timing (pysstv-compatible, 36 s format):
    # sync 9ms + sync-porch 3ms + Y 88ms + gap 4.5ms + porch 1.5ms + colour 44ms
    MS_SYNC, MS_SYNC_PORCH, MS_Y, MS_GAP, MS_PORCH, MS_C = 9, 3, 88, 4.5, 1.5, 44
    LINE_MS = MS_SYNC + MS_SYNC_PORCH + MS_Y + MS_GAP + MS_PORCH + MS_C  # 150 ms
    win_ms = WINDOW / rate * 1000.0

    y_img = [[0] * WIDTH for _ in range(HEIGHT)]
    r_img = [[0] * WIDTH for _ in range(HEIGHT)]
    b_img = [[0] * WIDTH for _ in range(HEIGHT)]
    lines_done = 0

    def freq_to_val(f):
        v = (f - FREQ_BLACK) / FREQ_RANGE * 255.0
        return max(0, min(255, int(round(v))))

    def sample_range(s0, s1, count):
        """Frequency across samples [s0,s1) sampled into `count` pixel values.

        Robot 36 pixels are ~0.27 ms (13 samples at 48 kHz) - far too short
        for a per-pixel zero-crossing estimate - so build a dense sliding
        frequency series (96-sample window, 4-sample step) across the whole
        segment and average the series within each pixel's span.
        """
        if s1 <= s0:
            return []
        step = 4
        freqs = []
        w_ = s0
        while w_ + WINDOW <= s1:
            freqs.append(_freq_estimate(samples[w_:w_ + WINDOW], rate))
            w_ += step
        if not freqs:
            return []
        span = s1 - s0
        out = []
        for k in range(count):
            lo = s0 + span * k // count
            hi = s0 + span * (k + 1) // count
            i0 = max(0, (lo - s0) // step)
            i1 = min(len(freqs), (hi - s0) // step + 1)
            acc, cnt = 0.0, 0
            for i in range(i0, i1):
                acc += freqs[i]
                cnt += 1
            out.append(freq_to_val(acc / max(1, cnt)))
        return out

    def find_next_sync(from_sample, max_ahead_ms=LINE_MS + 40):
        """First 1200 Hz run of >= MIN_SYNC_MS starting at/after from_sample."""
        end = min(len(samples), from_sample + int(max_ahead_ms / 1000 * rate))
        i = from_sample
        while i + WINDOW <= end:
            f = _freq_estimate(samples[i:i + WINDOW], rate)
            if abs(f - FREQ_SYNC) < 90:
                run = 0
                j = i
                while j + WINDOW <= end:
                    f2 = _freq_estimate(samples[j:j + WINDOW], rate)
                    if abs(f2 - FREQ_SYNC) < 90:
                        run += 1
                        j += WINDOW
                    else:
                        break
                if run * win_ms >= MIN_SYNC_MS:
                    return i
                i = j
            else:
                i += WINDOW
        return None

    sync_pos = find_next_sync(start)
    while sync_pos is not None and lines_done < HEIGHT:
        y_start = sync_pos + int((MS_SYNC + MS_SYNC_PORCH) / 1000 * rate)
        y_end = y_start + int(MS_Y / 1000 * rate)
        c_start = y_end + int((MS_GAP + MS_PORCH) / 1000 * rate)
        c_end = c_start + int(MS_C / 1000 * rate)
        if c_start >= len(samples):
            break  # nothing left to sample; slices below clamp at EOF safely
        y_vals = sample_range(y_start, y_end, WIDTH)
        c_vals = sample_range(c_start, c_end, WIDTH)
        if len(y_vals) == WIDTH:
            line = lines_done
            y_img[line] = y_vals
            if line % 2 == 0:
                r_img[line] = c_vals
            else:
                b_img[line] = c_vals
            lines_done += 1
        sync_pos = find_next_sync(
            sync_pos + int((LINE_MS - 5) / 1000 * rate))

    if lines_done < 10:
        raise ValueError(
            f"could not find Robot 36 lines (found {lines_done}) - "
            "is this an SSTV recording?")

    # Robot 36 sends Y + ONE colour per line (Cr on even lines, Cb on odd).
    # Assemble RGB: interpolate each line's missing channel from the nearest
    # same-parity neighbours (two-pass so line 0 doesn't inherit junk).
    def interp_chan(chan_img, parity):
        out = [[0] * WIDTH for _ in range(HEIGHT)]
        for y in range(HEIGHT):
            if y % 2 == parity:
                out[y] = chan_img[y]
                continue
            above = chan_img[y - 1] if y - 1 >= 0 else None
            below = chan_img[y + 1] if y + 1 < HEIGHT else None
            for x in range(WIDTH):
                if above is not None and below is not None:
                    out[y][x] = (above[x] + below[x]) // 2
                elif above is not None:
                    out[y][x] = above[x]
                elif below is not None:
                    out[y][x] = below[x]
        return out

    r_full = interp_chan(r_img, 0)   # Cr on even lines
    b_full = interp_chan(b_img, 1)   # Cb on odd lines

    rgb = bytearray(WIDTH * HEIGHT * 3)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            Y = y_img[y][x]
            Cr, Cb = r_full[y][x] - 128, b_full[y][x] - 128
            r = int(Y + 1.402 * Cr)
            g = int(Y - 0.344136 * Cb - 0.714136 * Cr)
            b = int(Y + 1.772 * Cb)
            idx = (y * WIDTH + x) * 3
            rgb[idx] = max(0, min(255, r))
            rgb[idx + 1] = max(0, min(255, g))
            rgb[idx + 2] = max(0, min(255, b))
    return "Robot36", WIDTH, HEIGHT, bytes(rgb)


def write_png(path, width, height, rgb_bytes):
    """Minimal stdlib PNG writer (8-bit RGB, no interlace)."""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + \
            struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgb_bytes[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    wav_path, out_path = sys.argv[1], sys.argv[2]
    mode, w, h, rgb = decode_robot36(wav_path)
    write_png(out_path, w, h, rgb)
    print(f"[sstv-decoder] {mode} {w}x{h} -> {out_path}")


if __name__ == "__main__":
    main()
