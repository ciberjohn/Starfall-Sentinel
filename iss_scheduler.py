#!/usr/bin/env python3
"""Orchestrator: pauses GRAVES monitoring for each qualifying ISS pass, runs
iss_recorder.py for that window, then resumes GRAVES.

One RTL-SDR dongle means one channel at a time - there is no way to listen
for meteor pings and the ISS simultaneously on this station, so a pass means
graves-watch goes dark for a few minutes. Only passes clearing [iss]
min_elevation trigger this (see config.ini.example) - a high bar by design,
so the station only gives up meteor coverage for passes with a real chance
of a usable catch, not every marginal horizon-skimmer.

Meant to run as its own systemd --user service (graves-iss.service),
independent of graves-watch/graves-dashboard.

Usage:
  python3 iss_scheduler.py --config config.ini
"""

import argparse
import configparser
import datetime
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import satpass  # noqa: E402 - needs HERE on sys.path first

GRAVES_SERVICE = "graves-watch"
MAX_SLEEP_CHUNK_S = 900  # heartbeat cadence during a long wait for the next pass


def load_config(config_path):
    cfg = {
        "lat": 0.0, "lon": 0.0,
        "min_elevation": 40.0,
        "pass_preroll_s": 20.0,
        "pass_postroll_s": 20.0,
        "recheck_interval_s": 1800.0,
        "device_cooldown_s": 2.5,
        "data_dir": os.path.join(HERE, "data"),
    }
    cp = configparser.ConfigParser()
    if config_path and os.path.exists(config_path):
        cp.read(config_path)
        if cp.has_section("station"):
            cfg["lat"] = cp.getfloat("station", "lat", fallback=cfg["lat"])
            cfg["lon"] = cp.getfloat("station", "lon", fallback=cfg["lon"])
        if cp.has_section("iss"):
            for k in ("min_elevation", "pass_preroll_s", "pass_postroll_s",
                      "recheck_interval_s", "device_cooldown_s"):
                if cp.has_option("iss", k):
                    cfg[k] = cp.getfloat("iss", k)
    return cfg


def log(msg):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[iss-scheduler] {ts} UTC  {msg}", flush=True)


def sleep_until(target_dt):
    """time.sleep() in bounded chunks so a heartbeat shows up in the journal
    during multi-hour waits between qualifying passes, instead of one giant
    silent sleep."""
    while True:
        remaining = (target_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        chunk = min(remaining, MAX_SLEEP_CHUNK_S)
        time.sleep(chunk)


def systemctl(action, service):
    r = subprocess.run(["systemctl", "--user", action, service],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"WARN: systemctl {action} {service} failed: {r.stderr.strip()}")
    return r.returncode == 0


def run_one_pass(cfg, config_path, payload):
    aos = datetime.datetime.strptime(payload["aos_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    los = datetime.datetime.strptime(payload["los_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    trigger_at = aos - datetime.timedelta(seconds=cfg["pass_preroll_s"])

    log(f"next qualifying pass: AOS {payload['aos_utc']} el {payload['max_el_deg']:.0f} deg "
        f"-> recording starts {trigger_at.strftime('%H:%M:%S')} UTC")
    sleep_until(trigger_at)

    now = datetime.datetime.now(datetime.timezone.utc)
    duration_s = max(30.0, (los - now).total_seconds() + cfg["pass_postroll_s"])

    log(f"AOS approaching - stopping {GRAVES_SERVICE}, recording for {duration_s:.0f}s")
    systemctl("stop", GRAVES_SERVICE)
    time.sleep(cfg["device_cooldown_s"])

    try:
        recorder = os.path.join(HERE, "iss_recorder.py")
        r = subprocess.run(
            [sys.executable, recorder, "--config", config_path,
             "--duration-s", str(int(duration_s)), "--pass-aos", payload["aos_utc"]],
            timeout=duration_s + 60)
        if r.returncode != 0:
            log(f"WARN: iss_recorder.py exited {r.returncode}")
    except subprocess.TimeoutExpired:
        log("WARN: iss_recorder.py timed out and was killed")
    except Exception as exc:
        log(f"WARN: iss_recorder.py failed to run: {exc}")
    finally:
        # GRAVES must come back regardless of how the recording went -
        # a bug in the new ISS code must never leave meteor watch dark
        time.sleep(cfg["device_cooldown_s"])
        log(f"pass complete - restarting {GRAVES_SERVICE}")
        systemctl("start", GRAVES_SERVICE)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.ini"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    log(f"started - min elevation {cfg['min_elevation']:.0f} deg, "
        f"station {cfg['lat']:.3f},{cfg['lon']:.3f}")

    # Safety net for a crash mid-pass: if this process died between stopping
    # and restarting graves-watch, systemd's Restart=on-failure brings this
    # daemon back, but a fresh main() wouldn't otherwise touch graves-watch
    # again until the *next* qualifying pass - which could be hours away.
    # Starting an already-running unit is a harmless no-op.
    systemctl("start", GRAVES_SERVICE)

    while True:
        try:
            payload = satpass.next_pass_payload(
                "iss", cfg["lat"], cfg["lon"],
                min_elevation_deg=cfg["min_elevation"], cache_dir=cfg["data_dir"])
            if not payload.get("available"):
                log(f"no qualifying pass in the next 72h - rechecking in "
                    f"{cfg['recheck_interval_s']:.0f}s")
                time.sleep(cfg["recheck_interval_s"])
                continue
            run_one_pass(cfg, args.config, payload)
        except Exception as exc:  # this daemon must never die silently
            log(f"ERROR in scheduler loop: {exc}")
            # make sure GRAVES isn't stuck paused after an unexpected failure
            systemctl("start", GRAVES_SERVICE)
            time.sleep(60)


if __name__ == "__main__":
    main()
