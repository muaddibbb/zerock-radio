# Reviewer 3: audio engine + transitions (read-only; liquidsoap not installed locally: .liq read carefully only; server runs Liquidsoap 2.2.4, Python 3.12.3). All 9 files in this area have md5 identical to live copies.

## Chain
1. rocky_loudness.py + rocky_transitions.py measure each library song once with ffmpeg (loudness, ending/opening analysis), cached in JSON by size+mtime.
2. They write english_liq.m3u / hebrew_liq.m3u as `annotate:liq_amplify=...,tr_*=...:path`; Liquidsoap polls hourly; app can force `src_*.reload`.
3. rocky.liq: 5 music sources (amplify -> limit -> mksafe) + jingle source; `rotate` plays E E H E H J.
4. `cross(rocky_smart_xfade)`: `rocky_level_xfade` meters the outgoing song's real RMS and starts the next once the level has fallen; jingle path always 2 s fade; rules engine `tr_decide` only for per-song overrides or mode="rules"; `tr_announce` fixes now-playing label.
5. `rocky` -> fallback([zikaron_switch, rocky]) -> blank.skip (4 s silence skips the track).
6. main = switch([shows, background]); on_frame watcher skips background when a show ends and sets `rocky_resume_skip_t` so the stale tail is dropped.
7. main -> on_metadata writes now_playing.txt -> mksafe; two output.icecast (local + icecast.live) toggled by interactive.bool.
8. Flask talks to Liquidsoap on telnet 127.0.0.1:1234: shows via `shows.flush_and_skip` + `shows.push`; /api/skip sets `tr_skip_t` then `rocky_out.skip`; /transitions sliders use var.set.
9. /transitions routes edit overrides then rewrite playlists; preview renders offline with the same .liq. 10. `_queue_updater` polls telnet for the label; the `shows` queue lives in Liquidsoap memory and is lost on a Liquidsoap restart.

## Findings
HIGH
- No authentication on control routes (verified): no before_request, app binds 0.0.0.0:5000 (radio_app.py:12334): /api/skip (6528), /api/stream/stop (4404), /api/queue-file (6631), /api/liquidsoap/restart (4463), /api/transitions/settings|override|preview (rocky_transitions_web.py:83,173,220). Internet reachability depends on Caddy on 114 (not checked, suspect). Fix: shared token or restrict Caddy to /stream.
- Hard-coded secrets in public repo (verified): rocky.liq:181, :199 Icecast source passwords; radio_app.py:303 and :4248 login password literal from which auth token and logs token derive. Fix: env file / untracked include.
- Unrotated logs, disk 74% (verified): web.log 3.0 GB, liquidsoap.log 410 MB (6.5M lines; in the last 30k lines 28,650 were `[server:3]` telnet connect lines from polling). No logrotate for zerock. Fix: logrotate copytruncate, cut telnet polling.
MEDIUM
- `abrupt_fade=0` gives 0/0 (verified by reading, not run): server allows 0 (rocky_transitions_web.py:49); rocky_transition.liq:252-253 divides by fl(); first frame NaN gain into encoder. Fix: min 0.1 or guard fl()<=0.
- `out_fade=0` mutes the whole tail (verified by reading): validation accepts 0 (rocky_transitions_web.py:197-203); `tr_ramp_out` (rocky_transition.liq:131) dur<=0 -> u0=1: silent for the whole window. UI min 0.5 but API does not enforce.
- No dead-air fallback if the NAS drops: every source and jingle is NAS-only; mksafe falls back to endless blank; blank.skip loops. Mount is cifs soft. Fix: local emergency file as last fallback instead of blank.
- Transient failures cached forever: rocky_transitions.py:416-417 and rocky_loudness.py:200-202 store `failed: True` on any ffmpeg failure incl. NAS blip; entry counts as valid; song plays un-normalized and is treated like a jingle until size/mtime changes. Today 2 failed transition entries, 1 failed loudness. Fix: retry/expiry.
- Seek drops the rest of the show (verified by reading): /api/seek (radio_app.py:6590) does flush_and_skip and pushes only the trimmed file; closing jingle and remaining album tracks are lost.
- Overrides file: `load_overrides` returns {} on bad JSON so the next save wipes every override (rocky_transitions.py:282-296); read-modify-write has no lock (rocky_transitions_web.py:182-211); `bool("false")` is True (:205); a dict/list for `path` raises 500 (:156,180,226); playlists rewritten but Liquidsoap not reloaded: change only airs at the hourly poll, and a reload re-shuffles the rotation.
LOW
- Preview has no size cap: bounded only by finite source and 150 s timeout (rocky_transition_preview.py:122-123; web timeout also 150 s, rocky_transitions_web.py:243); subprocess.run kills only the direct child, so a web timeout leaves orphaned liquidsoap/ffmpeg and trprev_* temp dirs (none now). Fix: RLIMIT_FSIZE + process-group kill.
- Telnet client: success judged by substring "ERROR" (radio_app.py:1539,6621,6641); not checked what an unresolvable shows.push replies (suspect failed push counted as success); missing END not an error so responses can slip out of step (:547-583, :3804-3843); try/except around lq_send_direct at :6533 is dead code (never raises).
- `now_playing.txt` written non-atomically (rocky.liq:159); reader can catch it empty and show "Rocky" for one poll (radio_app.py:648-663).
- `max_overlap` above the cross window does nothing (window capped 12 s + silence; slider to 20).
- Latent: `audioop` (rocky_transitions.py:27) removed in Python 3.13 (rocky_loudness.py hides the failure silently); duration filter (rocky.liq:40-45) probably dead code, risky if a ms `duration` tag appears; blank.skip on background may cut the quiet Zikaron jingle (suspect); external Icecast output shares a clock with the local one so an unreachable icecast.live could stutter the local stream (suspect); frame length 0.04 s hard-coded in several timing counters; `[Scheduler]` line logged thousands of times in web.log.
FINE: no shell=True, list-based ffmpeg args with absolute paths, paths checked against the library set, preview file id hex regex; config/cache/playlist writes atomic (os.replace), NaN rejected by settings bounds; telnet bound to localhost; template escapes names and `why`; show-resume flip logic sound; caches no stale entries (1135 transition entries, 0 missing files); 924 tracks, 923 annotated, none with ':' or '"' in the path.
