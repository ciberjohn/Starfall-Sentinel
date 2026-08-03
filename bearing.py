#!/usr/bin/env python3
"""Azimuth / distance / antenna-orientation calculator for meteor-scatter targets.

Computes great-circle bearing and distance from your QTH to GRAVES (or any
target), the horizontal-dipole axis orientation (broadside to the arrival
direction), and rough elevation angles for the forward-scatter specular region.

Usage:
  python3 bearing.py --from "51.5,-0.1"                      # you -> GRAVES
  python3 bearing.py --from "51.5,-0.1" --target graves
  python3 bearing.py --from "51.5,-0.1" --to 47.351,5.515 --name "custom"
  python3 bearing.py --from "51.5,-0.1" --declination 1.0    # + magnetic bearing

--from is required - there's no sensible default location for a shared tool.
Find your own lat,lon from Google Maps (right-click your location) or any
GPS app.
"""

import argparse
import math
import sys

EARTH_R = 6371.0  # km

TARGETS = {
    "graves": {
        "name": "GRAVES radar (ONERA, Broye-les-Pesmes near Dijon, FR)",
        "lat": 47.351, "lon": 5.515,
        "freq": "143.050 MHz",
        "pol": "horizontal",
        "note": ("kW-class continuous space-surveillance radar; the standard "
                 "European forward-scatter meteor beacon. Signal is a "
                 "CW-like burst train."),
    },
    "gb3mrs": {
        "name": "UK 6m meteor beacon (50 MHz band)",
        "lat": 52.0, "lon": -2.0,
        "freq": "50.406 MHz",
        "pol": "horizontal",
        "note": ("Amateur 50 MHz meteor beacon network - activity varies; "
                 "verify current status before relying on it."),
    },
}


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing (true north), degrees 0-360."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return math.degrees(math.atan2(x, y)) % 360.0


def distance_km(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def midpoint(lat1, lon1, lat2, lon2):
    """Great-circle midpoint."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    bx = math.cos(phi2) * math.cos(dlam)
    by = math.cos(phi2) * math.sin(dlam)
    phi_m = math.atan2(math.sin(phi1) + math.sin(phi2),
                       math.sqrt((math.cos(phi1) + bx) ** 2 + by ** 2))
    lam_m = math.radians(lon1) + math.atan2(by, math.cos(phi1) + bx)
    return math.degrees(phi_m), math.degrees(lam_m)


def elevation_deg(h_km, d_km):
    """Apparent elevation of a point at height h_km over surface distance
    d_km, including Earth-curvature drop (no refraction)."""
    drop = (d_km ** 2) / (2 * EARTH_R)
    return math.degrees(math.atan((h_km - drop) / d_km))


def compass_name(b):
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((b + 11.25) % 360 // 22.5)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="from_", required=True,
                    help='your QTH "lat,lon" (e.g. "51.5,-0.1") - required, no default')
    ap.add_argument("--target", default="graves",
                    help="named target (graves, gb3mrs) or 'custom'")
    ap.add_argument("--to", default=None, help='custom target "lat,lon"')
    ap.add_argument("--name", default=None, help="custom target name")
    ap.add_argument("--freq", default=None, help="custom target frequency")
    ap.add_argument("--declination", type=float, default=None,
                    help="magnetic declination in degrees EAST (magnetic = true - decl)")
    ap.add_argument("--meteor-h", type=float, default=95.0,
                    help="assumed meteor altitude km for elevation table")
    args = ap.parse_args()

    try:
        lat1, lon1 = (float(x) for x in args.from_.replace(" ", "").split(","))
    except ValueError:
        print("ERROR: --from must be 'lat,lon'", file=sys.stderr)
        raise SystemExit(1)

    if args.to:
        try:
            lat2, lon2 = (float(x) for x in args.to.replace(" ", "").split(","))
        except ValueError:
            print("ERROR: --to must be 'lat,lon'", file=sys.stderr)
            raise SystemExit(1)
        name = args.name or "custom"
        freq = args.freq or "?"
        note = ""
    elif args.target in TARGETS:
        t = TARGETS[args.target]
        lat2, lon2, name, freq = t["lat"], t["lon"], t["name"], t["freq"]
        note = t["note"]
    else:
        print(f"ERROR: unknown target '{args.target}' (known: "
              f"{', '.join(TARGETS)} or use --to)", file=sys.stderr)
        raise SystemExit(1)

    brng = bearing_deg(lat1, lon1, lat2, lon2)
    dist = distance_km(lat1, lon1, lat2, lon2)
    dipole = (brng - 90.0) % 180.0
    mid_lat, mid_lon = midpoint(lat1, lon1, lat2, lon2)
    mid_dist = distance_km(lat1, lon1, mid_lat, mid_lon)

    print(f"== {name} ==")
    print(f"   Target QTH      : {lat2:.3f}, {lon2:.3f}")
    print(f"   Frequency       : {freq}")
    print(f"   Polarization    : horizontal (match it)")
    print()
    print("   YOUR LOCATION")
    print(f"   QTH             : {lat1:.3f}, {lon1:.3f}")
    print(f"   True bearing    : {brng:.1f} deg  ({compass_name(brng)})")
    if args.declination is not None:
        mag = (brng - args.declination) % 360.0
        print(f"   Magnetic bearing: {mag:.1f} deg  (declination {args.declination:+.1f} deg E)")
    print(f"   Distance        : {dist:.0f} km")
    print()
    print("   ANTENNA ORIENTATION (horizontal dipole / wire)")
    print(f"   Dipole axis     : run elements at {dipole:.0f} deg == "
          f"{(dipole + 180.0) % 180.0:.0f} deg (broadside to arrival)")
    print("                     i.e. perpendicular to the line of sight")
    print(f"   Yagi boom       : point at {brng:.0f} deg, keep boom near-horizontal "
          f"(tilt up slightly)")
    print()
    print(f"   FORWARD-SCATTER GEOMETRY (meteor altitude {args.meteor_h:.0f} km)")
    print(f"   Great-circle midpoint : {mid_lat:.2f}, {mid_lon:.2f} "
          f"({mid_dist:.0f} km from you)")
    el_mid = elevation_deg(args.meteor_h, mid_dist)
    print(f"   Elevation to specular point at midpoint: {el_mid:.1f} deg")
    for d in (300, 400, 500, 600):
        if d < dist:
            print(f"   Elevation at {d:3d} km range: {elevation_deg(args.meteor_h, d):5.1f} deg")
    if note:
        print(f"   Note            : {note}")
    print()
    print("   PRACTICAL")
    print("   - Interior windowsill dipole is the recommended starting setup - it")
    print("     fits on a sill and this is usually a strong enough signal to work")
    print("     indoors. Keep it HORIZONTAL, elevated as high as practical.")
    print(f"   - The dipole's axis runs {compass_name(dipole)}-{compass_name((dipole + 180.0) % 360.0)}; "
          f"the broadside (max response)")
    print(f"     faces the ~{compass_name(brng)} arrival direction.")
    print("   - Outdoor/fence-post Yagi is an optional later upgrade for extra")
    print("     sensitivity, not a requirement.")


if __name__ == "__main__":
    main()
