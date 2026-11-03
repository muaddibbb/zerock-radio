#!/usr/bin/env python3
"""Daily listeners report for https://rocky.kupernet.com/stream.

Reads Caddy JSON access logs, counts finished /stream sessions in a 24 h window
(21:00 to 21:00 Asia/Jerusalem), and sends one text report through an injected
delivery command. Standard library only. See README.md.

No raw IP address is ever stored, printed or logged: sessions are kept as salted
HMAC device hashes plus country/city names.
"""
import argparse
import base64
import collections
import datetime as dt
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
import zlib
from zoneinfo import ZoneInfo

from mmdb_reader import MmdbReader

BOT_MARKERS = (
    "amazonbot", "amplitudeairplaymonitor", "meta-externalagent", "curl/", "lavf/",
    "tunein-dirmon", "infrawatch", "censysinspect", "python-requests", "go-http-client",
    "zgrab", "masscan", "nmap", "bot", "crawler", "spider", "uptime",
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
    "mmdb_path": "",
}
REQUIRED = ("caddy_log_globs", "state_dir")

HISTORY_DAYS = 7
MAX_CITIES = 10

FOOTER = (
    "Listener = IP + app/device type, not a person. One person on two devices counts twice; "
    "a new IP counts as new. Session = one finished connection to rocky.kupernet.com/stream; "
    "connections still open at 21:00 count in the next report. Not counted: icecast.live relay "
    "listeners, metadata-only players. Locations approximate. IP Geolocation by DB-IP."
)

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
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
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


def geo_lookup(reader, ip):
    """Return (country, city); both "unknown" when unresolved. The IP does not leave this function."""
    if reader is None:
        return "unknown", "unknown"
    try:
        record = reader.lookup(str(ip))
    except ValueError:
        record = None
    if not isinstance(record, dict):
        return "unknown", "unknown"
    return _english_name(record.get("country")), _english_name(record.get("city"))


def _english_name(node):
    names = node.get("names") if isinstance(node, dict) else None
    name = names.get("en") if isinstance(names, dict) else None
    return name if isinstance(name, str) and name else "unknown"


def scan_logs(cfg, start_ts, end_ts, salt, reader):
    scan = {"files": 0, "unparsed": 0, "parsed": 0, "min_ts": None, "max_ts": None,
            "internal": 0, "bot": 0, "no_ip": 0, "sessions": []}
    paths = set()
    for pattern in cfg["caddy_log_globs"]:
        paths.update(glob.glob(pattern))
    for path in sorted(paths):
        try:
            if os.path.getmtime(path) < start_ts:
                scan["files"] += 1
                continue
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                scan["files"] += 1
                for line in fh:
                    _scan_line(line, cfg, start_ts, end_ts, salt, reader, scan)
        except (OSError, EOFError, zlib.error):
            continue
    return scan


def _scan_line(line, cfg, start_ts, end_ts, salt, reader, scan):
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
    country, city = geo_lookup(reader, ip)
    scan["sessions"].append((ts, device_hash(salt, ip, user_agent), country, city))


def coverage_problem(scan, start_ts, reference_ts, stale_seconds):
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


def load_salt(state_dir, create):
    """Return the salt bytes. When absent: create it (mode 0600) or, if create is False, use a throwaway one."""
    path = os.path.join(state_dir, "salt")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if data:
            return data
    except FileNotFoundError:
        pass
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


def log_failure(state_dir, now_utc, start_iso, kind, rc):
    path = os.path.join(state_dir, "run.log")
    line = f"{utc_iso(now_utc)} window={start_iso} kind={kind} send_rc={rc}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------- rendering

def _ordered(counter):
    return sorted(counter.items(), key=lambda kv: (kv[0] == "unknown", -kv[1], kv[0]))


def _join(items):
    return ", ".join(f"{name} {count}" for name, count in items)


def render_cities(cities):
    items = _ordered(cities)
    known = [kv for kv in items if kv[0] != "unknown"]
    unknown = [kv for kv in items if kv[0] == "unknown"]
    shown = known[:MAX_CITIES]
    rest = sum(count for _, count in known[MAX_CITIES:])
    parts = _join(shown)
    if rest:
        parts = (parts + ", " if parts else "") + f"other {rest}"
    if unknown:
        parts = (parts + ", " if parts else "") + _join(unknown)
    return parts or "none"


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
    if len(starts) >= HISTORY_DAYS:
        return None
    first = dt.datetime.strptime(min(starts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    first_day = first.astimezone(tz).date()
    until = first_day + dt.timedelta(days=HISTORY_DAYS)
    return (f"HISTORY STARTS {first_day:%Y-%m-%d} ({len(starts)} of {HISTORY_DAYS} days): "
            f'"new" is overstated until {until:%Y-%m-%d}.')


def render_report(header, res, history):
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
    if res["geo_status"] == "ok":
        lines.append("Countries: " + (_join(_ordered(res["countries"])) or "none"))
        lines.append("Cities (approx.): " + render_cities(res["cities"]))
    else:
        lines.append("Countries: " + res["geo_status"])
        lines.append("Cities (approx.): " + res["geo_status"])
    if history:
        lines.append(history)
    lines.append("--")
    lines.append(FOOTER)
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

def run_report(config, window_end_local, now_utc, send, dry_run=False, resend=False, force=False):
    cfg = normalize_config(config)
    tz = ZoneInfo(cfg["timezone"])
    if not force and now_utc.astimezone(tz).hour != cfg["report_hour"]:
        return 0
    start_local, end_local = compute_window(window_end_local, tz)
    start_utc = start_local.astimezone(dt.timezone.utc)
    end_utc = end_local.astimezone(dt.timezone.utc)
    start_iso, end_iso = utc_iso(start_utc), utc_iso(end_utc)
    state_dir = cfg["state_dir"]
    header = render_header(start_local, end_local, cfg["timezone"])

    conn = open_state_readonly(state_dir) if dry_run else open_state(state_dir)
    try:
        if not dry_run and not resend:
            row = conn.execute("SELECT sent_at_utc FROM windows WHERE window_start_utc=?",
                               (start_iso,)).fetchone()
            if row and row[0]:
                print("already sent")
                return 0

        salt = load_salt(state_dir, create=not dry_run)
        reader, geo_status, geo_month = None, "ok", ""
        if not cfg["mmdb_path"]:
            geo_status = "unavailable (not configured)"
        else:
            try:
                reader = MmdbReader(cfg["mmdb_path"])
                geo_month = reader.db_month
            except FileNotFoundError:
                geo_status = "unavailable (file not found)"
            except ValueError:
                geo_status = "unavailable (invalid file)"
            except OSError:
                geo_status = "unavailable (unreadable)"
        try:
            scan = scan_logs(cfg, start_utc.timestamp(), end_utc.timestamp(), salt, reader)
        finally:
            if reader is not None:
                reader.close()

        reference = min(now_utc, end_utc).timestamp()
        problem = coverage_problem(scan, start_utc.timestamp(), reference,
                                   cfg["stale_after_minutes"] * 60)
        if problem:
            return _handle_problem(cfg, conn, header, problem, start_iso, now_utc, send,
                                   dry_run, resend)

        res = _summarize(scan, conn, start_iso, geo_status)
        stored = [r[0] for r in conn.execute("SELECT window_start_utc FROM windows")] if conn else []
        text = render_report(header, res, history_line(stored, start_iso, tz))
        if dry_run:
            print(text)
            return 0

        built = utc_iso(now_utc)
        with conn:
            conn.execute("DELETE FROM sessions WHERE window_start_utc=?", (start_iso,))
            conn.executemany(
                "INSERT INTO sessions(window_start_utc, ts_utc, device, country, city) VALUES(?,?,?,?,?)",
                [(start_iso, epoch_iso(ts), dev, country, city)
                 for ts, dev, country, city in sorted(scan["sessions"])])
            conn.execute(
                "INSERT OR REPLACE INTO windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (start_iso, end_iso, f"{end_local:%Y-%m-%dT%H:%M}", res["sessions"], res["internal"],
                 res["bot"], res["no_ip"], res["unparsed"], res["unique"], res["new"],
                 res["returning"], geo_status, geo_month, epoch_iso(scan["min_ts"]),
                 epoch_iso(scan["max_ts"]), built))
        rc = send(text, cfg["delivery_argv"])
        if rc != 0:
            log_failure(state_dir, now_utc, start_iso, "report", rc)
            print(f"send failed (rc={rc})", file=sys.stderr)
            return 2
        with conn:
            conn.execute("UPDATE windows SET sent_at_utc=? WHERE window_start_utc=?",
                         (utc_iso(dt.datetime.now(dt.timezone.utc)), start_iso))
        print("report sent")
        return 0
    finally:
        if conn is not None:
            conn.close()


def _summarize(scan, conn, start_iso, geo_status):
    # A device's geography comes from its first session (by time) that resolves a country.
    first_seen = {}
    for _ts, dev, country, city in sorted(scan["sessions"]):
        if dev not in first_seen:
            first_seen[dev] = ("unknown", "unknown")
        if first_seen[dev][0] == "unknown" and country != "unknown":
            first_seen[dev] = (country, city)
    devices = set(first_seen)
    earlier = set()
    if conn is not None and devices:
        earlier = {r[0] for r in conn.execute(
            "SELECT DISTINCT device FROM sessions WHERE window_start_utc < ?", (start_iso,))}
    new = len(devices - earlier)
    countries = collections.Counter(c for c, _ in first_seen.values())
    cities = collections.Counter(city for _, city in first_seen.values())
    return {"sessions": len(scan["sessions"]), "unique": len(devices), "new": new,
            "returning": len(devices) - new, "internal": scan["internal"], "bot": scan["bot"],
            "no_ip": scan["no_ip"], "unparsed": scan["unparsed"], "countries": countries,
            "cities": cities, "geo_status": geo_status}


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
                     (start_iso, reason, utc_iso(dt.datetime.now(dt.timezone.utc))))
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
