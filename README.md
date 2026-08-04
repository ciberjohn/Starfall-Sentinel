# STARFALL SENTINEL — GRAVES Meteor-Scatter Detection Station

Passive meteor detection: listen for reflections of the **GRAVES space-surveillance
radar** (near Dijon, France) at **143.050 MHz**. No transmitter needed — you only
receive. Detect meteors through clouds, in daylight, from your living room.

**Station:** your own QTH — the station location lives only in your private `config.ini` and is never published.

> **New to the project? Read [`GUIDE.md`](GUIDE.md)** — the complete
> end-to-end instructions: hardware setup → installation → Discord alerts →
> 24/7 operation → troubleshooting.

| Quick facts | |
|---|---|
| Target | GRAVES radar, 143.050 MHz, horizontal polarization |
| Bearing (example) | **~123° true** for a your region observer — compute yours with `bearing.py` |
| Distance | your distance |
| Recommended antenna | **Interior half-wave dipole** — 2 × 52 cm, on a south-wall window |
| Software | Linux + rtl-sdr tools + Python 3.8+ (no pip packages needed) |

## How it works

Meteors entering the atmosphere at 80–110 km altitude leave a short-lived ionized
trail. VHF signals from a distant transmitter scatter off that trail — like a
mirror — toward your receiver. The reflection lasts a fraction of a second to a
few seconds: the classic meteor **"ping"**. Forward-scatter geometry means the
antenna must be **horizontal** (GRAVES transmits horizontally), but exact aiming
is forgiving: a dipole's broad pattern easily covers your path.

> Note: the 158° azimuth sometimes quoted online is wrong. The verified
> great-circle bearing from a your region observer → GRAVES (47.351N, 5.515E)
> is **~your bearing**, so that observer's dipole axis runs **NE–SW (your dipole axis)**,
> not East–West. Compute yours with `bearing.py` for your own QTH.

---

## The setup: interior dipole (recommended)

**This is the primary configuration, not a compromise.** GRAVES is a kW-class
signal and unusually strong on the ground; most UK observers get their first
detection from a dipole indoors, on the first try. A 143 MHz half-wave dipole is
only **1.05 m tip-to-tip** — it fits on a windowsill.

### Mounting (10 minutes)

1. **Position:** a window on the **south wall** of the house (any room; a south
   wall that faces the garden is ideal). Window glass costs only ~1–3 dB at VHF.
2. **Orientation:** elements **horizontal**, running **NE–SW** — i.e. the dipole
   lies along the your dipole axis line, so its broadside faces ~123° SE toward GRAVES.
   A small compass app on your phone is all you need.
3. **Height:** as high as practical — curtain rail, bookshelf top, wardrobe.
   Every metre helps, but a sill at 1 m works.
4. **Element length:** **52 cm per side** (143 MHz). If your dongle came with
   telescopic whips, extend each to 52 cm and you're done.
5. **Clearance:** keep 30+ cm away from radiators, TV screens, metal window
   frames and foil-backed insulation behind the wall.

Walls attenuate (plasterboard ~1–2 dB, brick ~5–10 dB, double glazing ~1–3 dB)
but GRAVES has margin to spare. If a windowsill is inconvenient, a wall mount
anywhere on the south side still works. **Verify empirically**: run
`detector.py --calibrate` at two positions and keep the one with the higher
level — data over speculation.

### Cable

A **1–3 m RG174 patch lead (SMA male → SMA male)** from dipole to dongle is all
the indoor setup needs. Details in `SHOPPING.md`.

### Optional later upgrade: Yagi on the fence pillar

When you want extra sensitivity for weak (daytime/off-peak) activity: mount a
2 m-band Yagi **horizontal**, boom at **123°**, 1.5–2 m up on the fence pillar,
fed by 10 m of RG174 through the door rubber seal — no drilling required. This
is an enhancement, not a requirement.

---

## Hardware shopping list

See **`SHOPPING.md`** for the full UK-supplier list with links. Summary:

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

On a fresh machine:

```bash
gh repo clone ciberjohn/Starfall-Sentinel
cd Starfall-Sentinel
```

(On this host the project already lives at `~/graves-detector` — skip this step.)

### Step 4 — Configure

```bash
cp config.ini.example config.ini
```

Default settings target GRAVES correctly. Only touch this later for the Discord
webhook or gain tweaks.

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
forward-scatter is, and the recent-events table. From any other device on your
Tailscale network, open **http://<host-tailscale-ip>:8090** (or the Pi's Tailscale
IP). Zero-dependency Python stdlib, same as the rest of the project — no
Docker or pip install required; it runs as the `graves-dashboard` systemd
service (Step 9). Designed to double as an OBS Browser Source if you want to
put the station on a public stream — dark-themed, self-explanatory for
viewers with no context, a light Star Trek/LCARS visual touch, a synthesized
ambient hum + a distinct chime on real meteor pings (mute toggle in the
header), four live "sensor quadrants" (solar weather, the next/active meteor
shower, a compass locked to the true bearing toward GRAVES, and the next ISS
pass with rise/max/set time, azimuth, elevation, duration), and — for passes
that clear a configurable elevation bar — an "ISS Audio Log": the dongle
briefly retunes off GRAVES to listen on the ISS's own frequencies, and any
above-floor audio it catches (voice, SSTV, APRS packets) is saved as a WAV
clip and playable right on the page.

The chart library is bundled, so the core station (detector, chart, alerts)
needs **no internet** as before. Two quadrants are the exception: solar
weather (Kp index + solar wind, from NOAA, refreshed every 5 minutes) and the
ISS pass predictor (TLE from Celestrak, refreshed every few hours and cached
to disk — a stale-but-present TLE keeps working through a network outage).
If there's no connection at all, those two panels just read "no data";
everything else keeps working offline.

If you edit `dashboard.py`, pick up the change on a running station with
`systemctl --user restart graves-dashboard`.

### Step 9 — Auto-start (optional, for 24/7 operation)

```bash
./deploy.sh
```

`deploy.sh` installs rtl-sdr, checks the dongle, creates `config.ini` if
missing, runs the self-test, then installs `graves-watch`, `graves-dashboard`,
and `graves-iss` (the ISS pass listener) as systemd **user** services (with
`WorkingDirectory` set to wherever you cloned the repo) and enables linger so
they survive reboots without a login session. Safe to re-run any time — e.g.
after `git pull`.

Prefer to do it by hand? The equivalent manual steps:

```bash
loginctl enable-linger $USER
mkdir -p ~/.config/systemd/user
sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-watch.service      > ~/.config/systemd/user/graves-watch.service
sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-dashboard.service > ~/.config/systemd/user/graves-dashboard.service
sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-iss.service      > ~/.config/systemd/user/graves-iss.service
systemctl --user daemon-reload
systemctl --user enable --now graves-watch graves-dashboard graves-iss
```

> The `.service` files are templates written for a `~/graves-detector`
> checkout — the `sed` (or `deploy.sh`) substitutes in your actual repo path.
> Don't just `cp` them verbatim unless your clone happens to live at that
> exact path, and don't add `User=`/`SupplementaryGroups=` — see the comments
> in `graves-watch.service` for why (`--user` units already run as you).

Check: `systemctl --user status graves-watch graves-dashboard graves-iss`.

`graves-iss` pauses `graves-watch` for the duration of any ISS pass that
clears `[iss] min_elevation` in `config.ini` (40° by default) — a real
tradeoff, since that's a few minutes of lost meteor coverage per qualifying
pass. Raise the threshold for fewer/stronger passes, lower it to catch more
at the cost of more downtime. See `config.ini.example`'s `[iss]` section.

`deploy.sh` also installs a daily `graves-backup.timer` if `~/Dropbox`
exists — it mirrors `data/` (pings + live samples) and `config.ini` into
`~/Dropbox/BACKUPS/Starfall-Sentinel/`, which Dropbox then syncs to the
cloud on its own. Check: `systemctl --user list-timers graves-backup.timer`.
`live.csv` itself is also kept bounded now — `live_max_hours` in
`config.ini` (default 12h) trims it every 30 min, so it no longer grows
forever (not that it was a real risk: ~2 MB/day, ~800 MB/year at 1 row/s).

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

- [x] Geometry verified (bearing your bearing, dipole your dipole axis)
- [x] RTL-SDR toolchain installed
- [x] Detector + simulator + end-to-end test (6/6 seeds PASS)
- [x] Real-time dashboard + remote access
- [x] First live calibration when the dongle arrives
- [x] Discord webhook alerting on pings
- [x] `deploy.sh` one-command install + systemd autostart
- [x] ISS SSTV decode (`sstv_decoder.py` pure-stdlib Robot 36 + `iss_sstv_decode.py` + timer)
- [x] Meteor-shower rate monitor (Ping Rate quadrant + `/sporadic-e` hourly chart)
- [x] Sporadic-E / LONG-event catalog (`/sporadic-e` page + `/api/sporadic-e`)
- [x] IMO citizen-science report (`imo_report.py`; intro sent to radio@imo.net 08-04 — daily cron paused awaiting format reply)
- [x] Ping-curve capture (`detector.py` per-event profiles + `curve_plot.py` PNG; Shape column + `/ping-curves` page + Discord curve attachments)
- [ ] Meteor-shower alert cron (Perseids/Geminids rate spikes)

## References

- GRAVES: ONERA space-surveillance radar, Broye-lès-Pesmes (47.351N, 5.515E), 143.050 MHz
- SDR# (Windows waterfall viewer): <https://airspy.com/download/>
- RTL-SDR: <https://www.rtl-sdr.com>
- International Meteor Organization: <https://www.imo.net>
