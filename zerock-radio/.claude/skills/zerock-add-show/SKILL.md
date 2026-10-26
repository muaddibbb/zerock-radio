---
name: zerock-add-show
description: Add a new show or a new host to ZeRock Radio (new upload form, schedule slot, WordPress show/slug/picture, board visibility). Use when asked to create an upload form, add a show or host, or give a show a new picture. Not for moving the time of an existing show (use zerock-change-show-schedule).
---

# Add a show / host

Everything below is in `radio_app.py` (repo root). Facts for each field are in memory `radio_schedule_and_board`.
Do not put credentials in this repo (it is public).

## 1. Collect (ask only for what is missing)
Show name (Hebrew, exact), host, broadcast day + time, upload time (when the file is pushed to Podbean/WP), rerun offset in days + rerun time, picture, and whether it should appear on the board now.

## 2. Edit `radio_app.py`
1. `SHOW_SCHEDULE`: new dict `{'key','name','broadcaster','day'(0=Mon..6=Sun),'time','upload_time','rerun_days_offset','rerun_time','wp_show_id'}`. Add `'board_hidden': True` to keep it off the board. A host sharing an existing show NAME reuses that show's `wp_show_id`.
2. `_WP_SLUGS[key]` (show term slug for the board link), `_SHOW_DURATIONS_H[key]` (hours on the board).
3. Only for a NEW show name: `_WP_SHOW_IDS[name]` (WP `shows` term id; check `GET /wp-json/wp/v2/shows?per_page=100`, create the term if absent), `_WP_SHOW_SLUGS[name]` (episode slug prefix, post slug = `<prefix>-ddmmyy`), `_WP_FEATURED_IMAGES[name]` (media id) and `_WP_SHOW_ART_URLS[name]` (picture URL for the homepage widget). `name` must match `SHOW_SCHEDULE` exactly.
4. Per-host episode slug (e.g. `forte-eran-DDMMYYYY`): `_WP_SLUG_BY_HOST[(name, broadcaster)] = (prefix, strftime_format)`. `_WP_BROADCASTER_IDS[host]` = WP broadcaster term id.
5. Hosts that share a name with another host: add the new key to `AUTO_RERUN_EXCLUDED`, otherwise an empty slot is filled with the latest episode of the same NAME by another host.
6. `templates/show_form.html`: if the show has no episode numbers (Forte), add the key to the list that hides that field.
7. Picture: upload to WP media (`POST /wp-json/wp/v2/media`, Basic auth with the app password that sits in the `zerock-radio-web` service environment: `systemctl show zerock-radio-web -p Environment`, never print it). Use an ASCII filename; set title/alt text. Put the returned id and `source_url` into the two picture dicts above.

## 3. Deploy and verify
Follow `CLAUDE.md` deploy workflow, then restart with **zerock-safe-restart**. Then check:
- Form: `curl -s -o /dev/null -w '%{http_code}' https://rocky.kupernet.com/<key-with-dashes>` is 200 (e.g. `forte_eran` -> `/forte-eran`).
- Board (if visible): load https://zerockradio.com/ and confirm the slot, or check `.schedule-show` entries for the name.
- Future queue entries are created when the host uploads; nothing to pre-create unless the first week needs a specific entry.

## Gotchas
- Board visibility also needs `POST /api/wp-sync` (the safe-restart helper does it).
- A new `board_hidden` show is never auto-filled; a visible one is. Check what auto-rerun would air in the new slot.
- Repair an already published episode: `POST /wp-json/wp/v2/episodes/<id> {"shows":[<term>],"slug":"<prefix>-ddmmyy"}`.
