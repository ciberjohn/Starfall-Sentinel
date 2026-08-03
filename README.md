# STARFALL SENTINEL — GRAVES Meteor-Scatter Detection Station

Passive meteor detection: listen for reflections of the **GRAVES space-surveillance
radar** (near Dijon, France) at **143.050 MHz**. No transmitter needed — you only
receive. Detect meteors through clouds, in daylight, from your living room.

This works from anywhere in range of GRAVES (most of Europe) or any other
forward-scatter beacon you point it at — the software has no location baked
in. The first thing you'll do is run one command to get *your* bearing,
distance and antenna orientation for *your* coordinates.

> **New to the project? Read [`GUIDE.md`](GUIDE.md)** — the complete
> end-to-end instructions: hardware setup → installation → Discord alerts →
> 24/7 operation → troubleshooting.

| Quick facts | |
|---|---|
| Target | GRAVES radar, 143.050 MHz, horizontal polarization |
| Your bearing & distance | run `python3 bearing.py --from "your-lat,your-lon"` — unique to your location, see below |
| Recommended antenna | **Half-wave dipole** — 2 × 52 cm, horizontal, indoors is fine to start |
| Software | Linux + rtl-sdr tools + Python 3.8+ (no pip packages needed) |

## How it works

Meteors entering the atmosphere at 80–110 km altitude leave a short-lived ionized
trail. VHF signals from a distant transmitter scatter off that trail — like a
mirror — toward your receiver. The reflection lasts a fraction of a second to a
few seconds: the classic meteor **"ping"**. Forward-scatter geometry means the
antenna must be **horizontal** (GRAVES transmits horizontally), but exact aiming
is forgiving: a dipole's broad pattern easily covers your path.

> Note: a bearing/azimuth number you might see quoted for GRAVES somewhere
> online is only valid for the specific location whoever wrote it was at —
> it is *not* a universal number. Your bearing depends entirely on where you
> are. Compute your own (next section) rather than trusting a number from
> someone else's QTH.

---

## Find your bearing (do this first)

Before mounting anything, get your own true bearing, distance and antenna
orientation:

```bash
python3 bearing.py --from "51.5,-0.1"
```

(That's an illustrative example, not a real station — replace it with your
own coordinates. Find them from Google Maps: right-click your location and
the lat/lon is the first entry in the context menu, or use any GPS app.)

The output gives you everything needed to mount the antenna:

- **True bearing** — compass direction from you to GRAVES, in true (not
  magnetic) degrees. Add `--declination <deg>` for the magnetic-compass
  equivalent (declination depends on your location and the current year —
  look yours up if you need it).
- **Distance** — great-circle range in km.
- **Dipole axis** — the heading to lay a dipole's elements along, so its
  broadside (strongest reception) faces GRAVES.
- **Yagi boom** — if you build a directional antenna instead, point the boom
  here.
- **Elevation angles** — rough angle to the forward-scatter specular point
  at a few reference ranges, useful if you're tilting a directional antenna.

### Antenna pointing, in general

- **Element orientation:** a half-wave dipole is bidirectional — lay the
  elements *perpendicular* to your bearing line so the antenna's broadside
  faces the target. `bearing.py` prints the exact dipole-axis heading for
  your coordinates; don't guess it from a map.
- **Polarization:** keep it **horizontal**. GRAVES, and most meteor-scatter
  beacons, transmit horizontally polarized signals — a vertical antenna
  gives up several dB for no reason.
- **Element length:** ~**52 cm per side** for GRAVES's 143.050 MHz. For any
  other frequency, half-length in meters ≈ `71.5 / frequency_MHz`.
- **Height:** mount as high as practical. A curtain rail or bookshelf beats
  a windowsill; a fence-post mount beats an indoor one. Distance from the
  ground and nearby metal/masonry both matter.
- **Verify empirically, always.** The math gets you in the right
  neighbourhood, but a dipole's pattern is broad and real reception depends
  on walls, terrain and local interference. Run `python3 detector.py
  --calibrate`, compare a couple of positions or orientations, and keep
  whichever gives the higher, stable level — data over speculation.

---

## The setup: interior dipole (recommended)

**This is the primary configuration, not a compromise.** GRAVES is a kW-class
signal and unusually strong on the ground across most of Europe; many
observers get their first detection from a dipole indoors, on the first try.
A 143 MHz half-wave dipole is only **1.05 m tip-to-tip** — it fits on a
windowsill.

### Mounting (10 minutes)

1. **Position:** a window whose wall roughly faces the direction from you to
   GRAVES (see "Find your bearing" above) — any room. Window glass costs
   only ~1–3 dB at VHF, so it doesn't need to be exact.
2. **Orientation:** elements **horizontal**, running along your computed
   dipole axis, so the broadside faces GRAVES. A small compass app on your
   phone is all you need to lay it out once you have the heading.
3. **Height:** as high as practical — curtain rail, bookshelf top, wardrobe.
   Every metre helps, but a sill at 1 m works.
4. **Element length:** **52 cm per side** (143 MHz). If your dongle came with
   telescopic whips, extend each to 52 cm and you're done.
5. **Clearance:** keep 30+ cm away from radiators, TV screens, metal window
   frames and foil-backed insulation behind the wall.

Walls attenuate (plasterboard ~1–2 dB, brick ~5–10 dB, double glazing ~1–3 dB)
but GRAVES has margin to spare. If a windowsill is inconvenient, a wall mount
anywhere facing roughly the right direction still works. **Verify
empirically**: run `detector.py --calibrate` at two positions and keep the
one with the higher level — data over speculation.

### Cable

A **1–3 m RG174 patch lead (SMA male → SMA male)** from dipole to dongle is all
the indoor setup needs. Details in `SHOPPING.md`.

### Optional later upgrade: outdoor Yagi

When you want extra sensitivity for weak (daytime/off-peak) activity: mount a
2 m-band Yagi **horizontal**, boom pointed at your computed bearing, as high
up as you can manage outdoors, fed by an RG174 run through a door/window seal
if you'd rather not drill. This is an enhancement, not a requirement.

---

## Hardware shopping list

See **`SHOPPING.md`** for an example UK-supplier list with links — adapt the
search terms to your own country's retailers. Summary:

| Item | Need | Cost |
|---|---|---|
| RTL-SDR dongle (your "DTV receptor") | ✔ have it | — |
| Antenna | telescopic dipole kit **or** bare-wire dipole (2 × 52 cm) | £0–20 |
| Coax | RG174, SMA–SMA, 1–3 m | £8–12 |
| MCX→SMA adapter | only if your dongle has an MCX socket | £5 |

---

## Installation (step by step)

### Prerequisites

| What | Requirement | Notes |
|---|---|---|
| Computer | A laptop or Raspberry Pi 4/5 running **Ubuntu/Debian Linux** | The dongle plugs into this. A Pi 4/5 (~£50–70) is ideal for 24/7; an old laptop works identically |
| SDR | RTL-SDR dongle ("DTV receptor") | Already owned |
| Antenna | Dipole (above) | Already covered |
| Python | 3.8+ | Ubuntu 22.04+ and Pi OS Bookworm ship 3.10/3.11 — no install needed |
| Internet | One-time, for `apt` and `git` | Not needed afterwards |

None of the project scripts need pip packages — Python standard library only.

### Step 1 — Install the RTL-SDR tools

```bash
sudo apt update
sudo apt install -y rtl-sdr
```

This installs `rtl_test`, `rtl_fm`, `rtl_power` plus the USB permission rules.

### Step 2 — Plug in the dongle and verify it

```bash
rtl_test
```

**Expected:** `Found 1 device(s)` then a stream of `Reading samples in async
mode...`. Press Ctrl-C after a few seconds.

**If it says `No supported devices found`:** the dongle isn't connected, or the
DVB-T driver grabbed it. Fix:

```bash
sudo rmmod dvb_usb_rtl28xxu 2>/dev/null   # one-off; the rtl-sdr package blacklists it at boot
rtl_test
```

### Step 3 — Get the code

```bash
gh repo clone ciberjohn/Starfall-Sentinel
cd Starfall-Sentinel
```

(No `gh`? `git clone https://github.com/ciberjohn/Starfall-Sentinel` works
identically.)

### Step 4 — Configure

```bash
cp config.ini.example config.ini
```

`config.ini` is gitignored — it holds your personal, per-deployment settings
and is never committed. The detector defaults already target GRAVES
correctly; the two things worth editing now are the `[station]` section and,
later, the Discord webhook.

The `[station]` section drives the dashboard's bearing compass:

| Key | Meaning |
|---|---|
| `lat`, `lon` | your station's coordinates (same numbers you passed to `bearing.py`) |
| `region` | freeform text shown in the dashboard header — keep it **region-level only** (e.g. "Pacific Northwest, USA", "Bavaria, Germany"), never a postcode or house-level detail |
| `target_name` | label for the radar/beacon you're aiming at (defaults to "GRAVES radar") |
| `target_lat`, `target_lon` | that target's coordinates (defaults to GRAVES's) |

**Privacy note:** these coordinates are used locally to compute the bearing
and distance shown on the dashboard, but the *displayed* numbers are
deliberately rounded to the nearest 5° / 100 km, regardless of how precisely
you fill in `[station]`. That's intentional, not a bug — the dashboard is
designed to be safe to run as a public 24/7 stream, and a precise bearing +
distance from a target whose own coordinates are public (GRAVES, or any
other well-known beacon) would be enough to reverse-geolocate a station to
street level. Don't try to "fix" this back to exact values if you fork the
project — the coarsening is the point.

### Step 5 — Test without hardware (2 minutes, do this first)

```bash
python3 simulate.py --test
```

**Expected:** `PASS: end-to-end detector test`. This proves the detector,
logging and classification work before any hardware is involved.

### Step 6 — First light: find GRAVES

```bash
python3 detector.py --calibrate
```

You should see a level reading ~46 dB with a stable floor. If GRAVES is audible
you'll see the level rise, or hear the radar's characteristic burst train on the
audio. If you see nothing:

- nudge the frequency in 500 Hz steps: `python3 detector.py --calibrate --frequency 143.0505M`
- or correct the dongle's frequency error with `--ppm 30`, then `--ppm 60`, etc.
  until the signal peaks. Note the winning `--ppm` for Step 7.

### Step 7 — Run the detector

```bash
python3 detector.py
```

Logs every event to `data/pings.csv` and writes 1 Hz level samples to
`data/live.csv`. Run it in its own terminal, or make it auto-start (Step 9).

### Step 8 — Real-time dashboard ("Starfall Sentinel")

```bash
python3 dashboard.py --port 8090 --data-dir data
```

Open **http://localhost:8090** on the acquisition machine for the **Starfall
Sentinel** dashboard — a live strip chart of signal level and noise floor, a
plain-language explainer for anyone watching who doesn't know what GRAVES or
forward-scatter is, and the recent-events table. From any other device on
your network, open **http://\<your-tailscale-ip\>:8090** — or just use the
machine's regular LAN IP if you're not using Tailscale.

> [Tailscale](https://tailscale.com) is an optional private mesh VPN — it
> lets you reach the dashboard securely from your phone or another computer
> without opening any ports on your router or exposing it to the public
> internet. Not required: plain LAN access, a reverse proxy, or a tunnel
> service all work too.

Zero-dependency Python stdlib, same as the rest of the project — no
Docker or pip install required; it runs as the `graves-dashboard` systemd
service (Step 9). Designed to double as an OBS Browser Source if you want to
put the station on a public stream — dark-themed, self-explanatory for
viewers with no context, a light Star Trek/LCARS visual touch, a synthesized
ambient hum + a distinct chime on real meteor pings (mute toggle in the
header), and three live "sensor quadrants": solar weather (Kp index + solar
wind — the one feature on this page that needs internet, since it only
exists on NOAA's servers), the next/active meteor shower, and a compass
showing the bearing toward GRAVES computed from your `[station]` config
(rounded for privacy, see Step 4).

The chart library is bundled, so the core station (detector, chart, alerts)
needs **no internet** as before. The one exception is the solar-weather
quadrant — Kp index and solar wind only exist on NOAA's servers, fetched
server-side every 5 minutes and cached; if there's no connection, that one
panel just reads "no data," everything else keeps working offline.

If you edit `dashboard.py`, pick up the change on a running station with
`systemctl --user restart graves-dashboard`.

### Step 9 — Auto-start (optional, for 24/7 operation)

```bash
./deploy.sh
```

`deploy.sh` installs rtl-sdr, checks the dongle, creates `config.ini` if
missing, runs the self-test, then installs `graves-watch` and
`graves-dashboard` as systemd **user** services (with `WorkingDirectory` set
to wherever you cloned the repo) and enables linger so they survive reboots
without a login session. Safe to re-run any time — e.g. after `git pull`.

Prefer to do it by hand? The equivalent manual steps:

```bash
loginctl enable-linger $USER
mkdir -p ~/.config/systemd/user
sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-watch.service      > ~/.config/systemd/user/graves-watch.service
sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-dashboard.service > ~/.config/systemd/user/graves-dashboard.service
systemctl --user daemon-reload
systemctl --user enable --now graves-watch graves-dashboard
```

> The `.service` files are templates using a literal `/home/YOUR_USERNAME/graves-detector`
> placeholder — the `sed` above (or `deploy.sh`) substitutes in your actual
> repo path. Don't just `cp` them verbatim unless your clone happens to live
> at that exact literal path, and don't add `User=`/`SupplementaryGroups=` —
> see the comments in `graves-watch.service` for why (`--user` units already
> run as you).

Check: `systemctl --user status graves-watch graves-dashboard`.

### Step 10 — Discord alerts (optional)

Create a webhook in your Discord channel, paste its URL into `config.ini`:

```ini
webhook = https://discord.com/api/webhooks/...
```

Restart the detector. Every ping then posts `⚡ PING · 850 ms · +18 dB`.

---

## Reading the log (`data/pings.csv`)

| Column | Meaning |
|---|---|
| `utc` / `local` | event timestamp |
| `start_ms` | milliseconds since stream start |
| `duration_ms` | burst length |
| `peak_db_over_floor` | how strong (dB above noise floor) |
| `peak_level_db` | absolute windowed RMS dB |
| `kind` | `PING` (80 ms – 8 s) or `LONG` (> 8 s — sporadic-E / aircraft / interference) |

Meteor pings are typically **0.1–2 s**, sharp and strong. Long flat events are
not meteors. During major showers (Perseids ~12 Aug, Geminids ~14 Dec) ping
rates rise markedly.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No supported devices found` | Dongle not connected, or DVB driver conflict → `sudo rmmod dvb_usb_rtl28xxu`; replug |
| `usb_claim_interface error -6` / permission denied | USB rules not loaded → reinstall rtl-sdr, reboot, or `sudo usermod -aG plugdev $USER` + logout |
| Calibrate shows no signal | Wrong frequency/ppm → nudge `--frequency` in 500 Hz steps; try `--ppm 0/30/60`; confirm with a known FM station (e.g. 98.0 MHz) first |
| Dashboard shows `OFFLINE` | No fresh `live.csv` sample in 30 s → detector not running (or the dongle dropped). Check `systemctl --user status graves-watch` |
| Empty ping log after hours | Normal outside showers; check GRAVES is actually up and your floor is stable in calibrate |
| Webhook alerts never arrive | Wrong URL, or Discord rate limits → check detector console for `WARN: webhook failed` |

## Operating notes

- **Best hours:** local early morning; during showers, all night.
- **Interference:** pings (sharp, < 2 s) vs sporadic-E (minutes), airplanes
  (slow rise), local RFI (repeats at fixed intervals).
- **Safety:** unplug the antenna during thunderstorms; route coax away from
  mains; never work on antennas in the rain.

## Roadmap

- [x] Location-agnostic bearing/distance/antenna-orientation tool (`bearing.py`)
- [x] RTL-SDR detector + simulator with an end-to-end self-test (6/6 seeds PASS)
- [x] Real-time dashboard with privacy-coarsened bearing display
- [x] Discord webhook alerting on pings
- [x] `deploy.sh` one-command install + systemd autostart
- [ ] Meteor-shower alert cron (Perseids/Geminids rate spikes)

## Contributing

Starfall Sentinel is MIT licensed (see `LICENSE`) and maintained as a small,
shared community tool — forks, pull requests and issues are all welcome. If
you adapt it for a different target beacon, a different SDR, or a different
platform, consider sending the change back: `bearing.py`'s `TARGETS` dict and
the `[station]` config section were built specifically so this project isn't
tied to any one radar or any one person's location, and improvements in that
spirit are especially welcome. No CLA, no formal process — open an issue or
a PR.

## References

- GRAVES: ONERA space-surveillance radar, Broye-lès-Pesmes (47.351N, 5.515E), 143.050 MHz
- SDR# (Windows waterfall viewer): <https://airspy.com/download/>
- RTL-SDR: <https://www.rtl-sdr.com>
- International Meteor Organization: <https://www.imo.net>
