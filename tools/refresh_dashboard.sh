#!/usr/bin/env bash
# Refresh the Brave dashboard tab on the station host (Vostro).
#
# The dashboard is streamed 24/7 to Twitch via OBS on the Vostro; the
# browser tab only picks up NEW page markup on a reload (data refreshes
# itself every few seconds via JS). After any dashboard.py change, run
# this so the stream shows the new layout.
#
# Mechanism (learned from Claude Code, 2026-08-03): Brave runs as a
# Flatpak on X11; `xdotool` (installed for this purpose) sends the reload
# keypress to the "STARFALL SENTINEL" window. We hard-reload
# (ctrl+shift+r) so the markup can't come from the browser cache.
#
# Run directly on the Vostro:
#   bash tools/refresh_dashboard.sh
# Or from the dev host over SSH:
#   ssh user@station-host "bash -s" < tools/refresh_dashboard.sh
set -uo pipefail
export DISPLAY=:0
wid=$(xdotool search --name "STARFALL SENTINEL" 2>/dev/null | head -1)
if [ -z "$wid" ]; then
  echo "ERROR: STARFALL SENTINEL window not found (Brave open?)" >&2
  exit 1
fi
echo "[refresh-dashboard] refreshing window $wid"
xdotool windowactivate --sync "$wid" 2>/dev/null || xdotool windowfocus --sync "$wid"
sleep 0.5
xdotool key --window "$wid" ctrl+shift+r 2>/dev/null || xdotool key ctrl+shift+r
echo "[refresh-dashboard] sent ctrl+shift+r (hard reload)"
sleep 2
xdotool search --name "STARFALL SENTINEL" >/dev/null 2>&1 \
  && echo "[refresh-dashboard] window alive after refresh" \
  || echo "[refresh-dashboard] WARN: window gone"
