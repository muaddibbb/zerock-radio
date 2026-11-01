# Reviewer 5: deployed-vs-repo parity + server health (read-only; only write: a throwaway /tmp py_compile copy, deleted)
Secret exposure note: the reviewer's sed redaction of /etc/systemd/system/zerock-radio-web.service was incomplete, so some values (partial WP app password, Podbean/Spotify client IDs) appeared in ITS tool output/transcript; not repeated in its report. Consider rotating the WP app password (already advised by the security review).

## Production layout
1. rocky 192.168.1.166, Ubuntu, up 26 days; app dir /home/roy/zerock-radio; services zerock-icecast (:8000), zerock-liquidsoap (rocky.liq), zerock-radio-web (:5000): all active+enabled, NRestarts=0, Restart=on-failure.
2. Legacy services still running from ~/zerock-stream: `zerock-rocky` and `zerock-web` (:8080), Restart=always; `zerock-nas-mount` oneshot runs /usr/local/sbin/zerock-nas-mount.sh.
3. NAS //192.168.1.198/public at /mnt/nas (fstab also /mnt/mycloud, /mnt/truenas; only /mnt/nas mounted).
4. State in JSON files in the app dir; schedule.json 2.3 MB / 1443 entries.
5. Cron on rocky: backup_schedule.sh every 15 min (48 snapshots, ~106 MB), WhatsApp backup_auth.sh every 15 min, send_vote_wa.sh Thu 15:00.
6. WhatsApp bridge /status ready:true.
7. Restart helpers restart_if_clear.sh, safe-restart.sh + ~25 leftover one-off restart_*/scheduled_*/lq_* scripts.
8. Stream monitor forced-command key present; /etc/needrestart/conf.d/zerock.conf exists. Host 114 not checked.
9. radio_app.py deployed Oct 25 21:26; web process started later that night (running deployed code); icecast+liquidsoap running since Oct 23 06:41.
10. Last shows today (zifim 09:59, stage_dive 11:59, black_parade 12:59) on time.

## Parity (repo vs server)
Identical: radio_app.py, rocky.liq, rocky_transition.liq, rocky_transition_cfg.liq, rocky_transitions.py, rocky_transitions_web.py, rocky_loudness.py, rocky_transition_preview.py, templates/*.html (15), zerock-icecast.service, drive_write_helper.py, send_vote_wa.sh; icecast.xml identical to the app-dir copy (live /etc/icecast2/icecast.xml unreadable without sudo: unverified).
DIFFERENT: zerock-liquidsoap.service (/etc adds zerock-nas-mount.service to After/Wants) and zerock-radio-web.service (/etc adds zerock-nas-mount to After/Wants, PYTHONUNBUFFERED=1 and ~14 Environment= lines for SMTP/WP/Podbean/Spotify credentials+URLs). Repo copies match stale copies in the app dir.
Server-only (not in repo): backup_schedule.sh, stream_remote.sh, restart_if_clear.sh, safe-restart.sh, ~/whatsapp-bridge/ (bridge.js, backup_auth.sh, routes.json), lq_normalize_*.sh, lq_smart_*/lq_update3-5.sh, ~17 one-off restart_*/scheduled_* scripts, rebuild_1100.sh, republish_zifim_1200.sh, resume_test.py, backfill_*.py, run_liq.sh, tv_*_check.sh, /usr/local/sbin/zerock-nas-mount.sh, /etc/needrestart/conf.d/zerock.conf; clutter: templates/radio_app.py (296 KB stray copy from May), templates/index.html.bak-rebuild-btn, stale show_form.html/live_stream.html/al_haroker_upload.html in app dir, many rocky.liq.* and schedule.json.bak-* backups.

## Findings
HIGH logs unbounded, no rotation: logs/web.log 3.0 GB, liquidsoap.log 410 MB, /var/log/journal 2.4 GB; no logrotate for zerock; web.log grows ~9.4 KB/20 s (~40 MB/day), almost all `[Scheduler] '<show>' in -Ns` prints (~462k lines for one show in the last 200 MB). `/` 74% used, 6.0 GB free: months of headroom but another disk-full incident is possible.
MEDIUM 82 stale untriggered queue entries 3-198 days past due (re-logged each pass): 74 with no file (unfilled placeholders), 8 with missing files; worst: ze_prog 23, oy_vavoy 17, beat_on 12, pascal 11; 15 within the last 30 days.
MEDIUM cleanup message loop: "Upload failed ... file is missing — cannot retry, marking deleted" >42,000 times for the Rock Chart alone; 4 entries (oy_vavoy, al_harocker, matzad_harok x2) have upload_failed set but no file_deleted in schedule.json so the flag never sticks (log spam; consistent with Reviewer-1 finding 1).
MEDIUM credentials in systemd: /etc/systemd/system/zerock-radio-web.service mode 644 holds SMTP/WP/Podbean/Spotify secrets, readable by any local user; repo copy omits them (explains the diff). rocky.liq has 2 secret-keyword matches (known open item).
MEDIUM leftover test server: stray `python3 /tmp/trweb_test.py` running 14 days on 0.0.0.0:5099 serving the transitions web app.
LOW: /var/run/reboot-required exists (icecast/liquidsoap on pre-upgrade libs by design); legacy zerock-rocky writes ~/zerock-stream/rocky.log (111 MB) with "API unreachable"/"No files available" errors every few seconds; BounceCheck errors in web.log (none in last ~7 days): `'Header' object has no attribute 'lower'` ~2,180 in last 1.2 GB (~30 days), IMAP SSL EOF 383+294; old web.log error lines (File type not supported 7,702; Filename is missing 4,449; Show file not found) none in last ~7 days; stray .env_wp_check.tmp (0 bytes); rocky_transitions.enabled and .mitsad_on_hold are intentional flags; /mnt/mycloud not mounted (nofail); /mnt/truenas only in fstab.
OK: services; memory/load (0.23, 6 GB available); /mnt/nas mounted .198, 62% used; stream: exactly one source on /stream, 7 listeners, source start Oct 23; `/` 200 in 35 ms, /api/on-air 200; liquidsoap clean since Oct 23 restart apart from ID3/decoder warnings on bad files (last 11-01 02:09), pipe errors last Oct 22; all 24 *.json parse; schedule.json valid, written 15:59 today, no upload_in_progress, 33 future entries (next 17:00), two old duplicates (on_air 2026-09-30 18:00, zifim 2026-10-06 11:00); all five .py pass py_compile (Python 3.12.3).
NOT checked: host 114 and the stream monitor's last results; there is no ~/stream_monitor.log on rocky, so the remote heal has never run (expected: it only logs when it heals); live /etc/icecast2/icecast.xml.
