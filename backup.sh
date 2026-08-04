#!/usr/bin/env bash
# Back up this station's detection history + config to Dropbox (the Dropbox
# desktop client syncs it to the cloud automatically once it lands here).
# Mirrors current state each run rather than keeping dated copies - Dropbox's
# own file-version history covers "what did this look like last week" if
# you ever need it. Safe to re-run any time.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# NOTE: previously ~/Dropbox/BACKUPS/Starfall-Sentinel. Dropbox's selective
# sync excluded that path and renamed the folder "BACKUPS (Selective Sync
# Conflict)", which broke the cp mid-run. A fresh top-level folder syncs by
# default, so use a dedicated one instead.
DEST="$HOME/Dropbox/Starfall-Sentinel-Backup"

mkdir -p "$DEST/data"
rsync -a --delete "$REPO_DIR/data/" "$DEST/data/"
[ -f "$REPO_DIR/config.ini" ] && cp "$REPO_DIR/config.ini" "$DEST/config.ini"

echo "[$(date -u +%FT%TZ)] backed up data/ + config.ini to $DEST" >> "$DEST/backup.log"
