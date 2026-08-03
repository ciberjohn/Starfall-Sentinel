# Shopping List — GRAVES Detection Station

Target: GRAVES 143.050 MHz. Run `python3 bearing.py --from "your-lat,your-lon"`
to get your own true bearing before buying anything direction-sensitive (a
Yagi). Interior dipole first; Yagi on fence pillar later.

## Step 0: the receiver itself (skip if you already have an RTL-SDR)

Everything here runs on an **RTL-SDR** — a ~$25–35 / £20–30 USB dongle
(RTL2832U chip), originally a cheap DVB-T TV tuner, repurposed as a general
SDR receiver. Recommended: **`RTL-SDR Blog V3`** or **`RTL-SDR Blog V4`**
(SMA connector, stable TCXO, the community-standard choice) — search that
exact name on Amazon, eBay, AliExpress, or any electronics retailer:
<https://www.amazon.com/s?k=RTL-SDR+Blog+V3>. A cheap `RTL2832U R820T2 dongle`
also works, usually with an MCX connector instead (adapter below).

## Check what you already have
- Check its RF connector:
  - **SMA** (most common, incl. RTL-SDR Blog V3/V4) → plug cable straight in.
  - **MCX** (cheap bare sticks) → you need an **MCX-to-SMA adapter** (~£5).
- If your dongle came in a **kit with telescopic dipoles**, the antenna is also
  free: set each whip to **52 cm** and you are tuned for 143 MHz.

## To purchase

| # | Item | Spec | Search term | Est. cost |
|---|---|---|---|---|
| 0 | RTL-SDR dongle *(if you don't have one)* | RTL2832U-based, SMA preferred | `RTL-SDR Blog V3` or `RTL-SDR Blog V4` | $25–35 / £20–30 |
| 1 | Telescopic dipole kit *(only if you have a bare dongle)* | 2 whips, each extends to ~52 cm, SMA plug | `SMA telescopic dipole antenna VHF` | £10–20 |
| 2 | RG174 coax patch lead (indoor use) | SMA male → SMA male, 1–3 m, 2.8 mm Ø | `RG174 SMA cable 2m` | £8–12 |
| 3 | RG174 coax (future fence-pillar Yagi) | SMA male → SMA male, 10 m | `RG174 SMA 10m` | £15–20 |
| 4 | MCX→SMA adapter *(only if dongle has MCX)* | female MCX → male SMA | `MCX to SMA adapter` | £5 |
| 5 | SMA female→female barrel *(optional)* | joins two male cables | `SMA female to female adapter` | £4 |

Search terms work on Amazon/eBay/AliExpress/rtl-sdr.com or any local
electronics retailer — prices above are rough USD/GBP estimates, not
region-specific quotes.

Specialist UK ham shops (all live): **Waters & Stanton** (hamradiostore.co.uk),
**Radioworld** (radioworld.co.uk), **Moonraker** (moonrakeronline.com). Useful
for better coax (RG58/RG213) if you later accept a 5 mm cable.

## Why RG174 for the door gap
2.8 mm Ø flexes through the door/window rubber seal — no drilling, no cutting.
Loss at 143 MHz ≈ 0.25 dB/m; 10 m ≈ 2.5 dB, negligible for GRAVES.

## Antenna orientation (interior window)
- Dipole **horizontal**; elements running along *your* computed dipole axis
  — run `python3 bearing.py --from "your-lat,your-lon"` first, it prints the
  exact heading for your location (this varies by where you are; there's no
  universal number).
- Broadside (maximum response) then faces GRAVES.
- Elevate it: curtain rail, bookshelf top, or top of a wardrobe — 1.5 m+ helps.
- Tune for 143.05 MHz: each element **52 cm** tip-to-tip at the feedpoint.
  Other frequency? half-length in meters ≈ `71.5 / frequency_MHz`.

## Software
| Software | Platform | Purpose |
|---|---|---|
| **SDR# (SDRSharp)** | Windows | The classic RTL-SDR waterfall viewer — official download: **airspy.com/download** |
| **GQRX** | Linux | Same job; nice waterfall + FFT |
| **CubicSDR** | Win/Linux/macOS | Simple cross-platform alternative |
| **rtl_fm + detector.py** | Linux | This project's automated ping detector — the actual station software |
| **dashboard.py** | Linux | Real-time strip chart + event log, served over HTTP (LAN or Tailscale) |

Windows note: SDR# needs the **Zadig** driver for the RTL-SDR. On Linux no
driver work needed — `rtl-sdr` package tools are ready to go once installed.

## Suggested checklist
- [ ] rtl-sdr tools installed (`sudo apt install -y rtl-sdr`)
- [ ] Detector + simulator verified (`python3 simulate.py --test` → PASS)
- [ ] Dongle connector check (SMA vs MCX)
- [ ] Cable + dipole ordered
- [ ] Bearing computed (`bearing.py --from "your-lat,your-lon"`)
- [ ] First live calibration (`python3 detector.py --calibrate`)
- [ ] Perseids watch (peak ~12 Aug each year)
