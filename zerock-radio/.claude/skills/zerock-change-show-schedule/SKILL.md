---
name: zerock-change-show-schedule
description: Move a ZeRock show's broadcast, upload or rerun time, or hide/show a show on the WordPress board. Use for "move X one hour ahead", "change upload time", "remove/show X on the board". Covers the queue entries that silently keep the old time.
---

# Change a show's time or board visibility

## Rule that causes most mistakes
Changing `SHOW_SCHEDULE` in `radio_app.py` only affects FUTURE entries. Entries already in `schedule.json` on rocky keep their old times and **override the schedule on the board**. Always do both parts.

## A. Code (`radio_app.py`)
- Times: edit `time`, `upload_time`, `rerun_days_offset`, `rerun_time` of the show's `SHOW_SCHEDULE` entry. Several hosts can share a name; edit the right `key`.
- Remove from the board: add `'board_hidden': True` (it still airs and still accepts uploads; auto-rerun then never fills its slot). Temporary hide: `'board_hidden_until': 'YYYY-MM-DDTHH:MM:SS'`. Show it again: delete the flag.
- Rerun slot of another show moves too if it shares that hour; check the board for clashes after the change.

## B. Queue (on rocky, `/home/roy/zerock-radio/schedule.json`)
1. List entries: `curl -s localhost:5000/api/schedule` and filter by `show_key` and `scheduled_time >= now`.
2. Broadcast time change: `POST /api/schedule/<id>/reschedule {"new_time":"YYYY-MM-DDTHH:MM"}`. It shifts the upload time and the paired rerun by the same delta and updates the WP post time.
3. Upload-time-only change has no API: back up `schedule.json` (`cp schedule.json schedule.json.bak-<reason>-$(date +%s)`), edit the entry's `upload_time` with Python, write atomically (temp file + `os.replace`).
4. Entries with no `rerun_id` (reruns created from a fixed rerun slot) do not shift when the primary moves: change their time separately if the rerun time changed.
5. Never edit entries that are `upload_in_progress` or already triggered.

## C. Deploy and verify
1. Deploy per `CLAUDE.md`, restart with **zerock-safe-restart** (it also re-syncs the WP board).
2. Verify with `GET /api/schedule` (new times) and by loading https://zerockradio.com/ (board). On the board desktop columns should be equal width; Saturday is a single-day group.

## Time zone
All schedule times are Israel local time (`Asia/Jerusalem`). Rocky's clock is Israel time; 192.168.1.114 runs UTC, so check `date` before comparing there.
