#!/usr/bin/env python3
"""Daily listeners report for https://rocky.kupernet.com/stream.

Reads Caddy JSON access logs, counts finished /stream sessions in a 24 h window
(21:00 to 21:00 Asia/Jerusalem), and sends one text report through an injected
delivery command. Standard library only. See README.md.

No raw IP address is ever stored, printed or logged: sessions are kept as salted
HMAC device hashes.
"""
import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import glob
import gzip
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import zlib
from zoneinfo import ZoneInfo

BOT_MARKERS = (
    "amazonbot", "amplitudeairplaymonitor", "meta-externalagent", "curl/", "lavf/",
    "tunein-dirmon", "infrawatch", "censysinspect", "python-requests", "go-http-client",
    "zgrab", "masscan", "nmap", "crawler", "spider",
    "googlebot", "bingbot", "yandexbot", "semrushbot", "ahrefsbot", "petalbot", "mj12bot",
    "dotbot", "applebot", "uptimerobot", "uptime-kuma",
)

INTERNAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7",
))

DEFAULTS = {
    "site_host": "rocky.kupernet.com",
    "own_public_ips": [],
    "stale_after_minutes": 30,
    "timezone": "Asia/Jerusalem",
    "report_hour": 21,
    "delivery_argv": [],
    "mmdb_path": "",  # accepted and ignored: geography was removed from the report
    "show_timeline_url": "http://192.168.1.166:5000/api/listener-stats/history",
}
REQUIRED = ("caddy_log_globs", "state_dir")

HISTORY_DAYS = 7
SAMPLE_MINUTES = 15
ROTATION_LABEL = "Rocky rotation (no show)"
SHOW_SEPARATOR = " \u2014 "

FOOTER = (
    "Listener = IP + app/device type, not a person. One person on two devices counts twice; "
    "a new IP counts as new. Session = one finished connection to rocky.kupernet.com/stream; "
    "connections still open at 21:00 count in the next report. Not counted: icecast.live relay "
    "listeners, metadata-only players. Per-show numbers do not add up to the total (a listener "
    "present during two shows counts in both); show boundaries are approximate to 15 minutes."
)

OWN_NETWORK_LINE = ("Own network not excluded: own_public_ips is empty, so listeners behind the "
                    "station's own public address are counted.")


class SaltProblem(Exception):
    """The device-hash salt cannot be used safely; the message is the data-source problem."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS windows(
    window_start_utc TEXT PRIMARY KEY, window_end_utc TEXT, window_end_local TEXT,
    sessions INTEGER, excluded_internal INTEGER, excluded_bot INTEGER, no_ip INTEGER,
    unparsed INTEGER, unique_n INTEGER, new_n INTEGER, returning_n INTEGER,
    geo_status TEXT, geo_db_month TEXT, source_first_ts TEXT, source_last_ts TEXT,
    built_at_utc TEXT, sent_at_utc TEXT);
CREATE TABLE IF NOT EXISTS sessions(
    window_start_utc TEXT, ts_utc TEXT, device TEXT, country TEXT, city TEXT);
CREATE INDEX IF NOT EXISTS sessions_device ON sessions(device);
CREATE INDEX IF NOT EXISTS sessions_window ON sessions(window_start_utc);
CREATE TABLE IF NOT EXISTS notices(
    window_start_utc TEXT PRIMARY KEY, reason TEXT, sent_at_utc TEXT);
"""


# ---------------------------------------------------------------- config

def parse_ip(value):
    """Parse an address string; IPv4-mapped IPv6 becomes IPv4. None when unusable."""
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        ip = value
    elif isinstance(value, str):
        try:
            ip = ipaddress.ip_address(value.strip())
        except ValueError:
            return None
    else:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def normalize_config(raw):
    cfg = dict(DEFAULTS)
    cfg.update(raw)
    for key in REQUIRED:
        if not cfg.get(key):
            raise ValueError("missing config key: " + key)
    if isinstance(cfg["caddy_log_globs"], str):
        cfg["caddy_log_globs"] = [cfg["caddy_log_globs"]]
    own = []
    for item in cfg["own_public_ips"]:
        ip = parse_ip(item)
        if ip is None:
            raise ValueError("invalid entry in own_public_ips")
        own.append(ip)
    cfg["own_public_ips"] = own
    return cfg


def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raise SystemExit("config file missing or not valid JSON")
    if not isinstance(raw, dict):
        raise SystemExit("config file must hold a JSON object")
    return normalize_config(raw)


# ---------------------------------------------------------------- time

def utc_iso(when):
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_iso(ts):
    return utc_iso(dt.datetime.fromtimestamp(ts, dt.timezone.utc))


def compute_window(window_end_local, tz):
    """End is the given wall-clock time; start is the same wall-clock time on the previous calendar day."""
    end = dt.datetime(window_end_local.year, window_end_local.month, window_end_local.day,
                      window_end_local.hour, window_end_local.minute, tzinfo=tz)
    prev = end.date() - dt.timedelta(days=1)
    start = dt.datetime(prev.year, prev.month, prev.day, end.hour, end.minute, tzinfo=tz)
    return start, end


# ---------------------------------------------------------------- log parsing

def entry_ip(req):
    for key in ("client_ip", "remote_ip"):
        ip = parse_ip(req.get(key))
        if ip is not None:
            return ip
    return None


def entry_user_agent(entry, req):
    headers = req.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if isinstance(name, str) and name.lower() == "user-agent":
                if isinstance(value, list) and value and isinstance(value[0], str):
                    return value[0]
                if isinstance(value, str):
                    return value
    top = entry.get("user_agent")
    return top if isinstance(top, str) else ""


def is_stream_session(entry, req, site_host):
    if req.get("method") != "GET":
        return False
    uri = req.get("uri")
    if not isinstance(uri, str) or uri.split("?", 1)[0] != "/stream":
        return False
    if entry.get("status") not in (200, 206):
        return False
    host = req.get("host")
    if not isinstance(host, str):
        return False
    return re.sub(r":\d+$", "", host.lower()) == site_host.lower()


def is_internal(ip, own_ips):
    return any(ip in net for net in INTERNAL_NETWORKS) or ip in own_ips


def is_bot(user_agent):
    low = user_agent.lower()
    return any(marker in low for marker in BOT_MARKERS)


def device_hash(salt, ip, user_agent):
    if ip.version == 4:
        ip_key = str(ip)
    else:
        ip_key = str(ipaddress.ip_network(f"{ip}/64", strict=False))
    ua_key = re.sub(r"\d+", "#", " ".join(user_agent.lower().split()))
    return hmac.new(salt, (ip_key + "\n" + ua_key).encode(), hashlib.sha256).hexdigest()


def scan_logs(cfg, start_ts, end_ts, salt):
    scan = {"files": 0, "unparsed": 0, "parsed": 0, "min_ts": None, "max_ts": None,
            "internal": 0, "bot": 0, "no_ip": 0, "sessions": [], "errors": []}
    paths = set()
    for pattern in cfg["caddy_log_globs"]:
        paths.update(os.path.realpath(p) for p in glob.glob(pattern))
    for path in sorted(paths):
        try:
            if os.path.getmtime(path) < start_ts:
                scan["files"] += 1
                continue
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                scan["files"] += 1
                for line in fh:
                    _scan_line(line, cfg, start_ts, end_ts, salt, scan)
        except (OSError, EOFError, zlib.error):
            # Unreadable or truncated: counts would be too low. Never hide it.
            scan["errors"].append(os.path.basename(path))
    return scan


def _scan_line(line, cfg, start_ts, end_ts, salt, scan):
    line = line.strip()
    if not line:
        return
    try:
        entry = json.loads(line)
    except ValueError:
        scan["unparsed"] += 1
        return
    ts = entry.get("ts") if isinstance(entry, dict) else None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
        scan["unparsed"] += 1
        return
    scan["parsed"] += 1
    if scan["min_ts"] is None or ts < scan["min_ts"]:
        scan["min_ts"] = ts
    if scan["max_ts"] is None or ts > scan["max_ts"]:
        scan["max_ts"] = ts
    if not (start_ts <= ts < end_ts):
        return
    req = entry.get("request")
    if not isinstance(req, dict) or not is_stream_session(entry, req, cfg["site_host"]):
        return
    ip = entry_ip(req)
    if ip is None:
        scan["no_ip"] += 1
        return
    if is_internal(ip, cfg["own_public_ips"]):
        scan["internal"] += 1
        return
    user_agent = entry_user_agent(entry, req)
    if is_bot(user_agent):
        scan["bot"] += 1
        return
    duration = entry.get("duration")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0):
        duration = 0
    scan["sessions"].append((ts, device_hash(salt, ip, user_agent), ts - duration))


def coverage_problem(scan, start_ts, reference_ts, stale_seconds):
    if scan["errors"]:
        return "log file unreadable: " + ", ".join(scan["errors"])
    if scan["files"] == 0:
        return "missing"
    if scan["parsed"] == 0:
        return "empty"
    if scan["max_ts"] < reference_ts - stale_seconds:
        return "stale"
    if scan["min_ts"] > start_ts + stale_seconds:
        return "not covering window start"
    return None


# ---------------------------------------------------------------- state

def ensure_state_dir(state_dir):
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    os.chmod(state_dir, 0o700)


def load_salt(state_dir, create, has_history=False):
    """Return the salt bytes.

    An empty salt file, or a missing salt while history exists, raises SaltProblem: a new salt
    would silently make every device "new". A missing salt with no history is created (mode
    0600) or, if create is False, replaced by a throwaway one.
    """
    path = os.path.join(state_dir, "salt")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if data:
            return data
        raise SaltProblem("salt file empty")
    except FileNotFoundError:
        pass
    except OSError:
        raise SaltProblem("salt file unreadable")
    if has_history:
        raise SaltProblem("salt file missing while history exists, new/returning not comparable")
    fresh = os.urandom(32)
    if not create:
        return fresh
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(fresh)
    return fresh


def open_state(state_dir):
    ensure_state_dir(state_dir)
    db_path = os.path.join(state_dir, "state.db")
    conn = sqlite3.connect(db_path)
    os.chmod(db_path, 0o600)
    conn.executescript(SCHEMA)
    return conn


def open_state_readonly(state_dir):
    db_path = os.path.join(state_dir, "state.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM windows LIMIT 1")
        return conn
    except sqlite3.Error:
        return None


@contextlib.contextmanager
def run_lock(state_dir):
    """Exclusive non-blocking lock on state_dir/run.lock. Yields True when held, False when busy."""
    ensure_state_dir(state_dir)
    fd = os.open(os.path.join(state_dir, "run.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)  # closing releases the lock


def log_failure(state_dir, now_utc, start_iso, kind, rc):
    path = os.path.join(state_dir, "run.log")
    line = f"{utc_iso(now_utc)} window={start_iso} kind={kind} send_rc={rc}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------- per-show timeline

class _Unreadable(Exception):
    """The timeline answer is not in the expected shape."""


def _http_get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read(8 * 1024 * 1024)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise _Unreadable()


def _fail_reason(exc):
    if isinstance(exc, _Unreadable):
        return "unreadable timeline"
    if isinstance(exc, urllib.error.HTTPError):
        return f"timeline fetch failed: HTTP {exc.code}"
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timeline fetch failed: timed out"
    if isinstance(exc, OSError):
        return "timeline fetch failed: connection error"
    return "timeline fetch failed: error"


def _parse_samples(payload, tz):
    """Valid samples as {local datetime: show}. Malformed samples are skipped."""
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise _Unreadable()
    out = {}
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("ts"), str):
            continue
        show = rec.get("show")
        if show is not None and not isinstance(show, str):
            continue
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                naive = dt.datetime.strptime(rec["ts"], fmt)
                break
            except ValueError:
                naive = None
        if naive is None:
            continue
        out[naive.replace(tzinfo=tz)] = show or ""
    return out


def fetch_show_timeline(start_local, end_local, tz, fetch=None, url_base=None):
    """Return (samples, error). samples: sorted [(start datetime, show)] with window start <= T < end.

    Never raises. error is a short reason string or None; it is set only when no date gave a
    usable answer. A date that fails while another succeeds shows up as missing samples.
    """
    fetch = fetch or _http_get_json
    url_base = url_base or DEFAULTS["show_timeline_url"]
    samples, errors = {}, []
    day, last = start_local.date(), (end_local - dt.timedelta(microseconds=1)).date()
    try:
        while day <= last:
            url = f"{url_base}?date={day:%Y-%m-%d}"
            payload, failure = None, None
            for _attempt in range(2):
                try:
                    payload, failure = fetch(url), None
                    break
                except Exception as exc:  # noqa: BLE001 - never raise from the fetch
                    failure = exc
            if failure is not None:
                errors.append(_fail_reason(failure))
            else:
                try:
                    samples.update(_parse_samples(payload, tz))
                except _Unreadable:
                    errors.append("unreadable timeline")
            day += dt.timedelta(days=1)
    except Exception:  # noqa: BLE001
        return [], "timeline fetch failed: error"
    kept = sorted((t, show) for t, show in samples.items() if start_local <= t < end_local)
    if not kept and errors:
        return [], errors[0]
    return kept, None


def normalize_show(name):
    """Rotation label for an empty or "Rocky" name; a repeated host segment is collapsed."""
    name = (name or "").strip()
    if not name or name.lower() == "rocky":
        return ROTATION_LABEL
    parts = name.split(SHOW_SEPARATOR)
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts.pop()
    return SHOW_SEPARATOR.join(parts)


def show_listeners(sessions, samples):
    """Distinct devices per show: [(name, count)] by count descending, then name.

    A sample at T covers [T, T+15 min). A session (ts, device, start) overlaps it if
    start < T+15m and ts > T; a zero-length session counts if T <= ts < T+15m.
    """
    width = SAMPLE_MINUTES * 60
    devices = {}
    for t, show in samples:
        t0 = t.timestamp()
        t1 = t0 + width
        name = normalize_show(show)
        bucket = devices.setdefault(name, set())
        for ts, device, start in sessions:
            if (start < t1 and ts > t0) or (start == ts and t0 <= ts < t1):
                bucket.add(device)
    return sorted(((n, len(d)) for n, d in devices.items() if d), key=lambda kv: (-kv[1], kv[0]))


def per_show_result(sessions, start_local, end_local, tz, fetch=None, url_base=None):
    expected = round((end_local - start_local).total_seconds() / (SAMPLE_MINUTES * 60))
    result = {"shows": [], "error": None, "missing": 0, "expected": expected}
    try:
        samples, error = fetch_show_timeline(start_local, end_local, tz, fetch, url_base)
        if error:
            result["error"] = error
        elif not samples:
            result["error"] = "no samples in window"
        else:
            result["shows"] = show_listeners(sessions, samples)
            result["missing"] = max(0, expected - len(samples))
    except Exception:  # noqa: BLE001 - the rest of the report must still go out
        result["error"] = "unreadable timeline"
    return result


# ---------------------------------------------------------------- rendering

def render_header(start_local, end_local, tz_name):
    hours = round((end_local.astimezone(dt.timezone.utc)
                   - start_local.astimezone(dt.timezone.utc)).total_seconds() / 3600)
    return ("ZeRock Radio - Daily Listeners Report\n"
            f"Window: {start_local:%Y-%m-%d %H:%M} -> {end_local:%Y-%m-%d %H:%M} ({tz_name}, {hours} h)")


def render_problem(header, reason):
    return (header + f"\nDATA SOURCE PROBLEM: {reason}. "
            "No counts shown; nothing stored for this window.")


def history_line(stored_starts, start_iso, tz):
    starts = set(stored_starts) | {start_iso}

    def local_day(iso):
        when = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return when.astimezone(tz).date()

    days = {local_day(iso) for iso in starts}
    first_day, today = min(days), local_day(start_iso)
    lines = []
    if len(starts) < HISTORY_DAYS:
        until = first_day + dt.timedelta(days=HISTORY_DAYS)
        lines.append(f"HISTORY STARTS {first_day:%Y-%m-%d} ({len(starts)} of {HISTORY_DAYS} days): "
                     f'"new" is overstated until {until:%Y-%m-%d}.')
    day = max(first_day, today - dt.timedelta(days=HISTORY_DAYS - 1))
    missing = []
    while day < today:
        if day not in days:
            missing.append(f"{day:%Y-%m-%d}")
        day += dt.timedelta(days=1)
    if missing:
        lines.append(f'MISSING DAYS: {", ".join(missing)} (no stored report): "new" may be overstated.')
    return "\n".join(lines) or None


def render_per_show(per_show):
    """Lines for the per-show section. per_show: {"shows", "error", "missing", "expected"}."""
    if per_show["error"]:
        return [f"Per show: UNAVAILABLE ({per_show['error']})"]
    if per_show["shows"]:
        lines = ["Listeners per show:"] + [f"  {name}: {count}" for name, count in per_show["shows"]]
    else:
        lines = ["Listeners per show: none"]
    if per_show["missing"] > 0:
        lines.append(f"Per-show timeline incomplete: {per_show['missing']} of "
                     f"{per_show['expected']} samples missing")
    return lines


def render_report(header, res, history, own_excluded=True):
    lines = [
        header,
        f"Sessions: {res['sessions']}",
        f"Unique listeners: {res['unique']} (new {res['new']}, returning {res['returning']})",
        f"Excluded internal (own systems, own network): {res['internal']}",
        f"Excluded bots and probes: {res['bot']}",
        f"No usable client IP (not counted): {res['no_ip']}",
    ]
    if res["unparsed"] > 0:
        lines.append(f"Unparsed log lines (ignored): {res['unparsed']}")
    lines.extend(render_per_show(res["per_show"]))
    if history:
        lines.append(history)
    lines.append("--")
    lines.append(FOOTER)
    if not own_excluded:
        lines.append(OWN_NETWORK_LINE)
    return "\n".join(lines)


# ---------------------------------------------------------------- delivery

def send_via_command(text, argv):
    """Run the delivery command with the base64 of the UTF-8 text as the last argument. No shell."""
    if not argv:
        return 127
    full = list(argv) + [base64.b64encode(text.encode("utf-8")).decode("ascii")]
    try:
        return subprocess.run(full, timeout=60, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127


# ---------------------------------------------------------------- core

def run_report(config, window_end_local, now_utc, send, dry_run=False, resend=False, force=False,
               timeline_fetch=None):
    cfg = normalize_config(config)
    tz = ZoneInfo(cfg["timezone"])
    if not force and now_utc.astimezone(tz).hour != cfg["report_hour"]:
        return 0
    _, end_local = compute_window(window_end_local, tz)
    if end_local.astimezone(dt.timezone.utc) > now_utc and not dry_run:
        print("refused: the window has not ended yet; nothing stored or sent", file=sys.stderr)
        return 3
    if dry_run:
        return _run_window(cfg, window_end_local, now_utc, send, dry_run, resend, timeline_fetch)
    with run_lock(cfg["state_dir"]) as held:
        if not held:
            print("another run is in progress; nothing done")
            return 0
        return _run_window(cfg, window_end_local, now_utc, send, dry_run, resend, timeline_fetch)


def _run_window(cfg, window_end_local, now_utc, send, dry_run, resend, timeline_fetch=None):
    tz = ZoneInfo(cfg["timezone"])
    start_local, end_local = compute_window(window_end_local, tz)
    start_utc = start_local.astimezone(dt.timezone.utc)
    end_utc = end_local.astimezone(dt.timezone.utc)
    start_iso, end_iso = utc_iso(start_utc), utc_iso(end_utc)
    state_dir = cfg["state_dir"]
    header = render_header(start_local, end_local, cfg["timezone"])
    if end_utc > now_utc:  # reachable only in dry-run
        header += ("\nINCOMPLETE WINDOW: it ends after the current time, so counts cover only part "
                   "of it. Dry-run only: never stored, never sent.")

    conn = open_state_readonly(state_dir) if dry_run else open_state(state_dir)
    try:
        if not dry_run and not resend:
            row = conn.execute("SELECT sent_at_utc FROM windows WHERE window_start_utc=?",
                               (start_iso,)).fetchone()
            if row and row[0]:
                print("already sent")
                return 0

        try:
            has_history = conn is not None and bool(
                conn.execute("SELECT 1 FROM windows LIMIT 1").fetchone()
                or conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone())
            salt = load_salt(state_dir, create=not dry_run, has_history=has_history)
        except SaltProblem as exc:
            return _handle_problem(cfg, conn, header, str(exc), start_iso, now_utc, send,
                                   dry_run, resend)
        scan = scan_logs(cfg, start_utc.timestamp(), end_utc.timestamp(), salt)

        reference = min(now_utc, end_utc).timestamp()
        problem = coverage_problem(scan, start_utc.timestamp(), reference,
                                   cfg["stale_after_minutes"] * 60)
        if problem:
            return _handle_problem(cfg, conn, header, problem, start_iso, now_utc, send,
                                   dry_run, resend)

        res = _summarize(scan, conn, start_iso)
        res["per_show"] = per_show_result(scan["sessions"], start_local, end_local, tz,
                                          timeline_fetch, cfg["show_timeline_url"])
        stored = [r[0] for r in conn.execute("SELECT window_start_utc FROM windows")] if conn else []
        text = render_report(header, res, history_line(stored, start_iso, tz),
                           own_excluded=bool(cfg["own_public_ips"]))
        if dry_run:
            print(text)
            return 0

        built = utc_iso(now_utc)
        with conn:
            conn.execute("DELETE FROM sessions WHERE window_start_utc=?", (start_iso,))
            conn.executemany(
                "INSERT INTO sessions(window_start_utc, ts_utc, device, country, city) VALUES(?,?,?,?,?)",
                [(start_iso, epoch_iso(ts), dev, "unknown", "unknown")
                 for ts, dev, _start in sorted(scan["sessions"])])
            conn.execute(
                "INSERT OR REPLACE INTO windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (start_iso, end_iso, f"{end_local:%Y-%m-%dT%H:%M}", res["sessions"], res["internal"],
                 res["bot"], res["no_ip"], res["unparsed"], res["unique"], res["new"],
                 res["returning"], "off", "", epoch_iso(scan["min_ts"]),
                 epoch_iso(scan["max_ts"]), built))
        rc = send(text, cfg["delivery_argv"])
        if rc != 0:
            log_failure(state_dir, now_utc, start_iso, "report", rc)
            print(f"send failed (rc={rc})", file=sys.stderr)
            return 2
        with conn:
            conn.execute("UPDATE windows SET sent_at_utc=? WHERE window_start_utc=?",
                         (utc_iso(now_utc), start_iso))
        print("report sent")
        return 0
    finally:
        if conn is not None:
            conn.close()


def _summarize(scan, conn, start_iso):
    devices = {dev for _ts, dev, _start in scan["sessions"]}
    earlier = set()
    if conn is not None and devices:
        earlier = {r[0] for r in conn.execute(
            "SELECT DISTINCT device FROM sessions WHERE window_start_utc < ?", (start_iso,))}
    new = len(devices - earlier)
    return {"sessions": len(scan["sessions"]), "unique": len(devices), "new": new,
            "returning": len(devices) - new, "internal": scan["internal"], "bot": scan["bot"],
            "no_ip": scan["no_ip"], "unparsed": scan["unparsed"]}


def _handle_problem(cfg, conn, header, reason, start_iso, now_utc, send, dry_run, resend):
    text = render_problem(header, reason)
    if dry_run:
        print(text)
        return 0
    if not resend and conn.execute("SELECT 1 FROM notices WHERE window_start_utc=?",
                                   (start_iso,)).fetchone():
        print("already sent")
        return 0
    rc = send(text, cfg["delivery_argv"])
    if rc != 0:
        log_failure(cfg["state_dir"], now_utc, start_iso, "problem", rc)
        print(f"send failed (rc={rc})", file=sys.stderr)
        return 2
    with conn:
        conn.execute("INSERT OR REPLACE INTO notices VALUES(?,?,?)",
                     (start_iso, reason, utc_iso(now_utc)))
    print("problem notice sent")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Daily listeners report")
    parser.add_argument("--config", required=True, help="JSON config file (kept outside the repo)")
    parser.add_argument("--window-end", help="YYYY-MM-DDTHH:MM Jerusalem local; default today at report_hour")
    parser.add_argument("--force", action="store_true", help="skip the report-hour gate")
    parser.add_argument("--dry-run", action="store_true", help="print the report; write and send nothing")
    parser.add_argument("--resend", action="store_true", help="rebuild and send even if already sent")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    tz = ZoneInfo(cfg["timezone"])
    now = dt.datetime.now(dt.timezone.utc)
    if args.window_end:
        try:
            window_end = dt.datetime.strptime(args.window_end, "%Y-%m-%dT%H:%M")
        except ValueError:
            raise SystemExit("--window-end must look like 2026-11-03T21:00")
    else:
        today = now.astimezone(tz)
        window_end = dt.datetime(today.year, today.month, today.day, cfg["report_hour"], 0)
    return run_report(cfg, window_end, now, send_via_command, dry_run=args.dry_run,
                      resend=args.resend, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
