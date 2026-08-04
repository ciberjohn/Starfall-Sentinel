#!/usr/bin/env python3
"""ISS SSTV decoder: turns recorded ISS audio clips into images.

Scans iss_recorder.py's WAV clips (data/iss_clips), decodes any clip not
yet decoded with the bundled pure-stdlib Robot 36 decoder (sstv_decoder.py
- no pip packages needed), saves PNGs to data/iss_sstv/, logs each decode
to data/iss_sstv.csv, and optionally posts new images to the Discord
webhook.

Idempotent: already-decoded clips are tracked in iss_sstv.csv, so this is
safe to run on a timer (iss-sstv.timer).

Usage:
  python3 iss_sstv_decode.py --config config.ini
  python3 iss_sstv_decode.py --clips-dir data/iss_clips --out-dir data/iss_sstv
"""

import argparse
import configparser
import csv
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sstv_decoder  # noqa: E402 - needs HERE on sys.path first


def load_config(argv):
    cfg = {
        "clips_dir": os.path.join(HERE, "data", "iss_clips"),
        "out_dir": os.path.join(HERE, "data", "iss_sstv"),
        "log": os.path.join(HERE, "data", "iss_sstv.csv"),
        "webhook": None,
        "post": True,
    }
    args = vars(build_parser().parse_args(argv))
    ini_path = args.get("config")
    if ini_path and os.path.exists(ini_path):
        cp = configparser.ConfigParser()
        cp.read(ini_path)
        if cp.has_section("detector"):
            if "webhook" in cp["detector"]:
                cfg["webhook"] = cp["detector"]["webhook"].strip()
        if cp.has_section("sstv"):
            for k, v in cp["sstv"].items():
                if k in cfg:
                    cfg[k] = v
    for k, v in args.items():
        if k == "config":
            continue
        if v is not None:
            cfg[k] = v
    return cfg


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="INI file ([detector] webhook, [sstv] section)")
    p.add_argument("--clips-dir", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--log", default=None, help="decode manifest CSV")
    p.add_argument("--webhook", default=None, help="Discord webhook for image posts")
    p.add_argument("--no-post", action="store_true", help="decode only, no Discord post")
    return p


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_with_sstv(wav_path, out_path):
    """Decode one WAV to PNG with the bundled stdlib Robot 36 decoder."""
    mode, w, h, rgb = sstv_decoder.decode_robot36(wav_path)
    sstv_decoder.write_png(out_path, w, h, rgb)
    return mode


def post_to_discord(webhook, image_path, caption):
    """Discord webhook file upload (multipart) via curl subprocess."""
    payload = json.dumps({"content": caption})
    subprocess.run(["curl", "-s", "-m", "20", "-X", "POST", webhook,
                    "-F", f"content={caption}",
                    "-F", f"file=@{image_path}"],
                   capture_output=True)


def main():
    cfg = load_config(sys.argv[1:])
    os.makedirs(cfg["out_dir"], exist_ok=True)

    decoded = set()
    if os.path.exists(cfg["log"]):
        with open(cfg["log"], newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 4 and row[0].startswith("20"):
                    decoded.add(row[3])  # image_file

    clips = sorted(f for f in os.listdir(cfg["clips_dir"]) if f.endswith(".wav"))
    new_count = 0
    for clip in clips:
        img_name = clip[:-4] + "_sstv.png"
        if img_name in decoded:
            continue
        wav_path = os.path.join(cfg["clips_dir"], clip)
        out_path = os.path.join(cfg["out_dir"], img_name)
        print(f"[iss-sstv] decoding {clip} ...", flush=True)
        try:
            mode = decode_with_sstv(wav_path, out_path)
        except Exception as exc:
            print(f"[iss-sstv] WARN: {exc}", file=sys.stderr)
            # mark failures as attempted so we don't retry forever? No - leave
            # them for a later run (e.g. package was just installed).
            continue
        with open(cfg["log"], "a", newline="") as f:
            csv.writer(f).writerow([utc_now(), clip, mode, img_name])
        new_count += 1
        print(f"[iss-sstv] saved {img_name}", flush=True)
        if cfg["webhook"] and cfg["post"]:
            post_to_discord(cfg["webhook"], out_path,
                            f"🛰 **ISS SSTV decode** — {clip} → {img_name}")
    print(f"[iss-sstv] done - {new_count} new decode(s), {len(clips)} clip(s) scanned")


if __name__ == "__main__":
    main()
