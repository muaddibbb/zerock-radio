#!/bin/bash
# ZeRock sync: source → git commit+push → Google Drive
# Triggered by Claude Code PostToolUse hook on every Edit/Write

SRC="/Users/rkuperman/Documents/Claude_Code/ZeRock_Uploader"
GIT_DIR="/Users/rkuperman/zerock-sync"
DRIVE_DIR="/Users/rkuperman/Library/CloudStorage/GoogleDrive-kuperoy@gmail.com/My Drive/ZeRock"
TOKEN="$(cat /Users/rkuperman/.zerock-gh-token 2>/dev/null)"
LOG="$GIT_DIR/sync.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync triggered" >> "$LOG"

# ── 1. Rsync source → git working directory ──────────────────────────────────
/usr/bin/rsync -a --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='dist' \
  --exclude='uploads' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='*.pkg' \
  --exclude='*.tar.gz' \
  --exclude='.DS_Store' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='logs/' \
  --exclude='zerock-radio/logs/' \
  --exclude='sam-queue.json' \
  --exclude='scheduled.json' \
  --exclude='team-updates-data.json' \
  --exclude='zerock-radio/schedule.json' \
  --exclude='zerock-radio/board_cancellations.json' \
  --exclude='zerock-radio/zikaron_schedule.json' \
  --exclude='zerock-radio/al_haroker_bookings.json' \
  --exclude='zerock-radio/now_playing.txt' \
  --exclude='zerock-radio/playlists/' \
  --exclude='fix*.py' \
  --exclude='check_radio.py' \
  --exclude='deploy_radio.py' \
  --exclude='sync.sh' \
  --exclude='watch.sh' \
  --exclude='sync.log' \
  "$SRC/" "$GIT_DIR/" 2>> "$LOG"

# ── 2. Git commit + push if there are changes ─────────────────────────────────
cd "$GIT_DIR" || exit 1

if ! /usr/bin/git diff --quiet || ! /usr/bin/git diff --cached --quiet || [ -n "$(/usr/bin/git ls-files --others --exclude-standard)" ]; then
  /usr/bin/git add -A
  CHANGED=$(/usr/bin/git diff --cached --name-only | head -5 | tr '\n' ' ')
  /usr/bin/git commit -m "Auto-sync: ${CHANGED}$(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1
  /usr/bin/git -c "url.https://${TOKEN}@github.com/.insteadOf=https://github.com/" push origin main >> "$LOG" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pushed to GitHub" >> "$LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] No changes, skipping commit" >> "$LOG"
fi

# ── 3. Rsync git working directory → Google Drive ─────────────────────────────
/usr/bin/rsync -a --delete \
  --exclude='.git' \
  --exclude='sync.sh' \
  --exclude='watch.sh' \
  --exclude='sync.log' \
  "$GIT_DIR/" "$DRIVE_DIR/" 2>> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Drive sync complete" >> "$LOG"
