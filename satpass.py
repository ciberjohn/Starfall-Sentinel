#!/usr/bin/env python3
"""ISS (or any TLE-tracked satellite) next-pass predictor - stdlib only.

A self-contained, simplified SGP4-class propagator: J2 secular perturbation
(RAAN/argument-of-perigee precession) plus the TLE's own mean-motion decay
term for drag, no external ephemeris libraries. That's a deliberate scope
choice: full SGP4 (deep-space resonance, higher-order drag) matters for
week-plus forecasts, not for "when's the next pass" a few hours to a couple
of days out - well within the accuracy this gets you (rise/set times good to
within roughly a minute against a TLE that's a few days old).

TLEs come from Celestrak (celestrak.org), no account or key needed, and are
cached to disk so the station keeps working (with a slowly staling TLE) if
the network is briefly down.

Usage:
  python3 satpass.py --from "51.5,-0.1"                 # next ISS pass
  python3 satpass.py --from "51.5,-0.1" --min-elevation 20
  python3 satpass.py --from "51.5,-0.1" --cache data/iss_tle.txt
"""

import argparse
import datetime
import math
import os
import sys
import urllib.request

EARTH_R_KM = 6378.135      # WGS72 equatorial radius (TLE convention)
GM_KM3_S2 = 398600.8       # WGS72 earth gravitational parameter
J2 = 1.082616e-3
EARTH_ROT_RAD_S = 7.2921150e-5  # sidereal rotation rate

CELESTRAK_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"

# Keyed by NORAD catalog number (stable public ID, see celestrak.org/satcat)
# so adding another bright pass-visible object later is one dict entry, not
# a redesign - today only the ISS is wired up in the dashboard tile.
KNOWN_SATELLITES = {
    "iss": {"name": "ISS (ZARYA)", "norad_id": 25544,
            "freqs": [("145.800 MHz FM", "voice downlink / SSTV (ARISS)"),
                      ("145.825 MHz FM", "APRS digipeater, 1200 baud AFSK - usually on"),
                      ("145.990 MHz FM", "cross-band repeater downlink (67.0 Hz CTCSS), when active")]},
}


def parse_tle(lines):
    """Parse a 2- or 3-line TLE into orbital elements (SI-ish units: rad, s)."""
    lines = [l for l in lines if l.strip()]
    name = None
    if not lines[0].startswith(("1 ", "2 ")):
        name = lines[0].strip()
        lines = lines[1:]
    l1, l2 = lines[0], lines[1]

    epoch_year = int(l1[18:20])
    epoch_year += 2000 if epoch_year < 57 else 1900
    epoch_day = float(l1[20:32])
    epoch = (datetime.datetime(epoch_year, 1, 1, tzinfo=datetime.timezone.utc)
             + datetime.timedelta(days=epoch_day - 1.0))

    ndot2 = float(l1[33:43])  # first derivative of mean motion / 2, rev/day^2

    inc_deg = float(l2[8:16])
    raan_deg = float(l2[17:25])
    ecc = float("0." + l2[26:33].strip())
    argp_deg = float(l2[34:42])
    ma_deg = float(l2[43:51])
    mm_rev_day = float(l2[52:63])

    return {
        "name": name,
        "epoch": epoch,
        "ndot_rev_day2": 2.0 * ndot2,
        "inc": math.radians(inc_deg),
        "raan0": math.radians(raan_deg),
        "ecc": ecc,
        "argp0": math.radians(argp_deg),
        "ma0": math.radians(ma_deg),
        "n0_rad_s": mm_rev_day * 2.0 * math.pi / 86400.0,
    }


def fetch_tle(norad_id, cache_path=None, max_age_hours=6.0, timeout=8):
    """Celestrak TLE, cached to disk. Falls back to a stale cache (any age) if
    the network fetch fails, so a NOAA/Celestrak hiccup or offline stretch
    degrades pass accuracy instead of killing the feature outright."""
    if cache_path and os.path.exists(cache_path):
        age_h = (time_now() - os.path.getmtime(cache_path)) / 3600.0
        if age_h < max_age_hours:
            with open(cache_path) as f:
                return f.read().splitlines()

    url = CELESTRAK_TLE_URL.format(norad_id=norad_id)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            text = r.read(8192).decode("ascii", "replace")
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 3:
            raise ValueError(f"unexpected TLE response ({len(lines)} lines)")
        if cache_path:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w") as f:
                f.write("\n".join(lines) + "\n")
        return lines
    except Exception as exc:
        if cache_path and os.path.exists(cache_path):
            print(f"[satpass] TLE fetch failed ({exc}), using stale cache", file=sys.stderr)
            with open(cache_path) as f:
                return f.read().splitlines()
        raise


def time_now():
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _mat3_vec(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def _mat3_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _rot_z(theta):
    c, s = math.cos(theta), math.sin(theta)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def _rot_x(theta):
    c, s = math.cos(theta), math.sin(theta)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def gmst_rad(dt):
    """Greenwich Mean Sidereal Time (Vallado's IAU-82 approximation), radians."""
    jd = (dt - datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)).total_seconds() / 86400.0 + 2451545.0
    t = (jd - 2451545.0) / 36525.0
    gmst_deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
                + 0.000387933 * t * t - (t ** 3) / 38710000.0)
    return math.radians(gmst_deg % 360.0)


def eci_position_km(elts, dt):
    """Satellite ECI position (km) at absolute time dt, via a J2-secular
    Keplerian propagation of the TLE's mean elements."""
    t = (dt - elts["epoch"]).total_seconds()
    t_days = t / 86400.0

    n0 = elts["n0_rad_s"]
    a0 = (GM_KM3_S2 / (n0 * n0)) ** (1.0 / 3.0)
    e = elts["ecc"]
    p0 = a0 * (1.0 - e * e)
    i = elts["inc"]

    raan_dot = -1.5 * n0 * J2 * (EARTH_R_KM / p0) ** 2 * math.cos(i)
    argp_dot = 0.75 * n0 * J2 * (EARTH_R_KM / p0) ** 2 * (5.0 * math.cos(i) ** 2 - 1.0)

    raan = elts["raan0"] + raan_dot * t
    argp = elts["argp0"] + argp_dot * t
    # secular mean-anomaly drift from the TLE's mean-motion-decay term
    ma = elts["ma0"] + n0 * t + math.pi * elts["ndot_rev_day2"] * t_days * t_days

    ma = ma % (2.0 * math.pi)
    ea = ma  # Newton-Raphson for eccentric anomaly, E - e sin E = M
    for _ in range(8):
        ea -= (ea - e * math.sin(ea) - ma) / (1.0 - e * math.cos(ea))

    nu = 2.0 * math.atan2(math.sqrt(1.0 + e) * math.sin(ea / 2.0),
                           math.sqrt(1.0 - e) * math.cos(ea / 2.0))
    r = a0 * (1.0 - e * math.cos(ea))
    x_pf, y_pf = r * math.cos(nu), r * math.sin(nu)

    r_mat = _mat3_mul(_mat3_mul(_rot_z(raan), _rot_x(i)), _rot_z(argp))
    return _mat3_vec(r_mat, (x_pf, y_pf, 0.0))


def eci_to_ecef(pos_eci, dt):
    theta = gmst_rad(dt)
    x, y, z = pos_eci
    c, s = math.cos(theta), math.sin(theta)
    return (x * c + y * s, -x * s + y * c, z)


def observer_ecef_km(lat_deg, lon_deg, alt_km=0.0):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    r = EARTH_R_KM + alt_km
    return (r * math.cos(lat) * math.cos(lon),
            r * math.cos(lat) * math.sin(lon),
            r * math.sin(lat))


def look_angles(lat_deg, lon_deg, obs_ecef, sat_ecef):
    """(azimuth_deg, elevation_deg, range_km) from observer to satellite."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    dx = sat_ecef[0] - obs_ecef[0]
    dy = sat_ecef[1] - obs_ecef[1]
    dz = sat_ecef[2] - obs_ecef[2]

    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = (-math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy
              + math.cos(lat) * dz)
    up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz

    rng = math.sqrt(east * east + north * north + up * up)
    elevation = math.degrees(math.asin(up / rng))
    azimuth = math.degrees(math.atan2(east, north)) % 360.0
    return azimuth, elevation, rng


def elevation_at(elts, lat_deg, lon_deg, alt_km, dt):
    obs = observer_ecef_km(lat_deg, lon_deg, alt_km)
    sat_eci = eci_position_km(elts, dt)
    sat_ecef = eci_to_ecef(sat_eci, dt)
    return look_angles(lat_deg, lon_deg, obs, sat_ecef)


def next_passes(elts, lat_deg, lon_deg, alt_km=0.0, min_elevation_deg=10.0,
                 start=None, search_hours=72, step_s=15, n=1):
    """Scan forward for the next `n` passes clearing `min_elevation_deg`.
    Coarse time-stepped search with a short bisection refine on each AOS/LOS
    crossing - simple and, at a 15 s step, easily good enough for a casual
    "when do I point the antenna" readout."""
    start = start or datetime.datetime.now(datetime.timezone.utc)
    obs = observer_ecef_km(lat_deg, lon_deg, alt_km)

    def el_at(dt):
        sat_eci = eci_position_km(elts, dt)
        sat_ecef = eci_to_ecef(sat_eci, dt)
        return look_angles(lat_deg, lon_deg, obs, sat_ecef)

    def refine_crossing(dt_lo, dt_hi, rising):
        for _ in range(20):
            dt_mid = dt_lo + (dt_hi - dt_lo) / 2
            _, el, _ = el_at(dt_mid)
            above = el >= min_elevation_deg
            if above == rising:
                dt_hi = dt_mid
            else:
                dt_lo = dt_mid
        return dt_lo + (dt_hi - dt_lo) / 2

    passes = []
    steps = int(search_hours * 3600 / step_s)
    prev_dt = start
    _, prev_el, _ = el_at(prev_dt)
    in_pass = prev_el >= min_elevation_deg
    aos = start if in_pass else None
    max_el, max_az, max_dt = prev_el, None, prev_dt

    for k in range(1, steps + 1):
        dt = start + datetime.timedelta(seconds=k * step_s)
        az, el, _ = el_at(dt)

        if not in_pass and el >= min_elevation_deg:
            aos = refine_crossing(prev_dt, dt, rising=True)
            in_pass = True
            max_el, max_az, max_dt = el, az, dt
        elif in_pass:
            if el > max_el:
                max_el, max_az, max_dt = el, az, dt
            if el < min_elevation_deg:
                los = refine_crossing(prev_dt, dt, rising=False)
                aos_az, _, _ = el_at(aos)
                los_az, _, _ = el_at(los)
                passes.append({
                    "aos": aos, "aos_az_deg": round(aos_az, 0),
                    "max_time": max_dt, "max_el_deg": round(max_el, 1), "max_az_deg": round(max_az, 0),
                    "los": los, "los_az_deg": round(los_az, 0),
                    "duration_s": int((los - aos).total_seconds()),
                })
                in_pass = False
                if len(passes) >= n:
                    return passes

        prev_dt, prev_el = dt, el

    return passes


def compass_name(deg):
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[round((deg % 360) / 22.5) % 16]


def next_pass_payload(sat_key, lat_deg, lon_deg, alt_km=0.0, min_elevation_deg=10.0,
                       cache_dir=None):
    """One dict, ready to serve as JSON: next pass for a known satellite, or
    an explicit 'none in the search window' / 'unavailable' result - never
    raises, so a Celestrak or math hiccup can't take a caller down."""
    sat = KNOWN_SATELLITES[sat_key]
    cache_path = os.path.join(cache_dir, f"{sat_key}_tle.txt") if cache_dir else None
    try:
        lines = fetch_tle(sat["norad_id"], cache_path=cache_path)
        elts = parse_tle(lines)
        found = next_passes(elts, lat_deg, lon_deg, alt_km,
                             min_elevation_deg=min_elevation_deg, n=1)
    except Exception as exc:
        print(f"[satpass] {sat_key} pass prediction failed: {exc}", file=sys.stderr)
        return {"available": False, "name": sat["name"], "freqs": sat["freqs"]}

    if not found:
        return {"available": False, "name": sat["name"], "freqs": sat["freqs"], "no_pass_in_window": True}

    p = found[0]
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "available": True,
        "name": sat["name"],
        "freqs": sat["freqs"],
        "aos_utc": p["aos"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aos_az_deg": p["aos_az_deg"],
        "aos_az_compass": compass_name(p["aos_az_deg"]),
        "max_el_deg": p["max_el_deg"],
        "max_az_deg": p["max_az_deg"],
        "max_az_compass": compass_name(p["max_az_deg"]),
        "los_utc": p["los"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "los_az_deg": p["los_az_deg"],
        "los_az_compass": compass_name(p["los_az_deg"]),
        "duration_s": p["duration_s"],
        "minutes_until": round((p["aos"] - now).total_seconds() / 60.0, 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="from_ll", required=True, help='observer "lat,lon" e.g. "51.5,-0.1"')
    ap.add_argument("--sat", default="iss", choices=sorted(KNOWN_SATELLITES), help="which satellite (default: iss)")
    ap.add_argument("--min-elevation", type=float, default=10.0, help="min pass elevation, degrees (default 10)")
    ap.add_argument("--passes", type=int, default=1, help="how many upcoming passes to list")
    ap.add_argument("--cache", default=None, help="TLE cache file path")
    args = ap.parse_args()

    lat, lon = (float(x) for x in args.from_ll.split(","))
    sat = KNOWN_SATELLITES[args.sat]
    lines = fetch_tle(sat["norad_id"], cache_path=args.cache)
    elts = parse_tle(lines)
    found = next_passes(elts, lat, lon, min_elevation_deg=args.min_elevation, n=args.passes)

    print(f"== {sat['name']} - next pass{'es' if args.passes != 1 else ''} over {lat:.3f},{lon:.3f} ==")
    if not found:
        print(f"   No pass above {args.min_elevation:.0f} deg elevation in the next 72 h.")
        return
    for p in found:
        dur_min, dur_s = divmod(p["duration_s"], 60)
        print(f"\n   AOS  {p['aos'].strftime('%Y-%m-%d %H:%M:%S UTC')}  az {p['aos_az_deg']:.0f} deg ({compass_name(p['aos_az_deg'])})")
        print(f"   MAX  {p['max_time'].strftime('%H:%M:%S UTC')}  el {p['max_el_deg']:.0f} deg  az {p['max_az_deg']:.0f} deg ({compass_name(p['max_az_deg'])})")
        print(f"   LOS  {p['los'].strftime('%H:%M:%S UTC')}  az {p['los_az_deg']:.0f} deg ({compass_name(p['los_az_deg'])})")
        print(f"   Duration: {dur_min}m{dur_s:02d}s")
    if sat["freqs"]:
        print("\n   LISTEN ON")
        for freq, note in sat["freqs"]:
            print(f"   {freq:<16s} {note}")


if __name__ == "__main__":
    main()
