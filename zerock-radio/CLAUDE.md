# ZeRock Radio

Internet rock radio station (zerockradio.com). Hebrew UI, English-speaking owner (Roy Kuperman).
This folder is the source of the radio server that runs on `rocky.kupernet.com`.

**This repo is PUBLIC and auto-pushed to GitHub every 5 minutes (launchd `com.zerock.push`).**
Never write passwords, API keys, app passwords or tokens into any file here, including this one.
Credentials live in Claude's private memory (`project_deployment`), not in the tree.

## What runs where
| Host | Role |
|---|---|
| `192.168.1.166` = `rocky.kupernet.com` | Main server. `zerock-icecast` (stream, :8000), `zerock-liquidsoap` (audio, `rocky.liq` + `rocky_transition*.liq`), `zerock-radio-web` (Flask `radio_app.py`, :5000), WhatsApp bridge (`~/whatsapp-bridge`, localhost:7733 only) |
| `192.168.1.114` | Secondary: n8n (docker, :5678 HTTPS), Caddy (HTTPS front for rocky), the old uploader (:3001, publishes to Podbean + WordPress), stream monitor cron. Reach it via `ssh -J` through rocky |
| NAS (DHCP, MAC `00:00:c0:06:98:bb`) | `/mnt/nas` music + show files. If its IP moves the stream dies; see `reference_radio_system` in memory |
| zerockradio.com | WordPress. REST only (no SSH). Schedule board is injected by `_sync_wp_board` |

Streams: main public stream is **https://rocky.kupernet.com/stream**. Also `https://icecast.live:8001/zerock`. Internal `http://192.168.1.166:8000/stream`.

## Files
- `radio_app.py` (about 12k lines): web app, `SHOW_SCHEDULE`, queue (`schedule.json` on the server), upload/publish pipeline, board sync, watchdogs.
- `templates/*.html`: pages. These take effect immediately, no restart.
- `rocky.liq`, `rocky_transition.liq`, `rocky_transition_cfg.liq`, `rocky_transitions*.py`, `rocky_loudness.py`: audio engine, smart crossfades (`/transitions` page), loudness.
- `*.service`: systemd units. `icecast.xml`: Icecast config.
- `.claude/skills/`: project skills (currently the WordPress "ארכיון" sweep).

## Deploy workflow (the only way code reaches the server)
1. Edit here. `python3 -m py_compile radio_app.py` locally.
2. `scp -P 22222 -i ~/.ssh/zerock_deploy <file> roy@rocky.kupernet.com:/home/roy/zerock-radio/<file>.new`, then compile on the server, back up the old file, `mv` into place.
3. Templates need nothing more. `radio_app.py` and `.liq` changes need a restart (below).
4. SSH always uses port **22222**: `ssh -p 22222 -i ~/.ssh/zerock_deploy roy@rocky.kupernet.com`.
5. Back up data files (`schedule.json` etc.) before editing them, write them atomically (temp file + `os.replace`).

## Restart rules
- Never restart `zerock-radio-web` while a show is on air, while an upload is running, or in minutes :00-:02 (shows trigger then).
- Use `~/restart_if_clear.sh <tag>` on rocky. It checks all of that, restarts, re-syncs the WP board (`POST /api/wp-sync`), WhatsApps Roy, and is safe to call repeatedly (marker file per tag).
- To run it later, schedule it from n8n (SSH node, credential "Rocky Radio SSH Password"), not as a sleeping script on rocky. Delete one-time workflows afterwards.
- Restarting the web app does not cut the audio. Restarting `zerock-liquidsoap` or `zerock-icecast` does, so only do it when the stream is already dead or the user asked.
- Ask before restarting unless the user's request was to make the change live.
- `needrestart` is configured not to auto-restart `zerock-*` services after package upgrades.

## Show schedule facts
- A show is an entry in `SHOW_SCHEDULE` (day 0=Mon..6=Sun, `time`, `upload_time`, `rerun_days_offset`, `rerun_time`, `wp_show_id`, optional `board_hidden`, `board_hidden_until`, `no_wp`).
- **Queue entries in `schedule.json` override `SHOW_SCHEDULE`** on the board and keep their old times. After changing a show's time, move already-queued entries with `POST /api/schedule/<id>/reschedule {"new_time":"YYYY-MM-DDTHH:MM"}`. That API shifts upload time and the paired rerun but cannot change upload time alone (edit `schedule.json` for that).
- Auto-rerun fills any slot without an upload with the latest Podbean episode of the same show NAME (any host). Add a show to `AUTO_RERUN_EXCLUDED` when that would air the wrong host. Hidden shows are never auto-filled.
- Adding a show touches: `SHOW_SCHEDULE`, `_WP_SLUGS`, `_SHOW_DURATIONS_H`, `_WP_SHOW_IDS`, `_WP_FEATURED_IMAGES` (media id), `_WP_SHOW_ART_URLS` (homepage widget picture), `_WP_BROADCASTER_IDS`, optionally `_WP_SLUG_BY_HOST` for per-host episode slugs. The form is served at `/<show-slug>`.
- Board changes (hiding, new times, CSS) need a restart plus `POST /api/wp-sync` (the restart helper does both).

## Checking state
- On air: `curl localhost:5000/api/on-air` on rocky. `"show": true` means a broadcaster show is playing (not just Rocky rotation).
- Logs: `/home/roy/zerock-radio/logs/web.log` (no timestamps) and `logs/liquidsoap.log`. `journalctl` is empty for the app.
- Stream health: the monitor on 114 (`~/stream_monitor/check.sh`, cron each minute) alerts to WhatsApp "Note to Self" and auto-heals. Check `~/stream_monitor/monitor.log` on 114.
- WhatsApp to Roy's own number is redirected to the group "Note to Self" by `~/whatsapp-bridge/routes.json`.

## Conventions
- Match the surrounding code: Hebrew show names as in `SHOW_SCHEDULE`, inline comments only for non-obvious reasons, dated comments for temporary decisions.
- Test audio changes with the bounded renders (`rocky_transition_preview.py`), never an unbounded render (it filled the disk once).
- Temp-file cleanup: only delete files in `shows/` or `/mnt/nas/Music/ZeRock_Temp` that no `schedule.json` entry references and are older than 14 days.

## Known open items
- Secrets are hard-coded in deploy scripts, `rocky.liq` and local settings. Fix before any real version control.
- NAS has no DHCP reservation yet.
- radio-browser.info merge request !424 pending; TuneIn re-validation pending.
