# Daily listeners report

Every evening at 21:00 Asia/Jerusalem one WhatsApp message covers the previous 24 hours of
listening on https://rocky.kupernet.com/stream: sessions, unique listeners, new versus
returning, and unique listeners per show. Every listener number comes from the Caddy access
log; show names and times come from rocky's recorded show timeline. If the log is missing, empty, stale or does not cover the window,
the message says `DATA SOURCE PROBLEM: <reason>` and shows no counts.

Files: `listener_report.py` (program), `mmdb_reader.py` (offline MaxMind DB reader, no longer
used by the report; kept in the repo), `listener_report.example.json` (config template), `tests/`.
Standard library only, Python 3.12. The config file and the state directory live outside the
repo; this repo is public, so never put paths to private files, recipients or keys in it.

## Rules

- Window: end is 21:00 Jerusalem time, start is 21:00 the previous calendar day. Start is
  inclusive, end is exclusive. On the two DST days the window is 23 or 25 hours; the report
  header says so.
- Session: a Caddy access-log line with `request.method` GET, path `/stream` (query string
  ignored), status 200 or 206, and host equal to `site_host`. The line is written when the
  connection ends, so a session belongs to the window that contains its log time. Everything
  else is ignored.
- Client IP: `request.client_ip`, else `request.remote_ip`. IPv4-mapped IPv6 becomes IPv4.
  A session with no usable address is counted as "No usable client IP" and not as a listener.
- Excluded internal: 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1, fc00::/7
  and any address in `own_public_ips`. Checked before bots.
- Excluded bots and probes: User-Agent contains (case-insensitive) one of
  `BOT_MARKERS = ("amazonbot", "amplitudeairplaymonitor", "meta-externalagent", "curl/", "lavf/", "tunein-dirmon", "infrawatch", "censysinspect", "python-requests", "go-http-client", "zgrab", "masscan", "nmap", "crawler", "spider", "googlebot", "bingbot", "yandexbot", "semrushbot", "ahrefsbot", "petalbot", "mj12bot", "dotbot", "applebot", "uptimerobot", "uptime-kuma")`.
  The broad `bot` and `uptime` markers were removed so apps with those letters in their name are not dropped.
  An empty or unknown User-Agent is a listener.
- Device ("listener"): HMAC-SHA256 with a random per-install salt over the IPv4 address (or
  the IPv6 /64) plus the User-Agent with digits replaced by `#`. So a browser or OS update
  does not make a new listener. Only the hash and the time are stored;
  never the IP or the User-Agent.
- New versus returning: a device is new in a window if it has no stored session in any earlier
  window. Returning = unique - new.
- Geography: removed. The report has no Countries or Cities line and never opens a geo
  database. `mmdb_path` in an existing config is accepted and ignored. The state tables keep
  their country/city columns, filled with `unknown` (and `geo_status` `off`).
- Listeners per show: each session has an interval [end - Caddy `duration`, end]; without a
  usable `duration` it is a point at its end time. The show timeline is fetched from
  `show_timeline_url` (default `http://192.168.1.166:5000/api/listener-stats/history`, LAN, no
  credentials) with `?date=YYYY-MM-DD` for both local dates the window touches (5 s timeout,
  one retry). Samples are every 15 minutes, `ts` in Asia/Jerusalem local time, field `show`; a
  sample at T means that show was on air during [T, T+15 min). A session counts for a show if
  it overlaps any of that show's samples (touching an end does not count; a zero-length
  session counts in the sample containing it). Listeners are distinct devices per show; a
  listener present during two shows counts in both, so per-show numbers do not add up to the
  total. An empty show name or the recorded name `Rocky` is printed as `Rocky rotation (no
  show)`; a repeated host segment ("A — B — B") prints once. Only shows with at least one
  listener are listed, highest first, then by name. Resolution is 15 minutes.
  With a complete timeline one more line follows: `Heard at least one show: M of N
  (rotation only: R)`, N = Unique listeners, M = distinct devices with a session overlapping
  any non-rotation show sample, R = N - M. It is omitted when the timeline is UNAVAILABLE or
  incomplete.
- Per-show failure lines (the rest of the report is still sent and stored):
  `Per show: UNAVAILABLE (timeline fetch failed: <reason>)`,
  `Per show: UNAVAILABLE (no samples in window)`, `Per show: UNAVAILABLE (unreadable timeline)`;
  with only some samples: the lines plus `Per-show timeline incomplete: N of 96 samples
  missing` (the number is the window length in 15-minute samples). Nothing is guessed.
- History: with fewer than 7 stored windows the report prints the history start date and warns
  that "new" is overstated until 7 days exist.

## Limits

- One person on two devices counts twice. The same device on a new IP counts as new.
- Lavf (ffmpeg/ffplay) is treated as a bot, but may be a real player.
- Listeners of the icecast.live relay and metadata-only players are not visible here.
- Two phones of the same model behind one address merge into one listener.
- Per-show boundaries are approximate to 15 minutes.
- The salted hash of an address plus User-Agent can be brute-forced by anyone holding both the
  salt and the database. Protection is the file mode (state dir 0700, files 0600).
- Time zone: the program computes the window itself with `zoneinfo`, whatever the server clock
  says. Cron should run every 15 minutes; the program acts only in the Jerusalem hour 21, sends
  at most once per window, and retries a failed send on later runs inside that hour. DST days
  produce a 23 h or 25 h window.

## Runbook

```
python3 listener_report.py --config /PATH/TO/config.json --dry-run
python3 listener_report.py --config /PATH/TO/config.json --window-end 2026-11-03T21:00 --force --dry-run
python3 listener_report.py --config /PATH/TO/config.json --window-end 2026-11-03T21:00 --force --resend
```

- Run it as `python3 /path/listener_report.py`.
- `--dry-run` prints the report and writes and sends nothing (it reads existing history).
- `--force` skips the hour-21 gate. `--window-end` is Jerusalem local time.
- `--resend` rebuilds that window from the log and sends it again. Re-running a window never
  double counts: its stored rows are replaced in one transaction.
- The delivery command is `delivery_argv` plus the base64 of the UTF-8 report as the last
  argument, run without a shell. A failed send is logged to `<state_dir>/run.log` (time,
  window, return code only) and the program exits 2.
- State reset: stop the cron job, move `<state_dir>` aside, start again. A new salt and an empty
  history follow; every listener will count as new until 7 days have been stored.
- Tests: `python3 -m unittest discover -s listener_report/tests -v`. They use synthetic
  documentation-range addresses only and never send or touch real state.

## Data-source problems and safeguards

- Caddy must log non-session traffic too (the whole site, or at least monitor requests), not
  only `/stream`. Sessions are logged when they end, so on a quiet-stream day a log holding
  only `/stream` can look stale or not covering the window and raise a notice.
- A log file that cannot be read or is truncated (for example a cut `.gz`) raises
  `DATA SOURCE PROBLEM: log file unreadable: <name>`; the program never reports a lower count.
- A window that has not ended yet is refused (exit code 3, nothing stored or sent). With
  `--dry-run` it is printed with an `INCOMPLETE WINDOW` line.
- A missing salt file while history exists, or an empty salt file, is a data-source problem;
  a new salt is never created over existing history.
- The history warning also lists `MISSING DAYS` (no stored report) in the last 7 days: devices
  heard on those days are unknown, so "new" may be overstated.
- Log globs are de-duplicated by resolved path. Globs must still match disjoint files (an
  uncompressed copy and its `.gz` would both be read).
- `own_public_ips` should hold the station's own public address. When it is empty the report
  ends with "Own network not excluded".
- Only one run at a time: a lock file in the state directory; a second overlapping run exits 0
  without doing anything.
