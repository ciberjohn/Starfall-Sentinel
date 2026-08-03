# STARFALL SENTINEL — GRAVES Meteor Station Complete End-to-End Guide

Everything from opening the box to watching meteors on your phone. Follow the
parts in order. Each part ends with a verification step — don't skip them.

**TL;DR:** dipole on the window → plug into dongle → `apt install rtl-sdr` →
`simulate.py --test` → `detector.py --calibrate` → `detector.py` →
`dashboard.py` → Discord webhook. ~1 hour, no soldering, no drilling.

---

## Architecture (what you are building)

```
[ Dipole, oriented toward GRAVES per bearing.py ]
        │ RG174 coax (SMA)
        ▼
   [ RTL-SDR dongle ]  ← your "DTV receptor", USB
        │
        ▼
 [ detector.py ] ──► data/pings.csv   (every meteor event)
        │        └─► data/live.csv    (1 Hz signal level)
        │        └─► Discord webhook  (⚡ ping alerts)
        ▼
 [ dashboard.py :8090 ] ──► Starfall Sentinel dashboard: chart + explainer +
                            event log, reachable from any device (LAN or
                            optionally Tailscale)
```

---

## Part 1 — Hardware & physical setup

### Check your dongle's connector

| Connector | What to do |
|---|---|
| **SMA** (most common, incl. RTL-SDR Blog V3/V4) | plug the RG174 lead straight in |
| **MCX** (cheap bare sticks) | buy an MCX→SMA adapter (~£5) |

### Find your bearing first

Before you build or orient anything, get your own numbers:

```bash
python3 bearing.py --from "51.5,-0.1"
```

(That's an illustrative example only, not a real station — swap in your own
coordinates. Right-click your location in Google Maps for the lat/lon, or
use any GPS app.) The output gives you: true bearing and distance to GRAVES,
the dipole-axis heading to build along, a Yagi boom heading if you go
directional instead, and rough elevation angles for the forward-scatter
region. Add `--declination <deg>` for a magnetic-compass bearing too.

### Antenna (interior dipole — the recommended setup)

1. **Elements:** 2 × **52 cm** for GRAVES's 143.050 MHz. If your dongle kit
   has telescopic whips, extend each to 52 cm. Otherwise use two 52 cm
   pieces of wire/rod on a stick, meeting at the feedpoint. (Other
   frequency? half-length in meters ≈ `71.5 / frequency_MHz`.)
2. **Position:** a window whose wall roughly faces the direction to GRAVES
   from your bearing calculation above. Any room works.
3. **Orientation:** dipole **horizontal**, elements running along your
   computed dipole axis (use a phone compass app to lay it out). Its
   broadside then faces GRAVES.
4. **Height:** as high as practical (curtain rail, bookshelf). A sill at 1 m
   still works.
5. **Clearance:** 30+ cm from radiators, TVs, metal frames, foil-backed
   insulation.
6. **Verify later with data:** run `--calibrate` at two positions, keep the
   higher level.

### Cable

1–3 m **RG174 SMA→SMA** for indoor. Later, for the fence-pillar Yagi upgrade:
10 m RG174 through the door rubber seal (hinge end), no drilling.

**Shopping:** example UK-supplier list with links in `SHOPPING.md` — adapt the
search terms to your own country's retailers.

✔ **Done when:** dipole assembled, connected to dongle, dongle in the computer.

---

## Part 2 — Software installation

### Prerequisites

| What | Need |
|---|---|
| Computer | Laptop or Raspberry Pi 4/5, Ubuntu/Debian Linux |
| Python | 3.8+ (Ubuntu 22.04+/Pi OS Bookworm: already installed) |
| Internet | one-time for `apt` and `git` |
| Sudo | admin access on the machine |

No pip packages anywhere in this project — standard library only.

### Step 2.1 — Install RTL-SDR tools

```bash
sudo apt update
sudo apt install -y rtl-sdr
```

### Step 2.2 — Verify the dongle

```bash
rtl_test
```

Expected: `Found 1 device(s)` then sample-reading output. Ctrl-C after a few
seconds.

If `No supported devices found`:

```bash
sudo rmmod dvb_usb_rtl28xxu 2>/dev/null
rtl_test
```

### Step 2.3 — Get the code

```bash
gh repo clone ciberjohn/Starfall-Sentinel
cd Starfall-Sentinel
```

(No `gh` CLI? `git clone https://github.com/ciberjohn/Starfall-Sentinel`
works the same.)

### Step 2.4 — Configure

```bash
cp config.ini.example config.ini
```

`config.ini` is gitignored — your personal settings (webhook URL,
coordinates, station name) never get committed. The detector defaults
already target GRAVES correctly; you'll come back to this file for the
Discord webhook (Part 3) and for the `[station]` section, which drives the
dashboard's bearing compass:

| Key | Meaning |
|---|---|
| `lat`, `lon` | your coordinates — same ones you gave `bearing.py` |
| `region` | freeform header text — keep it region-level (e.g. "Ontario, Canada"), never a postcode/house-level detail |
| `target_name`, `target_lat`, `target_lon` | the radar/beacon you're aiming at (defaults to GRAVES) |

The dashboard always displays bearing/distance rounded to the nearest
5°/100 km no matter how precisely you fill these in — that's a deliberate
anti-doxxing measure for a page meant to run as a public 24/7 stream (more
in Part 2, Step 2.8).

### Step 2.5 — Test without hardware

```bash
python3 simulate.py --test
```

✔ Expected: `PASS: end-to-end detector test`. Stop here if it fails.

### Step 2.6 — Find GRAVES (first light)

```bash
python3 detector.py --calibrate
```

You should see a floor around **46 dB**. If GRAVES is audible the level rises,
and you'll hear the radar's burst train. If nothing:

- nudge frequency: `python3 detector.py --calibrate --frequency 143.0505M`
  (500 Hz steps either way)
- try dongle correction: `--ppm 30`, `--ppm 60`, … until the level peaks
- sanity check on a known FM station first (e.g. `--frequency 98M`) — if you
  hear BBC Radio you know the chain works and the issue is frequency/ppm

Note the winning `--ppm` for the next step.

### Step 2.7 — Run the detector

```bash
python3 detector.py --ppm <your-value>     # omit --ppm if default was fine
```

Events → `data/pings.csv`; levels → `data/live.csv`. Every ping prints:

```
[PING] 2026-08-03T06:32:07.363Z start 1600 ms dur 1000 ms +23.0 dB over floor
```

### Step 2.8 — Dashboard ("Starfall Sentinel")

```bash
python3 dashboard.py --port 8090 --data-dir data
```

Open **http://localhost:8090** — the Starfall Sentinel dashboard: live strip
chart (blue = level, aqua dashed = noise floor, hover for exact values), a
plain-language "how to read this" panel for viewers with no background, three
live sensor quadrants (solar weather, meteor shower forecast, GRAVES bearing
compass), and the recent events table. Dark-themed with a light Star
Trek/LCARS accent, a synthesized ambient hum + ping chime (mute toggle in the
header), meant to look good as an OBS Browser Source if you're putting the
station on a stream. Only the solar-weather quadrant needs internet (NOAA
data, fetched server-side every 5 min); everything else stays fully
offline-capable.

From another device: **http://\<your-tailscale-ip\>:8090** (see Part 4), or
just the machine's regular LAN IP if you're not using Tailscale. Edited
`dashboard.py`? `systemctl --user restart graves-dashboard` picks up the
change.

✔ **Done when:** you have seen at least one `[PING]` line or a clean chart.

---

## Part 3 — Discord alerts

### 3.1 Create a channel webhook

1. In Discord, open the server and channel where alerts should appear
   (suggestion: create a dedicated **#meteor-station** channel, or reuse an
   existing science/hobby channel).
2. Click the channel **Settings** (gear icon) → **Integrations** →
   **Webhooks** → **New Webhook**.
3. Name it whatever you like — e.g. **"My Meteor Station"** (avatar optional).
4. Click **Copy Webhook URL** — it looks like
   `https://discord.com/api/webhooks/1234567890/ABC...`
5. You need the **Manage Webhooks** permission on that channel (as a server
   admin you have it).

### 3.2 Configure

```bash
nano config.ini
```

```ini
webhook = https://discord.com/api/webhooks/1234567890/ABC...
```

### 3.3 Verify (no meteor required)

```bash
python3 detector.py --test-webhook --config config.ini
```

✔ A ✅ **my-station-1** webhook test message appears in the channel (or
whatever `name = ` is set to in your `config.ini` — see `config.ini.example`).
If not, re-check the URL and the permission.

### 3.4 Restart the detector with alerts

```bash
systemctl --user restart graves-watch        # if installed as a service
# or just re-run: python3 detector.py --config config.ini
```

Every ping posts (station name is whatever `name = ` is set to in `config.ini`):

```
⚡ **my-station-1** PING @ 2026-08-03T06:32:07.363Z
`1000 ms · +23.0 dB over floor · peak 66.1 dB`
```

Long events (>8 s — likely sporadic-E or interference) are silent by default;
set `webhook_long = true` in `config.ini` if you want those too.

---

## Part 4 — Running 24/7

### systemd autostart

```bash
./deploy.sh
```

This is the one command that does Steps 2.1–2.4 and this section, idempotently
— run it fresh, or re-run it any time (new machine, after `git pull`, dongle
swapped). It substitutes your actual repo path into the `.service` templates
before installing them, so you don't need to hand-edit anything.

Doing it by hand instead? Two things the templates require that a plain `cp`
won't give you:

1. **Path substitution** — `graves-watch.service`/`graves-dashboard.service`
   use a literal `/home/YOUR_USERNAME/graves-detector` as a stand-in path.
   Substitute your real checkout path before copying:
   ```bash
   loginctl enable-linger $USER
   mkdir -p ~/.config/systemd/user
   sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-watch.service      > ~/.config/systemd/user/graves-watch.service
   sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-dashboard.service > ~/.config/systemd/user/graves-dashboard.service
   systemctl --user daemon-reload
   systemctl --user enable --now graves-watch graves-dashboard
   ```
2. **No `User=`/`SupplementaryGroups=`** — these are already absent from the
   templates. If you're adapting them for a *system* unit (`/etc/systemd/system`,
   root-run) instead of a `--user` unit, that's the one place those two
   directives would apply; don't add them back to a `--user` unit — the user
   session manager lacks `CAP_SETGID` and the service will exit
   `216/GROUP` ("Failed to determine supplementary groups: Operation not
   permitted"). A `--user` unit already runs as you, with your existing group
   memberships (incl. `plugdev` for the dongle).

Check:

```bash
systemctl --user status graves-watch
systemctl --user status graves-dashboard
```

### Remote access (Tailscale)

[Tailscale](https://tailscale.com) is an optional private mesh VPN — install
it on the acquisition host and any device you want to reach it from, and
each gets a stable private IP that works from anywhere, with no port
forwarding or public exposure. Entirely optional: plain LAN access, a
reverse proxy, or any other tunnel works just as well.

| Task | How |
|---|---|
| Dashboard | `http://<your-tailscale-ip>:8090` from any device |
| Admin shell | `ssh <user>@<your-tailscale-ip>` |
| Health check | `curl http://localhost:8090/status` → `OK | live_age_s 3 | pings_today 12` |

### Files to watch

| File | Grows | Rotate |
|---|---|---|
| `data/live.csv` | ~3.5 MB/day (1 row/s) | safe to delete/archive anytime; dashboard reads the tail |
| `data/pings.csv` | small | archive monthly |

---

## Part 5 — Reading the data

### The ping log (`data/pings.csv`)

| Column | Meaning |
|---|---|
| `utc` / `local` | when it happened |
| `start_ms` | ms since the detector started |
| `duration_ms` | burst length |
| `peak_db_over_floor` | strength above the noise floor |
| `peak_level_db` | absolute level |
| `floor_db` | noise floor at that moment |
| `kind` | `PING` (80 ms–8 s) or `LONG` (>8 s) |

### What's a meteor?

- **Ping:** sharp rise, 0.1–2 s, +10…+25 dB. That's your meteor.
- **Sporadic-E:** minutes-long elevated noise — not a meteor.
- **Aircraft:** slow rise and fall over tens of seconds.
- **Local RFI:** repeats at fixed intervals.

### Showers to watch

| Shower | Peak (2026) | Expect |
|---|---|---|
| **Perseids** | ~12 Aug | your first big night — up to 100/hr radiant |
| Orionids | ~21 Oct | moderate |
| Geminids | ~14 Dec | the year's best rates |

---

## Part 6 — Day-1 checklist (when hardware arrives)

1. [ ] Dongle connector identified (SMA or MCX adapter)
2. [ ] Bearing computed (`bearing.py`) and dipole built (2 × 52 cm), horizontal, oriented along the computed dipole axis
3. [ ] `sudo apt install -y rtl-sdr` · `rtl_test` shows the device
4. [ ] `python3 simulate.py --test` → PASS
5. [ ] `python3 detector.py --calibrate` → GRAVES audible (tune ppm)
6. [ ] `python3 detector.py --config config.ini` → first PING logged
7. [ ] `python3 dashboard.py` → chart visible on phone via LAN or Tailscale
8. [ ] Discord webhook created, `--test-webhook` ✅ in channel
9. [ ] systemd autostart enabled (24/7)
10. [ ] Leave it running for the **Perseids night of 11–12 Aug**

---

## Part 7 — Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No supported devices found` | Dongle unplugged or DVB driver conflict → `sudo rmmod dvb_usb_rtl28xxu`, replug |
| `usb_claim_interface error -6` | USB permissions → reinstall rtl-sdr, reboot, or `sudo usermod -aG plugdev $USER` + logout |
| Calibrate shows no signal | Wrong freq/ppm → nudge 500 Hz; try `--ppm 0/30/60`; test on a known FM station first |
| Dashboard `OFFLINE` | No live sample for 30 s → detector not running: `systemctl --user status graves-watch` |
| No pings for hours | Normal outside showers; confirm floor is stable in calibrate |
| Webhook silent | URL wrong or permission → `python3 detector.py --test-webhook --config config.ini` |
| `WARN: webhook failed` in console | Network/Discord issue — detector keeps running regardless |
| `graves-watch` exits immediately, `code=exited, status=216/GROUP` | `User=`/`SupplementaryGroups=` added to a `--user` unit — remove them, the session already runs as you |
| Service shows `active (running)` but dashboard stays `OFFLINE` / no `data/*.csv` in the repo | Missing `WorkingDirectory=` in the unit — check `~/.config/systemd/user/graves-watch.service`; `data/pings.csv` may have landed under `$HOME/data/` instead. Re-run `./deploy.sh` and `systemctl --user restart graves-watch graves-dashboard` (enabling an already-active unit does **not** restart it) |

---

## Appendix — command reference

| Command | What it does |
|---|---|
| `python3 detector.py --calibrate` | live levels, no logging — tuning mode |
| `python3 detector.py` | run the station (rtl_fm → pings.csv + live.csv) |
| `python3 detector.py --config config.ini` | run with your settings (webhook, ppm…) |
| `python3 detector.py --test-webhook --config config.ini` | test Discord alert |
| `python3 dashboard.py --port 8090 --data-dir data` | serve the Starfall Sentinel dashboard |
| `python3 simulate.py --test` | hardware-free end-to-end test |
| `python3 bearing.py --from "your-lat,your-lon"` | recompute bearing/distance/dipole orientation for any location (`--from` is required) |
| `python3 tools/feed_realtime.py x.pcm \| python3 detector.py --source stdin` | replay recorded audio at real-time rate |

## References

- GRAVES: ONERA space-surveillance radar, Broye-lès-Pesmes (47.351N, 5.515E), 143.050 MHz
- SDR# (Windows waterfall viewer): <https://airspy.com/download/>
- RTL-SDR: <https://www.rtl-sdr.com>
- International Meteor Organization: <https://www.imo.net>
