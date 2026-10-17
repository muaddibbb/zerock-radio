---
name: wp-archive-untagged-episodes
description: Sweep zerockradio.com for episodes that have no "Show" tag, export the list to a text file, and bulk-tag them with the show "ארכיון" (archive, slug `archive`). Use when asked to find untagged/orphan WordPress episodes, list episodes without a show, or tag/archive them.
---

# Sweep WordPress episodes without a Show tag → tag them "ארכיון"

Done once on 2026-10-17: 9,174 published episodes checked, 1,075 had no Show (2021–2026, mostly old
On Air / uncategorised uploads). All 1,075 were tagged with the `shows` term **ארכיון** (id 328, slug
`archive`, page https://zerockradio.com/shows/archive/). Only the `shows` field was changed
(date, status, content untouched).

## How it works
- Episodes are the WP custom post type `episodes` (`/wp-json/wp/v2/episodes`); the Show tag is the
  `shows` taxonomy (`/wp-json/wp/v2/shows`). An episode "has no Show" when its `shows` array is empty.
- **Listing needs no login** (public REST API, 100 per page, ~92 pages ≈ 1–2 min).
  It only sees *published* episodes; drafts/private posts need the admin login (not checked in the first sweep).
- **Tagging needs the WP admin app-password**, which only lives in the `zerock-radio-web` process
  environment on rocky (`WP_USERNAME`, `WP_APP_PASSWORD`). The script reads it from `/proc/<MainPID>/environ`;
  never print it. So `tag` runs **on rocky** (`ssh -p 22222 -i ~/.ssh/zerock_deploy roy@rocky.kupernet.com`).
- Tagging is sequential (0.25 s pause, ≈ 0.6 s per episode → ~11 min for 1,075), re-reads each episode before
  writing (skips ones that got a show meanwhile), stops on 401/403/429, and is resumable via `/tmp/archive_done.json`.

## Steps
1. **List** (local or anywhere): `python3 scripts/wp_untagged_episodes.py list episodes_without_show.txt`
   → tab-separated file (ID, date, title, public link, WP edit link), newest first. Give the user the file/counts.
2. **Confirm the target term exists** — `GET /wp-json/wp/v2/shows?slug=archive` (the script checks and aborts
   if it is missing; create the term in WP admin first, do not guess another one).
3. **Test on one episode first**: copy the script to rocky (`scp -P 22222 -i ~/.ssh/zerock_deploy`) and run
   `python3 wp_untagged_episodes.py tag --limit 1`, then verify publicly:
   `curl -s "https://zerockradio.com/wp-json/wp/v2/episodes/<id>?_fields=id,shows"` → `"shows":[328]`.
4. **Run the rest in the background** on rocky: `setsid nohup python3 wp_untagged_episodes.py tag > /tmp/tag_archive.log 2>&1 &`
   and watch `/tmp/tag_archive.log` for `FINISHED ok=N skipped=N failed=0` (don't poll in a tight loop).
5. **Verify**: re-run `list` — it should report 0 (or only episodes published since). Spot-check a few and the term
   count on `/wp-json/wp/v2/shows/328?_fields=count`.

## Notes / gotchas
- This is a bulk write to the live site: only do it when the user explicitly asks; the first run was done after the
  user said "All these 1075 shows add the show tag ארכיון".
- Normal uploads from the radio app always set a show (`_WP_SHOW_IDS`, see the board/queue gotchas memory); newly
  untagged episodes usually mean a show missing from those maps — fix the map rather than just re-archiving.
- An episode can carry several shows; this skill only touches episodes with **none**.
