# STARFALL SENTINEL — GRAVES Meteor Station Complete End-to-End Guide

Everything from opening the box to watching meteors on your phone. Follow the
parts in order. Each part ends with a verification step — don't skip them.

**TL;DR:** dipole on the window → plug into dongle → `apt install rtl-sdr` →
`simulate.py --test` → `detector.py --calibrate` → `detector.py` →
`dashboard.py` → Discord webhook. ~1 hour, no soldering, no drilling.

---

## Architecture (what you are building)

```
[ Dipole on south window ]
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
                            event log, any device via Tailscale
```

---

## Part 1 — Hardware & physical setup

### Check your dongle's connector

| Connector | What to do |
|---|---|
| **SMA** (most common, incl. RTL-SDR Blog V3/V4) | plug the RG174 lead straight in |
| **MCX** (cheap bare sticks) | buy an MCX→SMA adapter (~£5) |

### Antenna (interior dipole — the recommended setup)

1. **Elements:** 2 × **52 cm**. If your dongle kit has telescopic whips, extend
   each to 52 cm. Otherwise use two 52 cm pieces of wire/rod on a stick,
   meeting at the feedpoint.
2. **Position:** a window on the **south wall** of the house. Any room works;
   the wall facing your garden is ideal.
3. **Orientation:** dipole **horizontal**, elements running **NE–SW** (use a
   phone compass app: the your dipole axis line). Its broadside then faces ~123° SE —
   straight at GRAVES.
4. **Height:** as high as practical (curtain rail, bookshelf). A sill at 1 m
   still works.
5. **Clearance:** 30+ cm from radiators, TVs, metal frames, foil-backed
   insulation.
6. **Verify later with data:** run `--calibrate` at two positions, keep the
   higher level.

### Cable

1–3 m **RG174 SMA→SMA** for indoor. Later, for the fence-pillar Yagi upgrade:
10 m RG174 through the door rubber seal (hinge end), no drilling.

**Shopping:** full UK-supplier list with links in `SHOPPING.md`.

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

*(On this host it's already at `~/graves-detector`.)*

### Step 2.4 — Configure

```bash
cp config.ini.example config.ini
```

Defaults target GRAVES correctly. You only edit this for the Discord webhook
(Part 3) or gain tweaks.

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
plain-language "how to read this" panel for viewers with no background, four
live sensor quadrants (solar weather, meteor shower forecast, GRAVES bearing
compass, next ISS pass with its listening frequencies), and the recent events
table. Dark-themed with a light Star Trek/LCARS accent, a synthesized ambient
hum + ping chime (mute toggle in the header), meant to look good as an OBS
Browser Source if you're putting the station on a stream. If you stream it
24/7, prefer a hardware encoder in OBS (Intel QSV/VAAPI or NVENC) over
x264 — ~95% less CPU, quiet fans. Two quadrants need
internet: solar weather (NOAA, fetched server-side every 5 min) and the ISS
pass predictor (TLE from Celestrak, cached to disk for hours so a brief
outage doesn't blank it); everything else stays fully offline-capable.

From another device: **http://<tailscale-ip>:8090** (or the Pi's Tailscale IP —
see Part 4). Edited `dashboard.py`? `systemctl --user restart graves-dashboard`
picks up the change.

✔ **Done when:** you have seen at least one `[PING]` line or a clean chart.

---

## Part 3 — Discord alerts

### 3.1 Create a channel webhook

1. In Discord, open the server and channel where alerts should appear
   (suggestion: create a dedicated **#meteor-station** channel in the fleet
   server — or reuse #science).
2. Click the channel **Settings** (gear icon) → **Integrations** →
   **Webhooks** → **New Webhook**.
3. Name it **your QTH Meteor Station** (avatar optional).
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

✔ A ✅ **my-station-1** webhook test message appears in the channel. If not,
re-check the URL and the permission.

### 3.4 Restart the detector with alerts

```bash
systemctl --user restart graves-watch        # if installed as a service
# or just re-run: python3 detector.py --config config.ini
```

Every ping posts:

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

1. **Path substitution** — `graves-watch.service`/`graves-dashboard.service`/
   `graves-iss.service` hardcode `/home/YOUR_USERNAME/graves-detector` as a
   stand-in path. Substitute your real checkout path before copying:
   ```bash
   loginctl enable-linger $USER
   mkdir -p ~/.config/systemd/user
   sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-watch.service      > ~/.config/systemd/user/graves-watch.service
   sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-dashboard.service > ~/.config/systemd/user/graves-dashboard.service
   sed "s#/home/YOUR_USERNAME/graves-detector#$(pwd)#g" graves-iss.service      > ~/.config/systemd/user/graves-iss.service
   systemctl --user daemon-reload
   systemctl --user enable --now graves-watch graves-dashboard graves-iss
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
systemctl --user status graves-iss
```

### ISS pass listener (`graves-iss`)

A third service, `iss_scheduler.py`, watches for upcoming ISS passes
(`satpass.py`) and whenever one clears `[iss] min_elevation` in `config.ini`
(40° by default), it stops `graves-watch`, retunes the same dongle to
`145.825M` (ISS APRS - the most consistently-active ISS ham frequency) for
the pass window, and hands it back to `graves-watch` when the pass ends. Any
above-floor audio captured during that window is saved as a WAV clip and
shows up in the dashboard's "ISS Audio Log" table.

This is a real tradeoff, not a free feature: every qualifying pass is a few
minutes with no meteor coverage. `min_elevation` is the knob - raise it for
fewer, stronger-signal passes (less GRAVES downtime); lower it to catch more
passes at the cost of more downtime and weaker copy. Tune `[iss] threshold_db`
against a real pass with `python3 iss_recorder.py --calibrate --frequency 145.825M`
(same idea as `detector.py --calibrate` in Step 2.6) - FM's idle-vs-signal
margin is only a few dB, not GRAVES' 10+.

### Remote access (Tailscale)

| Task | How |
|---|---|
| Dashboard | `http://<host-tailscale-ip>:8090` from any device |
| Admin shell | `ssh <user>@<host-tailscale-ip>` |
| Health check | `curl http://localhost:8090/status` → `OK | live_age_s 3 | pings_today 12` |

No port forwarding, no public exposure — Tailscale is a private mesh (this
host: <tailscale-ip>).

### Files to watch

| File | Grows | Rotate |
|---|---|---|
| `data/live.csv` | ~3.5 MB/day (1 row/s) | safe to delete/archive anytime; dashboard reads the tail |
| `data/pings.csv` | small | archive monthly |
| `data/iss_hits.csv` | small, one row per capture | archive monthly |
| `data/iss_clips/*.wav` | ~190 KB/s of actual capture (quiet passes add nothing) | no automatic trimming yet - archive/delete old clips by hand if disk matters |

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

### Ping curves (echo profiles)

Every detection now saves its waveform (dB vs time at ~20 Hz) so you can
see the echo's shape: a sharp rise + exponential decay = a small
**underdense** meteor; long or irregular with fading = a large
**overdense** meteor. View them in the dashboard's Recent Events table
(click the ⤢ Shape button) or on the `/ping-curves` page; Discord alerts
attach a PNG of the curve. Per-echo stats: rise time, peak +dB,
half-power width, decay time constant (τ), oscillation count, size class.

### IMO citizen-science reports (optional)

The station can email hourly echo-count summaries to the International
Meteor Organization (IMO). The IMO Radio Commission (Director: Christian
Steyaert) coordinates forward-scatter observations; the working channel is
**`radio@imo.net`** (the Commission partners with ERAC, whose site is being
archived — the IMO address is the live channel).

- `imo_report.py` builds an IMO-style report: per-UTC-day hourly PING echo
  counts (LONG/sporadic-E events excluded) plus a station header, sent via
  SMTP from the account in `config.ini [email]`.
- Configure `config.ini [imo]`: `to` (recipient), `observer`, `station_name`.
- Build-only (no email): `python3 imo_report.py --config config.ini --date 2026-08-04`
- Send (pulls pings.csv from the station over SSH first):
  `python3 imo_report.py --config config.ini --pull-from-vostro --send`
- A daily 08:30 cron (`~/.hermes/scripts/starfall_imo.sh`) exists but is
  **PAUSED** until the Commission confirms the format it wants. The
  introduction email was sent 2026-08-04 from the station agent (Spock) on
  behalf of the owner, requesting replies go to both `[redacted]`
  and `[redacted]`. Resume the "IMO forward-scatter report (daily)"
  cron job once the format is agreed.

## Part 6 — Day-1 checklist (when hardware arrives)

1. [ ] Dongle connector identified (SMA or MCX adapter)
2. [ ] Dipole built (2 × 52 cm), horizontal, NE–SW, on the south window
3. [ ] `sudo apt install -y rtl-sdr` · `rtl_test` shows the device
4. [ ] `python3 simulate.py --test` → PASS
5. [ ] `python3 detector.py --calibrate` → GRAVES audible (tune ppm)
6. [ ] `python3 detector.py --config config.ini` → first PING logged
7. [ ] `python3 dashboard.py` → chart visible on phone via Tailscale
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
| `graves-iss` never seems to trigger | Normal if no pass has cleared `min_elevation` yet — `journalctl --user -u graves-iss` logs the next qualifying AOS it's waiting for; lower `min_elevation` if that's too rare for your liking |
| ISS Audio Log stays empty after a pass | Not every pass has traffic on 145.825 MHz at that moment — a "no hits" pass is a normal, correctly-working result, not a bug. Confirm the chain works with `iss_recorder.py --calibrate --frequency 145.825M` during a real pass |
| `graves-watch` stayed stopped after a pass | Should self-heal (`graves-iss` restarts it in a `finally` block, plus a safety check on its own startup) — if it didn't, `systemctl --user start graves-watch` and check `journalctl --user -u graves-iss` for what went wrong |

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
| `python3 bearing.py --from "51.5,-0.1"` | recompute azimuth/dipole orientation |
| `python3 satpass.py --from "51.5,-0.1"` | next ISS pass (rise/max/set, az/el, duration) + listening frequencies |
| `python3 iss_recorder.py --calibrate --frequency 145.825M` | live FM level monitor for tuning `[iss] threshold_db` — tuning mode |
| `python3 iss_scheduler.py --config config.ini` | run the ISS pass scheduler standalone (normally the `graves-iss` service) |
| `python3 tools/feed_realtime.py x.pcm \| python3 detector.py --source stdin` | replay recorded audio at real-time rate |

## References

- GRAVES: ONERA space-surveillance radar, Broye-lès-Pesmes (47.351N, 5.515E), 143.050 MHz
- SDR# (Windows waterfall viewer): <https://airspy.com/download/>
- RTL-SDR: <https://www.rtl-sdr.com>
- International Meteor Organization: <https://www.imo.net>
