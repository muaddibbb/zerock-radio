#!/bin/bash
# ZeRock file watcher — runs as launchd agent
# Watches ZeRock source dirs; batches changes (30s latency) then runs sync.sh

SYNC="/Users/rkuperman/Documents/ZeRock-Git/sync.sh"
SRC="/Users/rkuperman/Documents/Claude_Code/ZeRock_Uploader"

/opt/homebrew/bin/fswatch \
  --event=Updated \
  --event=Created \
  --event=Removed \
  --event=Renamed \
  --latency=30 \
  --one-per-batch \
  "$SRC/server.js" \
  "$SRC/public" \
  "$SRC/zerock-radio" \
  "$SRC/package.json" \
  "$SRC/Dockerfile" \
  "$SRC/docker-compose.yml" \
  "$SRC/sam-agent" \
| while read -r _event; do
    "$SYNC"
done
