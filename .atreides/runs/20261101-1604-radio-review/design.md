# Design: ZeRock Radio review and onboarding (run 20261101-1604-radio-review)

Author: Thufir. Read-only run. No secret values appear in this document, only file:line and the kind of secret.

## Urgent points and corrections (read first)

**Rakevet Laila tonight (upload 22:30):** the episode will publish with no show tag (`shows: []`). That part is certain.
- The "Hebrew slug" prediction is not certain. A copy of the 114 uploader is tracked at the repo root (`/Users/rkuperman/zerock-sync/server.js`), and it maps רכבת לילה to `night-train`. If 114 runs that version, the slug comes out as `night-train-011126`, with old featured image 3028 instead of 15675.
- Changing `schedule.json` cannot fix this. The code fix needs a restart, which is not allowed during the show.
- Safe path with no restart: after about 22:40, set the new episode's show tag (161), slug and image through the WordPress REST API, and do the same for episode 15676. The verifier will not undo it.

**New bug, due Thursday 2026-11-05 15:00:** the weekly chart renewal hard-codes UTC+3 (`radio_app.py:10355-10366`). Israel has been on UTC+2 since 2026-10-25, so the new poll's open time lands an hour in the future. Within a minute the close watcher (:3655-3670) marks the new poll closed, switches the WordPress vote snippet 55 to "voting closed" (nothing reopens it), and sends a false results message to the chart WhatsApp group. This may already have happened on 2026-10-29. The code reading is solid; the live effect is unconfirmed because I assumed the server runs on Israel time and did not see `polls.json`.

**Reviewer claims corrected:**
- The "startup auto-clear" item is not a wrong note. The memory note already says it was never added.
- The Eran episode's verify on the main upload path does pass the broadcaster (:2179). The two other verify calls (:1776, :2980) leave it out and switch the slug back to `forte-DDMMYY`.
- `html.escape` is never called anywhere (reviewer 2 is right; reviewer 4's two hits are `unescape`).
- The chart close time is fine; it is the open time that breaks.

---

**Sources:** the 5 reviewer reports in `inputs/` (R1 core pipeline, R2 WordPress, R3 audio engine, R4 security, R5 parity/health), CLAUDE.md, the memory notes, and my own spot checks of the code at HEAD `9ab1f23` of `/Users/rkuperman/zerock-sync`.
- HEAD equals origin/main, and the zerock-radio folder has no uncommitted changes.
- `radio_app.py` is 12,334 lines. All six `.py` files compile (checked in memory, nothing written).
- All line numbers below refer to `/Users/rkuperman/zerock-sync/zerock-radio/radio_app.py` unless another file is named.

## 0. Problem and acceptance criteria
1. A project map a new developer can work from (section 1).
2. Ranked findings, merged across reviewers, each marked VERIFIED (code read) or SUSPECT (depends on live state I could not see), with reviewer contradictions resolved (section 3).
3. A production-vs-repo parity and server health summary (section 1.7, from R5).
4. What the public repo exposes and what must be rotated (finding F1).
5. A fix backlog, not implemented (section 5).
6. Nothing modified.

## 1. Project map

### 1.1 Hosts

| Host | What runs there |
|---|---|
| rocky `192.168.1.166` (rocky.kupernet.com), app dir `/home/roy/zerock-radio` | `zerock-icecast` (:8000), `zerock-liquidsoap` (`rocky.liq` + `rocky_transition*.liq`), `zerock-radio-web` (Flask `radio_app.py`, `app.run(0.0.0.0:5000)` at :12334), WhatsApp bridge (127.0.0.1:7733), cron jobs (`backup_schedule.sh`, WhatsApp `backup_auth.sh`, `send_vote_wa.sh` on Thursday). Two legacy services from `~/zerock-stream` are still running: `zerock-rocky` and `zerock-web` (:8080). |
| `192.168.1.114` | Caddy (HTTPS front: `rocky.kupernet.com` → 166:5000, `/stream` → 166:8000), the uploader (Node, :3001: Podbean + WordPress post), n8n (:5678), the stream monitor cron. Not inspected in this run. |
| NAS `//192.168.1.198/public` at `/mnt/nas` | Music, jingles, show files (`NAS_TEMP` at :21). Its IP is fixed by a DHCP reservation. |
| zerockradio.com | WordPress, reached by REST only: page 254, options, Code Snippets 23/55/58, the `zr/v1/np` route. |

### 1.2 Life of a show

**1. Config**
- `SHOW_SCHEDULE` (:358-392) holds the weekly slots.
- Show-name maps:
  - `_WP_SHOW_SLUGS` :87
  - `_WP_SLUG_BY_HOST` :111 and `_wp_episode_slug` :114
  - `_WP_FEATURED_IMAGES` :122
  - `_WP_SHOW_ART_URLS` :153
  - `_WP_SHOW_IDS` :182
  - `_WP_BROADCASTER_IDS` :213
- Other lists: `AUTO_RERUN_EXCLUDED` :298 and `NEVER_UPLOAD_SHOWS` :301.
- Show-key maps: `_WP_SLUGS` :4897 and `_SHOW_DURATIONS_H` :4931.

**2. Upload**
- The form is served at `/<show-slug>` (catch-all route at :4107).
- `api_add_show` (:5877) replaces any not-yet-aired entry for the same slot. It appends the show and its rerun (built by `_make_rerun_entry` :458) to `schedule.json`.
- `load_schedule`/`save_schedule` (:491/:513) write atomically under a lock.
- `_move_to_nas` (:527) copies the file to the NAS. Publishing waits for `_upload_time_reached` (:419).

**3. Publish**
- `_upload_and_mark_done` (:2128) calls `_do_podbean_wp_upload` (:1914), which POSTs to the uploader on 114 (`UPLOADER_URL` :78).
- It sends `wpShowId` and `wpBroadcasterId` (:1996-1997) and authenticates with a cookie derived from a literal password (:303).
- After 60 s, `_verify_and_fix_wp_post` (:1787) checks and repairs the WordPress post. The WhatsApp notice goes out only after verification passes.
- Fallbacks: `_create_wp_post_direct` (:1653) creates the post directly, and `_check_wp_posts` (:2829) is a 30-minute safety net.

**4. Air**
- `scheduler_loop` (:2985, started at :3332) runs every 15 s. It triggers shows from 10 minutes early to 45 s late, or up to 6 h late with resume (:3027).
- `trigger_show` (:1389) pushes the show to Liquidsoap over telnet (`_lq_connect_send` :547).
- In the same pass, the loop:
  - queues reruns (:3102) and retries failed uploads (:3125)
  - merge-saves its own changes (:3154-3178)
  - runs `_check_auto_reruns` (:2651), which at T-60 min fills an empty slot with a **random** past Podbean episode (:2555 → :1645)
  - runs `_check_wp_posts`, then file cleanup (:3195-3305)

**5. Board**
- `_build_wp_schedule_slots` (:5005) builds the board, `_build_wp_schedule_html` (:5332) renders it, and `_sync_wp_board` (:5534) pushes it to the `zerock/v1/schedule` route, two WordPress options, page 254, and the `ihaf_insert_footer` option.
- It syncs:
  - Saturday midnight, plus the n8n job on Saturday 18:00
  - on `POST /api/wp-sync` (:6661)
  - when a booking changes
- Snippets 23/58 and the injected `zerock-fix` script drive the homepage "now broadcasting" box.

### 1.3 Audio chain
1. `rocky_loudness.py` and `rocky_transitions.py` analyse each song once and write tagged `*_liq.m3u` playlists.
2. In `rocky.liq`, five music sources go through amplify → limit → mksafe (:50), plus a jingle source (:59).
3. `rotate` plays English/Hebrew/jingle in turn, through the smart crossfade `cross(rocky_smart_xfade)`.
4. Then `fallback([zikaron_switch, rocky])` (:101) and `blank.skip` (:110).
5. `switch([shows, background])` with a watcher that restarts music cleanly after a show.
6. `mksafe` (:165), then `now_playing.txt`, then two Icecast outputs (local :181, icecast.live :199).

The `shows` queue lives only in Liquidsoap's memory, so a Liquidsoap restart loses it.

### 1.4 Data files (on rocky)
`schedule.json` (2.3 MB, 1,443 entries), polls/votes/codes, Al Haroker and Erev Albumim bookings, excluded tracks, the transitions and loudness caches, new releases, unsubscribes. Only `schedule.json` is written atomically. Most loaders return `[]` on a parse error (R1-10).

### 1.5 Watchdogs and background threads
- In the app: `scheduler_loop`, `_weekly_poll_renew_loop` :3412, `_poll_close_watcher` :3641, `_queue_updater` :3845, `_wa_bridge_watchdog` :4650, `_local_temp_watchdog` :4710, `_listener_stats_collector` :4570, `_rocky_enforcer` :1208, `_nightly_rebuild_loop` :3339, the booking reminder loops, `_bounce_checker` :8101, and the new-releases poller (:12000).
- Outside the app: the stream monitor on 114 checks every minute and heals rocky through a forced-command key that runs `stream_remote.sh`.

### 1.6 Deploy and safe restart
1. Compile locally.
2. `scp -P 22222` the file as `<file>.new`, compile it on the server, back up the old file, `mv` the new one into place.
3. Templates take effect immediately; `.py` and `.liq` changes need a restart.
4. Restart only with `~/restart_if_clear.sh <tag>` (it exists only on the server). It refuses while a show is on air, while an upload is running, or before minute :03, and afterwards runs wp-sync and sends a WhatsApp.
5. Never restart Liquidsoap or Icecast while the stream is healthy. Schedule delayed one-off jobs through n8n.

### 1.7 Parity and health (from R5)

| Item | State |
|---|---|
| radio_app.py, `.liq` files, `rocky_*.py`, 15 templates, icecast unit, `send_vote_wa.sh` | Identical to production |
| `zerock-liquidsoap.service`, `zerock-radio-web.service` | Differ: production adds the NAS-mount dependency, `PYTHONUNBUFFERED`, and about 14 credential `Environment=` lines, in a world-readable (644) file |
| Snippets 23/58 and the `zr/v1/np` route | 23 is older in the repo; 58 and `zr/v1/np` are missing from the repo |
| On the server only | `restart_if_clear.sh`, `safe-restart.sh`, `backup_schedule.sh`, `stream_remote.sh`, `whatsapp-bridge/`, about 25 one-off scripts, a stray `templates/radio_app.py` |
| Repo-root `server.js` (new) | A tracked copy of the 114 uploader from 2026-08-09. It reads credentials from the environment only, and is probably stale: no Rusty Wounds entry, and רכבת לילה uses image 3028 where radio_app uses 15675 |
| Health | All services active. Disk is 74% used: `web.log` 3.0 GB, `liquidsoap.log` 410 MB, journal 2.4 GB, no log rotation. 82 stale queue entries. A stray test server `/tmp/trweb_test.py` listens on 0.0.0.0:5099. A reboot is pending. |

## 2. Claim check (CLAUDE.md, project skills, memory notes)

| # | Claim (where) | Verdict and evidence |
|---|---|---|
| C1 | Auto-rerun plays the "latest" episode (CLAUDE.md:45, skill zerock-add-show:19, memory schedule_and_board:57) | **Wrong.** `random_choice=True` (:2555) picks at random from every matching past episode (:1645-1648). |
| C2 | Reschedule shifts the paired rerun (CLAUDE.md:44, memory :49, skill change-show-schedule:18) | **Wrong.** :6230 reads `rerun_id`, which is never assigned anywhere (:6141 only sets `rerun_scheduled`). Skill line 20 implies some entries have a `rerun_id`; none do. |
| C3 | "Edit schedule.json to change only the upload time" (CLAUDE.md:44) | **Misleading.** The upload gate (:419-431) uses the `SHOW_SCHEDULE` upload time. The entry's own `upload_time` is read only by cleanup (:3200) and reschedule. |
| C4 | A startup auto-clear of stuck uploads exists | **Not claimed.** memory schedule_and_board:16 already says "NOT yet added". R1 misread the note; nothing to fix in the docs. |
| C5 | The per-host slug is "used by publish + verifier" (memory :58) | **Misleading.** Only :2179 passes the broadcaster. :1776 and :2980 do not, so they switch the slug back (F11). |
| C6 | rocky.liq passwords are "historical" | **That phrase is not in the current notes.** The opposite is true: the passwords are in HEAD. CLAUDE.md:8 ("credentials live in memory, not the tree") is **wrong**. CLAUDE.md:61 ("fix before real version control") is **misleading**: the repo is already public. `project_open_issues` lists only part of the exposure. |
| C7 | Add-show checklist (CLAUDE.md:46) | **Incomplete.** It is missing `_WP_SHOW_SLUGS`, the `wp_show_id` field, and the uploader's own maps on 114. This is how רכבת לילה broke. |
| C8 | `/api/on-air` returns `"show": true` (CLAUDE.md:50, skill safe-restart:18) | **Imprecise.** The response is `{show_playing, on_air:{show}}` (:4376-4390). |
| C9 | The public stream is icecast.live and `rocky.kupernet.com/stream` returns 404 (memory infra:17) | **Stale.** Line 59 of the same note and CLAUDE.md:18 say the HTTPS stream on rocky.kupernet.com works. |
| C10 | The chart closes "next Wednesday 19:00" (memory chart:17) | **Wrong.** It closes Tuesday 19:00, with a hard-coded +03:00 offset (:10355-10366). See F7. |
| C11 | zerock-rocky and zerock-web are the "music manager" and "aux web" (memory infra:29) | **Misleading.** Both are legacy and log errors every few seconds. |
| C12 | `SHOW_SCHEDULE` is at "~line 233" (memory :13) | **Stale.** It is at :358. |

## 3. Verified findings (merged across reviewers)

### P0

**F1. Secrets in the HEAD of a public, auto-pushed repo** (raised by R1–R5). VERIFIED. Effort: M; the code part is small, but rotation and the history purge are customer decisions.
- Locations:
  - WordPress app password at `radio_app.py:4893`; it overrides the environment value set at :84.
  - Login password literal at :303. The uploader cookie is derived from it, and the 114 uploader accepts the sha256 of this password (`server.js:22`).
  - The `/logs` token at :4248 (derived from the same password).
  - Poll-results password at :9711, Spotify id and secret at :245-246.
  - Icecast passwords at `rocky.liq:181,199` and `icecast.xml:17,18,20`.
  - An admin credential URL in `templates/index.html:1424-1425`, served at `/`.
- History: present since the first commit `f516fd1` (2026-04-23).
- Other exposure: there is no `.gitignore` in zerock-radio. Credentials also sit in the world-readable unit file, in plain text in the memory notes, and partly in R5's transcript.
- Fix: move to env or untracked files, delete :4892-4893, rotate everything, purge history or make the repo private, add a secret scan to `sync.sh`.

**F2. No authentication layer** (R1–R4). Code VERIFIED; exact Caddy routing SUSPECT. Effort: M.
- There is no `before_request` hook and no auth decorator on any of the 111 routes or `/api/transitions/*`. The app listens on 0.0.0.0 and Caddy makes it public.
- Anyone can control the stream, upload and publish shows, delete schedule entries, close polls, and read subscriber emails and booking tokens.
- `/api/schedule-url` (:6334) also allows SSRF: it fetches any URL and its response can be read back.
- Fix: one auth gate with an allowlist of public routes, URL validation, and an upload size cap (`MAX_CONTENT_LENGTH`).

**F3. Stored XSS on zerockradio.com** (R2, R4). VERIFIED. Effort: S.
- Show and broadcaster names go into the board HTML unescaped (:5514-5524).
- Those values come from the unauthenticated registration forms (:7317, :7640, via :5188/:5146) and are pushed with admin rights into page 254 and the footer of every page.
- Fix: escape all dynamic values (also :1687-1695) and validate the registration fields.

### P1

**F4. רכבת לילה is missing from the WordPress maps** (R1, R2). VERIFIED. Effort: S.
- It is missing from `_WP_SHOW_IDS`, `_WP_SHOW_SLUGS` and `_WP_SLUGS`, and its `wp_show_id` is empty, so every episode publishes with no show tag (:1996). Episode 15676 is the proof. See section 4.

**F5. Cleanup changes are never saved** (R1, R5). VERIFIED. Effort: S.
- Cleanup (:3195-3305) runs after the only save (:3154-3178), so its changes are lost every pass. This is behind a log loop of more than 42k lines.
- If a failed upload's file still exists, a new upload thread starts every 15 s, which can create duplicate Podbean/WordPress episodes.
- Also, a successful upload never clears `upload_failed` (:2142-2149).

**F6. `upload_attempts: null` aborts the whole scheduler pass** (R1). Logic VERIFIED; how a null gets into the file is SUSPECT (only hand edits, per memory). Effort: S.
- The comparison at :3129 sits outside the inner try, so the error skips the save, and a show triggered in that pass is triggered again every 15 s. The same pattern is at :2210.

**F7. New: the winter-time chart renewal closes the new poll.** Code VERIFIED; runtime SUSPECT. Effort: S. Next renewal: Thursday 2026-11-05 15:00.
- The renewal (:10355-10366) sets the poll's open time an hour ahead of the clock.
- The close watcher (:3655-3670) then closes the new poll, switches WordPress snippet 55 to "voting closed" (nothing reopens it), and sends a false results message to the chart WhatsApp group.
- Fix: use the Asia/Jerusalem time zone.

**F8. Logs grow without limit** (R3, R5). VERIFIED. Effort: S.
- There is no log rotation, and :3017 prints a line every 15 s for each pending entry.

### P2

| ID | Finding | Status, effort |
|---|---|---|
| F11 | Per-host slug reverted by the two verify calls that omit the broadcaster (:1776, :2980). Eran's episode 15685 is affected (R2). | Mechanism VERIFIED, cause SUSPECT; S |
| F12 | `rerun_id` never set, so reschedule leaves the rerun behind (R1). | VERIFIED; S |
| F13 | Pass 3 of `_check_wp_posts` never runs, because of an early return at :2934 (R1, R2). | VERIFIED; S |
| F14 | The verifier reports success with no Podbean link (:1896). New: its fallback (:1859-1866) attaches the latest episode by show name, which is the wrong audio for shared names (Forte, פטרוק). | VERIFIED; S |
| F15 | The upload-in-progress flag can get stuck: :1933 is outside any try and nothing clears it on startup (R1). | VERIFIED; S |
| F16 | Stale board slugs give 404 links: erev_albumim, matzad_harok, on_air (:4925-4926, :4906; R2). | VERIFIED; S |
| F17 | WordPress robustness: post creation not idempotent, no alert when board sync fails, the footer option is overwritten whole and races :8885 (R2). | Reviewer-verified; M |
| F18 | No dead-air fallback when the NAS drops (R3). | VERIFIED by reading; M |
| F19 | Transition settings accept 0: divide-by-zero (`rocky_transitions_web.py:49`, `.liq:252`) or a muted tail (`web.py` :196-203, `.liq` :131) (R3). | VERIFIED; S |
| F20 | Data hygiene: delete removes files a rerun still uses (:6404-6408), non-atomic JSON writes, a cached "failed" analysis state, the overrides file wiped on bad JSON. | Reviewer-verified; M |
| F21 | Repo drift: snippets and the `np` route missing, unit files differ, server-only scripts, stale `server.js`. | Partly new; M |

### P3
Each of these was verified by a reviewer or by me:
- Duplicate code: `_resolve_broadcaster` is defined twice (the second copy crashes on None); duplicate fixed-host maps.
- Board/slug: the shared `patrock` slug prefix collides between two shows.
- Audio: seek drops the rest of the show; the telnet "ERROR" check is a loose substring match; `now_playing.txt` is written non-atomically; `audioop` breaks on Python 3.13.
- Security: reflected XSS at :8778; vote codes can be brute-forced (:10621).
- Ops: the stray :5099 server and the legacy services.

## 4. Time-sensitive (Sunday 2026-11-01)

**Tonight: רכבת לילה airs 21:00-23:00 and uploads at 22:30.**
- It will publish with `shows: []`.
- The slug is unclear: if 114 runs the same uploader version as the repo copy, it will be `night-train-011126` with the old image 3028.

**The code fix** (one deploy):
- `_WP_SHOW_IDS['רכבת לילה'] = 161`
- `_WP_SHOW_SLUGS['רכבת לילה'] = 'night-train'`
- `_WP_SLUGS['rakevet_laila'] = 'night-train'`
- `wp_show_id: '161'` in `SHOW_SCHEDULE`
- Align `server.js` on 114 to image 15675.
- In WordPress: term 161 already exists, but its ACF fields should be checked. Then run wp-sync.

**What is safe tonight:**
- Editing `schedule.json` cannot help, and no restart is allowed between 21:00 and 23:00.
- Recommended: after about 22:40, POST `/wp/v2/episodes/<new id>` with `{"shows":[161],"slug":"night-train-011126","featured_media":15675}`, and do the same for 15676 with slug `night-train-251025`. The verifier will not undo this.
- Alternative, if the customer approves: a gated deploy and restart between 19:15 and 20:50, after Pascal's 19:00 upload finishes.

**Before Thursday 15:00:** ship the F7 fix. Also check whether the current poll shows `open:false`, whether snippet 55 says "closed", and whether a false results message went out on 10-29.

## 5. Fix backlog, in recommended order

| # | Run | Scope | Risk, how to verify | Decision needed |
|---|---|---|---|---|
| A | Tonight, ops only | REST repair of the רכבת לילה episodes; check the F7 state | Low; GET the episodes afterwards | Approve the WordPress writes |
| B | Hotfix | F4 maps + F7 time zone | Low; gated restart before Thursday | Slug prefix `night-train` |
| C | Secrets | Move to env or untracked files, delete :4892-4893, scrub index.html, add `.gitignore` and gitleaks to `sync.sh`, chmod 600 the unit file | Icecast rotation cuts audio, so do it in a gap; pause auto-push during any history rewrite | Rotation; private repo or history rewrite with force-push |
| D | Auth layer | Gate admin routes; keep `/stream`, voting, nowplaying and the booking/upload-token pages public; SSRF guard; upload size cap; HMAC sessions | Medium: can break n8n, the monitor and the WordPress JS; list all callers first | Auth approach and public route list |
| E | Escape/XSS | F3, :8778, admin templates, registration validation | Low | — |
| F | Scheduler persistence | F5, F6, F12, F13, F15 | Medium; test on a copy of `schedule.json` | — |
| G | Verifier and maps | F11, F14, F16, F17 | Low-medium | Link targets for Erev and Matzad |
| H | Logs | Log rotation, throttle the scheduler print, clean the 82 stale entries | Low | — |
| I | Repo parity | Snippets, unit-file templates, server scripts, 114 `server.js` into the repo | Low | Is 114 in scope? |
| J | Audio robustness | F18, F19, cache expiry, overrides lock | Medium (changes the on-air sound) | Approval |
| K | Ops cleanup | Stop the :5099 server, retire the legacy services, reboot, update the docs (section 2) | Low | Reboot window |

**Test outline for Gurney:**
- Compile and AST checks only.
- A script that checks every show is in every WordPress map.
- Confirm there is no `before_request` and no `html.escape` in :5332-5534.
- Count secret patterns in HEAD, never printing values.

## 6. Open questions for the customer
1. Do you approve rotating all the credentials? And do you want the repo made private, or its history rewritten and force-pushed?
2. Should authentication sit in front of the app (Caddy) or inside it (Flask), and which pages must stay public?
3. Tonight: do you approve the REST repair after the upload, or would you rather have a gated restart between 19:15 and 20:50?
4. Should the רכבת לילה slug prefix be `night-train`? Should the Erev and Matzad links go to `album-night` and `chart`?
5. Is the uploader on 114 (`server.js`) in scope, and is the repo-root copy its source?
6. Should auto-rerun stay random, and should hosts that share a show name be excluded from it?
