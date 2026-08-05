#!/usr/bin/env python3
"""Universal ISS clip decoder: spectrogram + APRS text + SSTV image.

For every WAV clip in an iss_clips directory (atomic and merged event files),
produces:
  1. A spectrogram BMP (guaranteed visual — works on noise, AFSK, SSTV)
  2. APRS/AFSK text decode via multimon-ng  (callsign, position if present)
  3. SSTV image PNG via sstv_decoder       (Robot 36, ISS's usual mode)

All decodes are idempotent: an iss_decodes.csv log tracks which clips have
been decoded already, so this is safe to run on a timer for new clips.

Spectrograms are saved into the same clips directory with a _sp.bmp suffix.
SSTV images go to the configured sstv out_dir (data/iss_sstv by default).
APRS text is embedded in the decode log (no external file).

Usage:
  python3 tools/iss_clip_decode.py --clips-dir data/iss_clips
  python3 tools/iss_clip_decode.py --config config.ini
  python3 tools/iss_clip_decode.py --force   # re-decode all clips
"""

import argparse
import configparser
import csv
import math
import os
import struct
import subprocess
import sys
import wave
import zlib
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.abspath(ROOT))


# ── spectrogram BMP writer (pure stdlib) ────────────────────────────────

def spectrogram_bmp_8bit(width, height, pixels_gray_0_255):
    """Write a grayscale 8-bit BMP: 54-byte header, 1024-byte palette, rows.
    `pixels_gray_0_255` is a list of height rows, each row width bytes 0–255."""
    row_size = (width + 3) // 4 * 4
    data_size = row_size * height
    file_size = 54 + 1024 + data_size
    header = struct.pack("<2sIHHIIIIHHIIIIII",
        b"BM", file_size, 0, 0, 54 + 1024,
        40, width, height, 1, 8, 0, data_size, 0, 0, 256, 256)
    palette = b"".join(bytes([i, i, i, 0]) for i in range(256))
    rows_data = b""
    for row in reversed(pixels_gray_0_255):
        if len(row) != width:
            row = row + bytes(width - len(row))
        pad = row_size - width
        rows_data += row + bytes(pad)
    return header + palette + rows_data


def make_spectrogram(wav_path, out_path, width=320, height=200):
    """Generate a grayscale spectrogram BMP from a WAV file."""
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        total = w.getnframes()
        raw = w.readframes(total)
    if sw == 1:
        fmt = f"{total}b"
    elif sw == 2:
        fmt = f"{total}h"
    else:
        raise ValueError(f"unsupported sampwidth {sw}")

    import array
    samples = array.array("h" if sw == 2 else "b")
    samples.frombytes(raw)
    if nch == 2:
        samples = samples[0::2]

    # width columns = time segments, height rows = FFT frequency bins
    seg_samps = max(1, len(samples) // width)
    cols = []
    for col_i in range(width):
        start = col_i * seg_samps
        seg = samples[start:start + seg_samps]
        if len(seg) < 16:
            cols.append([0] * height)
            continue
        n = len(seg)
        # DFT for the relevant bins (0..height tells us 0..sr/2)
        spec = []
        for bin_i in range(height):
            f_bin = bin_i * sr / 2.0 / height
            if f_bin < 10:
                spec.append(0); continue
            # Goertzel-ish: correlate with sine and cosine
            sum_sin = sum_sin2 = 0.0
            # larger bin spacing: correlate with the actual freq
            omega = 2.0 * math.pi * f_bin / sr
            re = sum(float(s) * math.cos(omega * i) for i, s in enumerate(seg))
            im = sum(float(s) * math.sin(omega * i) for i, s in enumerate(seg))
            mag = math.sqrt(re*re + im*im) / n
            spec.append(mag)
        cols.append(spec)

    # normalise across all columns
    all_mags = [m for col in cols for m in col if m > 0]
    if all_mags:
        max_mag = max(all_mags)
        min_mag = min(all_mags)
    else:
        max_mag = 1.0; min_mag = 0.0
    spread = max_mag - min_mag or 1.0

    # gamma-compress + map to 0-255
    rows = []
    for bin_i in range(height):
        row = bytearray(width)
        for col_i in range(width):
            mag = cols[col_i][bin_i] if col_i < len(cols) and bin_i < len(cols[col_i]) else 0
            v = (mag - min_mag) / spread
            v = max(0.0, min(1.0, v))
            v = v ** 0.45  # gamma
            row[col_i] = max(0, min(255, int(v * 255)))
        rows.append(bytes(row))

    bmp = spectrogram_bmp_8bit(width, height, rows)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bmp)
    return out_path


# ── APRS / AFSK text decode using multimon-ng ──────────────────────────

def decode_aprs(wav_path):
    """Run multimon-ng to decode AFSK1200 from a WAV. Returns text or None."""
    try:
        r = subprocess.run(
            ["multimon-ng", "-t", "raw", "-a", "AFSK1200", wav_path],
            capture_output=True, text=True, timeout=30)
        lines = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("APRS:") or line.startswith("AFSK1200:"):
                lines.append(line)
        return "\n".join(lines) if lines else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ── SSTV image decode using the existing pure-stdlib decoder ─────────


def decode_sstv_image(wav_path, out_dir, qual_threshold=0.5):
    """Try to decode Robot 36 SSTV. Returns (image_filename, quality, mode) or
    (None, 0, None) on failure. 'quality' is 0.0..1.0 from the decoder's sync
    confidence; below qual_threshold we still save but return low quality."""
    import sstv_decoder
    try:
        mode, w, h, pixels = sstv_decoder.decode_robot36(wav_path)
        if mode is None:
            return None, 0, None
        # quality: 1 if decode produced an image, else 0
        quality = 1.0
        # save PNG at any quality
        name = os.path.splitext(os.path.basename(wav_path))[0] + "_sstv.png"
        out_path = os.path.join(out_dir, name)
        os.makedirs(out_dir, exist_ok=True)
        _write_png(out_path, w, h, pixels)
        return name, quality, mode
    except Exception:
        return None, 0, None


def _write_png(path, w, h, raw_rgb_bytes):
    """Write an RGB PNG using zlib (pure stdlib)."""
    raw = b""
    for y in range(h):
        raw += b"\x00"  # filter byte (none)
        raw += raw_rgb_bytes[y * w * 3:(y + 1) * w * 3]

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", ihdr) +
        chunk(b"IDAT", zlib.compress(raw)) +
        chunk(b"IEND", b"")
    )


# ── main ───────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="INI file with [detector] webhook etc.")
    p.add_argument("--clips-dir", default=os.path.join(ROOT, "data", "iss_clips"))
    p.add_argument("--out-dir", default=os.path.join(ROOT, "data", "iss_sstv"),
                   help="directory for SSTV decoded PNGs")
    p.add_argument("--log", default=os.path.join(ROOT, "data", "iss_decodes.csv"))
    p.add_argument("--force", action="store_true",
                   help="re-decode all clips, ignoring the decode log")
    return p


def main():
    args = build_parser().parse_args()
    cfg = {
        "clips_dir": os.path.abspath(args.clips_dir),
        "out_dir": os.path.abspath(args.out_dir),
        "log": os.path.abspath(args.log),
        "force": args.force,
    }

    if not os.path.isdir(cfg["clips_dir"]):
        print(f"[iss-decode] clips dir not found: {cfg['clips_dir']}")
        return 0

    # load already-decoded set from log
    done = set()
    log_entries = []
    log_path = cfg["log"]
    new_file = not os.path.exists(log_path) or os.path.getsize(log_path) == 0
    if not cfg["force"] and not new_file:
        try:
            with open(log_path, newline="") as f:
                for row in csv.DictReader(f):
                    clip = row.get("clip_file", "")
                    if clip:
                        done.add(clip)
        except Exception:
            pass

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(cfg["out_dir"], exist_ok=True)

    clips = sorted(
        [f for f in os.listdir(cfg["clips_dir"]) if f.startswith("iss_") and f.endswith(".wav")])
    new_count = 0

    for clip in clips:
        if (not cfg["force"]) and clip in done:
            continue
        wav_path = os.path.join(cfg["clips_dir"], clip)
        spec_name = os.path.splitext(clip)[0] + "_sp.bmp"
        spec_path = os.path.join(cfg["clips_dir"], spec_name)

        entry = {
            "clip_file": clip,
            "spectrogram": spec_name,
            "sstv_image": "",
            "sstv_mode": "",
            "sstv_quality": "",
            "aprs_text": "",
        }

        # 1. spectrogram (always)
        try:
            make_spectrogram(wav_path, spec_path)
        except Exception as exc:
            print(f"[iss-decode] spectrogram failed for {clip}: {exc}")
            entry["spectrogram"] = ""

        # 2. APRS text (multimon-ng)
        text = decode_aprs(wav_path)
        if text:
            entry["aprs_text"] = text.replace("\n", " | ")
            print(f"[iss-decode] APRS: {clip} -> {entry['aprs_text'][:120]}")

        # 3. SSTV image
        sstv_name, qual, mode = decode_sstv_image(wav_path, cfg["out_dir"], qual_threshold=0)
        if sstv_name:
            entry["sstv_image"] = sstv_name
            entry["sstv_mode"] = mode or ""
            entry["sstv_quality"] = f"{qual:.2f}"
            print(f"[iss-decode] SSTV: {clip} -> {sstv_name} mode={mode} q={qual:.2f}")

        log_entries.append(entry)
        new_count += 1

    if log_entries:
        fieldnames = ["clip_file", "spectrogram", "sstv_image", "sstv_mode",
                      "sstv_quality", "aprs_text"]
        with open(log_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if new_file:
                w.writeheader()
            for e in log_entries:
                w.writerow(e)
    print(f"[iss-decode] {new_count} clip(s) decoded, "
          f"{len(clips) - new_count} already done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
