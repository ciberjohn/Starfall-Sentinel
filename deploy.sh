#!/usr/bin/env bash
# Deploy the GRAVES meteor-scatter station on this machine: install rtl-sdr
# tools, verify the dongle, set up config.ini, and install/enable the
# graves-watch + graves-dashboard systemd user services for 24/7 operation.
# Safe to re-run (idempotent).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "==> Repo directory: $REPO_DIR"

echo "==> Installing rtl-sdr tools"
if ! command -v rtl_test >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y rtl-sdr
else
    echo "    rtl-sdr already installed, skipping"
fi

echo "==> Checking for RTL-SDR dongle"
if lsusb | grep -qE "0bda:283[28]"; then
    echo "    RTL2832/2838 dongle present"
else
    echo "    WARNING: no RTL2832/2838 dongle detected on the USB bus"
fi

echo "==> Configuring config.ini"
if [ ! -f "$REPO_DIR/config.ini" ]; then
    cp "$REPO_DIR/config.ini.example" "$REPO_DIR/config.ini"
    echo "    Created config.ini from example — edit it to add your webhook URL"
else
    echo "    config.ini already exists, leaving it untouched"
fi

echo "==> Running hardware-free self-test"
python3 "$REPO_DIR/simulate.py" --test

echo "==> Installing systemd user services"
mkdir -p "$SYSTEMD_USER_DIR"
for svc in graves-watch graves-dashboard graves-iss; do
    sed -e "s#/home/YOUR_USERNAME/graves-detector#$REPO_DIR#g" \
        "$REPO_DIR/$svc.service" > "$SYSTEMD_USER_DIR/$svc.service"
done

echo "==> Enabling linger so services run without an active login session"
loginctl enable-linger "$USER"

systemctl --user daemon-reload
systemctl --user enable graves-watch graves-dashboard graves-iss
systemctl --user restart graves-watch graves-dashboard graves-iss

if [ -d "$HOME/Dropbox" ]; then
    echo "==> Dropbox found — installing daily backup timer"
    sed -e "s#/home/YOUR_USERNAME/graves-detector#$REPO_DIR#g" \
        "$REPO_DIR/graves-backup.service" > "$SYSTEMD_USER_DIR/graves-backup.service"
    cp "$REPO_DIR/graves-backup.timer" "$SYSTEMD_USER_DIR/graves-backup.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now graves-backup.timer
else
    echo "==> No ~/Dropbox found — skipping backup timer (backup.sh needs a sync target)"
fi

echo "==> Done. Status:"
systemctl --user --no-pager status graves-watch graves-dashboard graves-iss || true

echo
echo "Dashboard: http://localhost:8090"
echo "Health:    curl http://localhost:8090/status"
