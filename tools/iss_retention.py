#!/usr/bin/env python3
"""ISS clip retention: tiered cleanup + lossy transcode of old pass clips.

Prevents the iss_clips directory growing without bound. The tiers:

  atomic clips (raw per-burst WAV)       -> keep  keep_atomic_days  (default 7)
  event merged clips (per transmission)  -> keep  keep_event_days   (default 30)
  pass clips (full-pass WAV)             -> keep  keep_pass_days    (default 90)
  pass clips older than that             -> transcode to MP3 32k mono, delete WAV
  spectrogram BMPs                       -> keep  keep_spec_days    (default 180)
  SSTV decoded PNGs                      -> kept forever (the product)

Transcoding uses ffmpeg (already installed on the station). MP3 at 32 kbps
mono preserves the AFSK 1200/2200 Hz tones fine and is ~25x smaller than
48 kHz PCM WAV (12.3 MB pass -> ~0.5 MB). MP3 is universally playable in
browsers (dashboard <audio> tags).

Idempotent: only touches files matching iss_* in the clips dir; never
touches anything it didn't create. Safe on a timer.

Usage:
  python3 tools/iss_retention.py --clips-dir data/iss_clips [--dry-run]
  python3 tools/iss_retention.py --config config.ini
"""

import argparse
import configparser
import datetime
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

# filename -> tier classifier
_ATOMIC = re.compile(r"^iss_\d{8}T\d{6}\d{3}Z_\d{3}\.wav$")
_EVENT = re.compile(r"^iss_\d{8}T\d{6}\d{3}Z_ev\.wav$")
_PASS = re.compile(r"^iss_\d{8}T\d{6}Z_pass\.wav$")
_SPEC = re.compile(r"^iss_.*_sp\.bmp$")


def tier_of(fname):
    if _ATOMIC.match(fname):
        return "atomic"
    if _EVENT.match(fname):
        return "event"
    if _PASS.match(fname):
        return "pass"
    if _SPEC.match(fname):
        return "spec"
    return None


def mtime(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path))


def run_retention(clips_dir, cfg, dry_run=False):
    """Return (deleted, transcoded, kept) counts."""
    deleted = []
    transcoded = []
    kept = []
    if not os.path.isdir(clips_dir):
        return deleted, transcoded, kept

    now = datetime.datetime.now()
    for fname in sorted(os.listdir(clips_dir)):
        if not fname.startswith("iss_"):
            continue
        tier = tier_of(fname)
        if tier is None:
            continue
        path = os.path.join(clips_dir, fname)
        age_days = (now - mtime(path)).total_seconds() / 86400.0

        if tier == "atomic":
            if age_days > cfg["keep_atomic_days"]:
                deleted.append(fname)
            else:
                kept.append(fname)
        elif tier == "event":
            if age_days > cfg["keep_event_days"]:
                deleted.append(fname)
            else:
                kept.append(fname)
        elif tier == "pass":
            if age_days > cfg["keep_pass_days"]:
                mp3 = os.path.splitext(fname)[0] + ".mp3"
                mp3_path = os.path.join(clips_dir, mp3)
                if not os.path.exists(mp3_path):
                    transcoded.append(fname)
                    if not dry_run:
                        _transcode(path, mp3_path)
                # keep the MP3 (forever), drop the WAV
                if not dry_run:
                    os.remove(path)
                deleted.append(fname)  # WAV removed (MP3 is the keeper)
            else:
                kept.append(fname)
        elif tier == "spec":
            if age_days > cfg["keep_spec_days"]:
                deleted.append(fname)
            else:
                kept.append(fname)

    if not dry_run:
        for fname in deleted:
            if fname.endswith(".wav") and tier_of(fname) == "pass":
                continue  # pass WAVs already removed above
            p = os.path.join(clips_dir, fname)
            if os.path.exists(p):
                os.remove(p)
        for fname in transcoded:
            p = os.path.join(clips_dir, os.path.splitext(fname)[0] + ".mp3")
            if os.path.exists(p):
                print(f"[retention] transcoded -> {os.path.basename(p)}")

    return deleted, transcoded, kept


def _transcode(wav_path, mp3_path):
    """ffmpeg: mono MP3 32 kbps, sample rate 22050 (plenty for AFSK tones)."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-ac", "1", "-ar", "22050",
         "-b:a", "32k", mp3_path],
        capture_output=True, check=True)


def load_cfg(args):
    cfg = {"keep_atomic_days": 7, "keep_event_days": 30, "keep_pass_days": 90,
           "keep_spec_days": 180, "clips_dir": os.path.join(ROOT, "data", "iss_clips")}
    if args.config and os.path.exists(args.config):
        cp = configparser.ConfigParser()
        cp.read(args.config)
        if cp.has_section("retention"):
            for k, v in cp["retention"].items():
                if k in ("keep_atomic_days", "keep_event_days", "keep_pass_days",
                         "keep_spec_days"):
                    cfg[k] = float(v)
        if cp.has_section("iss") and "clips_dir" in cp["iss"]:
            cfg["clips_dir"] = cp["iss"]["clips_dir"]
    if args.clips_dir:
        cfg["clips_dir"] = args.clips_dir
    return cfg


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="INI file ([retention] section)")
    p.add_argument("--clips-dir", default=None)
    p.add_argument("--keep-atomic-days", type=float, default=None)
    p.add_argument("--keep-event-days", type=float, default=None)
    p.add_argument("--keep-pass-days", type=float, default=None)
    p.add_argument("--keep-spec-days", type=float, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="report what WOULD be removed/transcoded, change nothing")
    return p


def main():
    args = build_parser().parse_args()
    cfg = load_cfg(args)
    for k in ("keep_atomic_days", "keep_event_days", "keep_pass_days", "keep_spec_days"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v

    deleted, transcoded, kept = run_retention(cfg["clips_dir"], cfg, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "done"
    print(f"[retention] {mode}: {len(kept)} kept, {len(deleted)} removed, "
          f"{len(transcoded)} transcoded to MP3")
    if args.dry_run:
        for f in deleted[:20]:
            print(f"  would remove: {f}")
        for f in transcoded[:10]:
            print(f"  would transcode: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
