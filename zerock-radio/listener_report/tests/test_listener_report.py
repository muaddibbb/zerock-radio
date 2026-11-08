"""Tests for the daily listeners report. No network, no real state, no real delivery."""
import datetime as dt
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import listener_report as lp  # noqa: E402
from mmdb_reader import MmdbReader  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TZ = ZoneInfo("Asia/Jerusalem")
UTC = dt.timezone.utc
END1 = dt.datetime(2026, 11, 3, 21, 0)
END2 = dt.datetime(2026, 11, 4, 21, 0)
NOW1 = dt.datetime(2026, 11, 3, 21, 5, tzinfo=TZ).astimezone(UTC)
NOW2 = dt.datetime(2026, 11, 4, 21, 5, tzinfo=TZ).astimezone(UTC)
START1 = dt.datetime(2026, 11, 2, 21, 0, tzinfo=TZ)

FOOTER_TEXT = (
    "Listener = IP + app/device type, not a person. One person on two devices counts twice; "
    "a new IP counts as new. Session = one finished connection to rocky.kupernet.com/stream; "
    "connections still open at 21:00 count in the next report. Not counted: icecast.live relay "
    "listeners, metadata-only players. Per-show numbers do not add up to the total (a listener "
    "present during two shows counts in both); show boundaries are approximate to 15 minutes."
)

OWN_LINE = ("Own network not excluded: own_public_ips is empty, so listeners behind the "
            "station's own public address are counted.")

GEO_ENTRIES = [
    ("203.0.113.0/25", "Israel", "Tel Aviv"),
    ("203.0.113.128/25", "Israel", None),
    ("192.0.2.0/24", "United States", "New York"),
    ("2001:db8::/32", "Germany", "Berlin"),
]
BUILD_EPOCH = int(dt.datetime(2026, 10, 15, tzinfo=UTC).timestamp())


# ---------------------------------------------------------------- synthetic mmdb writer

class Ptr:
    def __init__(self, offset):
        self.offset = offset


class U16:
    def __init__(self, value):
        self.value = value


class U64:
    def __init__(self, value):
        self.value = value


class Dbl:
    def __init__(self, value):
        self.value = value


def _ctrl(ftype, size):
    if size < 29:
        sz, extra = size, b""
    elif size < 285:
        sz, extra = 29, bytes([size - 29])
    elif size < 65821:
        sz, extra = 30, (size - 285).to_bytes(2, "big")
    else:
        sz, extra = 31, (size - 65821).to_bytes(3, "big")
    if ftype <= 7:
        return bytes([(ftype << 5) | sz]) + extra
    return bytes([sz, ftype - 7]) + extra


def _uint(ftype, value):
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return _ctrl(ftype, len(raw)) + raw


def encode(value):
    if isinstance(value, Ptr):
        if value.offset < 2048:
            return bytes([0x20 | (value.offset >> 8), value.offset & 0xFF])
        p = value.offset - 2048
        return bytes([0x20 | 0x08 | (p >> 16)]) + (p & 0xFFFF).to_bytes(2, "big")
    if isinstance(value, bool):
        return _ctrl(14, int(value))
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _ctrl(2, len(raw)) + raw
    if isinstance(value, U16):
        return _uint(5, value.value)
    if isinstance(value, U64):
        return _uint(9, value.value)
    if isinstance(value, int):
        return _uint(6, value)
    if isinstance(value, Dbl):
        return _ctrl(3, 8) + struct.pack(">d", value.value)
    if isinstance(value, list):
        return _ctrl(11, len(value)) + b"".join(encode(v) for v in value)
    if isinstance(value, dict):
        return _ctrl(7, len(value)) + b"".join(encode(k) + encode(v) for k, v in value.items())
    raise TypeError(type(value))


def build_mmdb(entries, record_size=24, ip_version=6):
    data = bytearray()
    names = {}

    def intern(value):
        offset = len(data)
        data.extend(encode(value))
        return offset

    nodes = [[None, None]]
    for net_text, country, city in entries:
        net = ipaddress.ip_network(net_text)
        names_for = {}
        for label, name in (("country", country), ("city", city)):
            if name is None:
                continue
            if name not in names:
                names[name] = Ptr(intern({"en": name, "de": name}))
            names_for[label] = {"names": names[name]}
        names_for["location"] = {"latitude": Dbl(32.08), "accuracy_radius": U16(50)}
        names_for["is_in_european_union"] = False
        offset = intern(names_for)
        bits = []
        for byte in net.network_address.packed:
            bits.extend((byte >> (7 - i)) & 1 for i in range(8))
        bits = bits[:net.prefixlen]
        if ip_version == 6 and net.version == 4:
            bits = [0] * 96 + bits
        node = 0
        for i, bit in enumerate(bits):
            if i == len(bits) - 1:
                nodes[node][bit] = ("d", offset)
                break
            nxt = nodes[node][bit]
            if nxt is None:
                nodes.append([None, None])
                nxt = len(nodes) - 1
                nodes[node][bit] = nxt
            node = nxt
    count = len(nodes)

    def value_of(child):
        if child is None:
            return count
        if isinstance(child, int):
            return child
        return count + 16 + child[1]

    tree = bytearray()
    for left, right in nodes:
        lv, rv = value_of(left), value_of(right)
        if record_size == 24:
            tree.extend(lv.to_bytes(3, "big") + rv.to_bytes(3, "big"))
        elif record_size == 32:
            tree.extend(lv.to_bytes(4, "big") + rv.to_bytes(4, "big"))
        else:
            tree.extend((lv & 0xFFFFFF).to_bytes(3, "big")
                        + bytes([((lv >> 24) << 4) | (rv >> 24)])
                        + (rv & 0xFFFFFF).to_bytes(3, "big"))
    meta = {
        "binary_format_major_version": U16(2), "binary_format_minor_version": U16(0),
        "build_epoch": U64(BUILD_EPOCH), "database_type": "Synthetic",
        "description": {"en": "synthetic test database"}, "ip_version": U16(ip_version),
        "languages": ["en"], "node_count": count, "record_size": U16(record_size),
    }
    return bytes(tree) + b"\x00" * 16 + bytes(data) + b"\xab\xcd\xefMaxMind.com" + encode(meta)


# ---------------------------------------------------------------- helpers

class FakeSend:
    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def __call__(self, text, argv):
        self.calls.append((text, list(argv)))
        return self.rc


def slurp(path, mode="rb"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


def parse_counts(line):
    result = {}
    for part in line.split(": ", 1)[1].split(", "):
        name, count = part.rsplit(" ", 1)
        result[name] = int(count)
    return result


def log_entry(when, ip, ua="Mozilla/5.0 Test", method="GET", uri="/stream", status=200):
    req = {"method": method, "host": "rocky.kupernet.com", "uri": uri,
           "headers": {"User-Agent": [ua]}, "remote_ip": ip}
    return {"ts": when.timestamp(), "request": req, "status": status}


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lr-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.object(lp.subprocess, "run",
                                    side_effect=AssertionError("tests must never run a command"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logs = os.path.join(self.tmp, "logs")
        os.makedirs(self.logs)
        for name in ("caddy_day1.jsonl", "caddy_day2.jsonl"):
            target = os.path.join(self.logs, name)
            shutil.copy(os.path.join(FIXTURES, name), target)
            self.touch(target)
        net = mock.patch.object(lp, "_http_get_json", side_effect=OSError("no network in tests"))
        net.start()
        self.addCleanup(net.stop)
        self.state = os.path.join(self.tmp, "state")

    @staticmethod
    def touch(path):
        stamp = dt.datetime(2026, 11, 20, tzinfo=UTC).timestamp()
        os.utime(path, (stamp, stamp))

    def config(self, glob_name, **overrides):
        cfg = {"caddy_log_globs": [os.path.join(self.logs, glob_name)], "state_dir": self.state,
               "own_public_ips": [], "delivery_argv": ["deliver", "wa"]}
        cfg.update(overrides)
        return cfg

    def run_day(self, day, send=None, cfg=None, **kwargs):
        send = send if send is not None else FakeSend()
        cfg = cfg or self.config(f"caddy_day{day}.jsonl")
        end, now = (END1, NOW1) if day == 1 else (END2, NOW2)
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            rc = lp.run_report(cfg, end, now, send, **kwargs)
        return rc, send, out.getvalue()

    def window_rows(self):
        conn = sqlite3.connect(os.path.join(self.state, "state.db"))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM windows ORDER BY window_start_utc")]
        finally:
            conn.close()

    def count(self, table):
        conn = sqlite3.connect(os.path.join(self.state, "state.db"))
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def custom_log(self, entries, anchors=True):
        """Write a log with the given entries plus ignored HEAD anchors covering the whole window."""
        end = dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ)
        rows = list(entries)
        if anchors:
            rows.append(log_entry(START1, "198.51.100.1", method="HEAD"))
            rows.append(log_entry(end - dt.timedelta(seconds=60), "198.51.100.1", method="HEAD"))
        path = os.path.join(self.logs, "custom.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        self.touch(path)
        return self.config("custom.jsonl")

    def at(self, minutes):
        return START1 + dt.timedelta(minutes=minutes)

    def summary(self, entries):
        rc, _, _ = self.run_day(1, cfg=self.custom_log(entries))
        self.assertEqual(rc, 0)
        row = self.window_rows()[0]
        return row


# ---------------------------------------------------------------- window and gate

class WindowTests(BaseCase):
    def test_window_prev_day_21_to_21(self):
        rc, _, out = self.run_day(1, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("Window: 2026-11-02 21:00 -> 2026-11-03 21:00 (Asia/Jerusalem, 24 h)", out)

    def test_hour_gate_skips_non_21_hour(self):
        early = dt.datetime(2026, 11, 3, 20, 5, tzinfo=TZ).astimezone(UTC)
        send = FakeSend()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            rc = lp.run_report(self.config("caddy_day1.jsonl"), END1, early, send)
        self.assertEqual((rc, out.getvalue(), send.calls), (0, "", []))
        self.assertFalse(os.path.exists(self.state))

    def test_force_bypasses_gate(self):
        late = dt.datetime(2026, 11, 3, 22, 5, tzinfo=TZ).astimezone(UTC)  # hour 22: gate would skip
        send = FakeSend()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            rc = lp.run_report(self.config("caddy_day1.jsonl"), END1, late, send, force=True)
        # window ended at 21:00 (a window that has not ended is refused), newest line 20:59:59
        self.assertEqual(rc, 0)
        self.assertEqual(len(send.calls), 1)
        self.assertIn("Sessions: 12", send.calls[0][0])

    def test_dst_window_lengths(self):
        for end, hours in ((dt.datetime(2026, 3, 27, 21, 0), 23), (dt.datetime(2026, 10, 25, 21, 0), 25),
                           (dt.datetime(2026, 10, 26, 21, 0), 24)):
            start_local, end_local = lp.compute_window(end, TZ)
            header = lp.render_header(start_local, end_local, "Asia/Jerusalem")
            self.assertIn(f", {hours} h)", header, end)


# ---------------------------------------------------------------- fixture counting

class FixtureTests(BaseCase):
    def check_day(self, day, row, text):
        exp = json.loads(slurp(os.path.join(FIXTURES, "expected.json"), "r"))[f"day{day}"]
        got = {"sessions": row["sessions"], "unique": row["unique_n"], "new": row["new_n"],
               "returning": row["returning_n"], "internal": row["excluded_internal"],
               "bot": row["excluded_bot"], "no_ip": row["no_ip"], "unparsed": row["unparsed"]}
        self.assertEqual(got, {k: exp[k] for k in got})
        self.assertEqual(row["window_end_local"], exp["window_end_local"])

    def test_fixture_counts_match_expected(self):
        rc, send1, _ = self.run_day(1)
        self.assertEqual(rc, 0)
        rc, send2, _ = self.run_day(2)
        self.assertEqual(rc, 0)
        rows = self.window_rows()
        self.assertEqual(len(rows), 2)
        self.check_day(1, rows[0], send1.calls[0][0])
        self.check_day(2, rows[1], send2.calls[0][0])

    def test_new_vs_returning_day2(self):
        self.run_day(1)
        _, send, _ = self.run_day(2)
        self.assertIn("Unique listeners: 7 (new 3, returning 4)", send.calls[0][0])

    def test_internal_and_bot_excluded_counted_separately(self):
        _, send, _ = self.run_day(1)
        text = send.calls[0][0]
        self.assertIn("Excluded internal (own systems, own network): 4", text)
        self.assertIn("Excluded bots and probes: 3", text)

    def test_head_404_and_other_paths_ignored(self):
        row = self.summary([
            log_entry(self.at(10), "203.0.113.7", method="HEAD"),
            log_entry(self.at(20), "203.0.113.7", status=404),
            log_entry(self.at(30), "203.0.113.7", uri="/status-json.xsl"),
            log_entry(self.at(40), "203.0.113.7", uri="/other"),
        ])
        self.assertEqual((row["sessions"], row["unique_n"], row["excluded_internal"],
                          row["excluded_bot"], row["no_ip"]), (0, 0, 0, 0, 0))

    def test_same_device_twice_one_unique(self):
        row = self.summary([log_entry(self.at(10), "203.0.113.7"), log_entry(self.at(20), "203.0.113.7")])
        self.assertEqual((row["sessions"], row["unique_n"]), (2, 1))

    def test_ua_version_bump_same_device(self):
        row = self.summary([
            log_entry(self.at(10), "203.0.113.7", ua="App/1.2.3 (OS 26_6_2)"),
            log_entry(self.at(20), "203.0.113.7", ua="App/1.2.4 (OS 26_6_3)"),
        ])
        self.assertEqual((row["sessions"], row["unique_n"]), (2, 1))

    def test_same_ip_different_ua_two_devices(self):
        row = self.summary([
            log_entry(self.at(10), "203.0.113.7", ua="Alpha Player"),
            log_entry(self.at(20), "203.0.113.7", ua="Beta Player"),
        ])
        self.assertEqual((row["sessions"], row["unique_n"]), (2, 2))

    def test_ipv6_slash64_same_device(self):
        row = self.summary([
            log_entry(self.at(10), "2001:db8:1:1::10", ua="Alpha Player"),
            log_entry(self.at(20), "2001:db8:1:1:ffff::9", ua="Alpha Player"),
            log_entry(self.at(30), "2001:db8:1:2::10", ua="Alpha Player"),
        ])
        self.assertEqual((row["sessions"], row["unique_n"]), (3, 2))

    def test_empty_ua_is_listener(self):
        row = self.summary([log_entry(self.at(10), "203.0.113.7", ua="")])
        self.assertEqual((row["sessions"], row["unique_n"], row["excluded_bot"]), (1, 1, 0))

    def test_boundaries_start_inclusive_end_exclusive(self):
        end = dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ)
        row = self.summary([
            log_entry(START1 - dt.timedelta(seconds=1), "203.0.113.1"),
            log_entry(START1, "203.0.113.2"),
            log_entry(end - dt.timedelta(seconds=1), "203.0.113.3"),
            log_entry(end, "203.0.113.4"),
        ])
        self.assertEqual((row["sessions"], row["unique_n"]), (2, 2))

    def test_proxy_client_ip_counted(self):
        entry = log_entry(self.at(10), "192.168.1.50")
        entry["request"]["client_ip"] = "192.0.2.10"
        row = self.summary([entry])
        self.assertEqual((row["sessions"], row["excluded_internal"]), (1, 0))

    def test_both_internal_excluded(self):
        entry = log_entry(self.at(10), "192.168.1.114")
        entry["request"]["client_ip"] = "192.168.1.50"
        row = self.summary([entry])
        self.assertEqual((row["sessions"], row["excluded_internal"]), (0, 1))

    def test_own_public_ip_excluded(self):
        cfg = self.custom_log([log_entry(self.at(10), "203.0.113.5")])
        cfg["own_public_ips"] = ["203.0.113.5"]
        self.run_day(1, cfg=cfg)
        row = self.window_rows()[0]
        self.assertEqual((row["sessions"], row["excluded_internal"]), (0, 1))

    def test_no_usable_ip_shown(self):
        entry = log_entry(self.at(10), "not-an-ip")
        row = self.summary([entry])
        self.assertEqual((row["sessions"], row["no_ip"]), (0, 1))
        _, send, _ = self.run_day(1, resend=True, cfg=self.custom_log([entry]))
        self.assertIn("No usable client IP (not counted): 1", send.calls[0][0])

    def test_gz_file_is_read(self):
        import gzip
        cfg = self.custom_log([log_entry(self.at(10), "203.0.113.7")])
        plain = os.path.join(self.logs, "custom.jsonl")
        gz = os.path.join(self.logs, "custom.jsonl.gz")
        with open(plain, "rb") as src, gzip.open(gz, "wb") as dst:
            dst.write(src.read())
        os.remove(plain)
        self.touch(gz)
        cfg["caddy_log_globs"] = [gz]
        self.run_day(1, cfg=cfg)
        self.assertEqual(self.window_rows()[0]["sessions"], 1)


# ---------------------------------------------------------------- mmdb reader (unit, kept) and ignored config

class GeoTests(BaseCase):
    def test_mmdb_reader_synthetic_lookup(self):
        for size in (24, 28, 32):
            path = os.path.join(self.tmp, f"synthetic{size}.mmdb")
            with open(path, "wb") as fh:
                fh.write(build_mmdb(GEO_ENTRIES, record_size=size))
            reader = MmdbReader(path)
            try:
                self.assertEqual(reader.db_month, "2026-10")
                rec = reader.lookup("203.0.113.7")
                self.assertEqual(rec["country"]["names"]["en"], "Israel")
                self.assertEqual(rec["city"]["names"]["en"], "Tel Aviv")
                self.assertEqual(rec["location"], {"latitude": 32.08, "accuracy_radius": 50})
                self.assertIs(rec["is_in_european_union"], False)
                self.assertNotIn("city", reader.lookup("203.0.113.200"))
                self.assertEqual(reader.lookup("2001:db8::1")["city"]["names"]["en"], "Berlin")
                self.assertEqual(reader.lookup("::ffff:192.0.2.5")["country"]["names"]["en"], "United States")
                self.assertIsNone(reader.lookup("198.51.100.1"))
                self.assertIsNone(reader.lookup("2001:db9::1"))
            finally:
                reader.close()
        v4 = os.path.join(self.tmp, "v4.mmdb")
        with open(v4, "wb") as fh:
            fh.write(build_mmdb([e for e in GEO_ENTRIES if ":" not in e[0]], ip_version=4))
        reader = MmdbReader(v4)
        self.assertEqual(reader.lookup("192.0.2.9")["country"]["names"]["en"], "United States")
        self.assertIsNone(reader.lookup("2001:db8::1"))
        reader.close()
        empty = os.path.join(self.tmp, "empty.mmdb")
        open(empty, "wb").close()
        with self.assertRaises(ValueError):
            MmdbReader(empty)


# ---------------------------------------------------------------- data problems and idempotency

class ProblemTests(BaseCase):
    def assert_problem(self, cfg, reason):
        rc, send, _ = self.run_day(1, cfg=cfg)
        self.assertEqual(rc, 0)
        self.assertEqual(len(send.calls), 1)
        text = send.calls[0][0]
        self.assertIn(f"DATA SOURCE PROBLEM: {reason}. No counts shown; nothing stored for this window.", text)
        self.assertNotIn("Sessions:", text)
        self.assertNotIn("Unique listeners", text)
        self.assertEqual((self.count("windows"), self.count("sessions")), (0, 0))
        return send

    def test_missing_source_problem(self):
        self.assert_problem(self.config("nothing-here-*.jsonl"), "missing")

    def test_empty_source_problem(self):
        path = os.path.join(self.logs, "empty.jsonl")
        open(path, "w").close()
        self.touch(path)
        self.assert_problem(self.config("empty.jsonl"), "empty")

    def test_stale_source_problem(self):
        cfg = self.custom_log([log_entry(self.at(60), "203.0.113.7")], anchors=False)
        path = os.path.join(self.logs, "custom.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_entry(START1, "198.51.100.1", method="HEAD")) + "\n")
        self.assert_problem(cfg, "stale")

    def test_uncovered_start_problem(self):
        end = dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ)
        cfg = self.custom_log([log_entry(self.at(120), "203.0.113.7"),
                               log_entry(end - dt.timedelta(minutes=1), "203.0.113.7")], anchors=False)
        self.assert_problem(cfg, "not covering window start")

    def test_problem_stores_nothing(self):
        send = self.assert_problem(self.config("nothing-here-*.jsonl"), "missing")
        self.assertEqual(self.count("notices"), 1)
        rc, send2, out = self.run_day(1, cfg=self.config("nothing-here-*.jsonl"), send=send)
        self.assertEqual((rc, len(send.calls), "already sent" in out), (0, 1, True))
        self.assertEqual((self.count("windows"), self.count("sessions")), (0, 0))

    def test_problem_dry_run_writes_nothing(self):
        rc, send, out = self.run_day(1, cfg=self.config("nothing-here-*.jsonl"), dry_run=True)
        self.assertEqual((rc, send.calls), (0, []))
        self.assertIn("DATA SOURCE PROBLEM: missing", out)
        self.assertFalse(os.path.exists(self.state))

    def test_double_run_no_second_send(self):
        send = FakeSend()
        self.run_day(1, send=send)
        rc, _, out = self.run_day(1, send=send)
        self.assertEqual((rc, len(send.calls)), (0, 1))
        self.assertIn("already sent", out)

    def test_rerun_same_numbers_no_double_count(self):
        send = FakeSend()
        self.run_day(1, send=send)
        first = self.window_rows()[0]
        self.run_day(1, send=send, resend=True)
        second = self.window_rows()[0]
        self.assertEqual(len(send.calls), 2)
        self.assertEqual(send.calls[0][0], send.calls[1][0])
        self.assertEqual(self.count("sessions"), 12)
        for key in ("sessions", "unique_n", "new_n", "returning_n", "excluded_internal", "excluded_bot"):
            self.assertEqual(first[key], second[key])
        self.assertIsNotNone(second["sent_at_utc"])

    def test_dry_run_writes_nothing_and_sends_nothing(self):
        rc, send, out = self.run_day(1, dry_run=True)
        self.assertEqual((rc, send.calls), (0, []))
        self.assertIn("Sessions: 12", out)
        self.assertFalse(os.path.exists(self.state))


# ---------------------------------------------------------------- history warning and golden text

class ReportTextTests(BaseCase):
    def test_history_warning_under_7_days(self):
        _, send, _ = self.run_day(1)
        self.assertIn('HISTORY STARTS 2026-11-02 (1 of 7 days): "new" is overstated until 2026-11-09.',
                      send.calls[0][0])
        _, send, _ = self.run_day(2)
        self.assertIn("HISTORY STARTS 2026-11-02 (2 of 7 days)", send.calls[0][0])

    def test_no_warning_at_7_days(self):
        conn = lp.open_state(self.state)
        with conn:
            for day in (27, 28, 29, 30, 31):
                conn.execute("INSERT INTO windows(window_start_utc) VALUES(?)", (f"2026-10-{day}T19:00:00Z",))
            conn.execute("INSERT INTO windows(window_start_utc) VALUES('2026-11-01T19:00:00Z')")
        conn.close()
        with open(os.path.join(self.state, "salt"), "wb") as fh:
            fh.write(b"s" * 32)
        _, send, _ = self.run_day(1)
        self.assertNotIn("HISTORY", send.calls[0][0])
        self.assertIn("Sessions: 12", send.calls[0][0])

    def test_report_text_golden(self):
        _, send, _ = self.run_day(1)
        expected = "\n".join([
            "ZeRock Radio - Daily Listeners Report",
            "Window: 2026-11-02 21:00 -> 2026-11-03 21:00 (Asia/Jerusalem, 24 h)",
            "Sessions: 12",
            "Unique listeners: 9 (new 9, returning 0)",
            "Excluded internal (own systems, own network): 4",
            "Excluded bots and probes: 3",
            "No usable client IP (not counted): 1",
            "Unparsed log lines (ignored): 2",
            "Per show: UNAVAILABLE (timeline fetch failed: connection error)",
            'HISTORY STARTS 2026-11-02 (1 of 7 days): "new" is overstated until 2026-11-09.',
            "--",
            FOOTER_TEXT,
            OWN_LINE,
        ])
        self.assertEqual(send.calls[0][0], expected)
        self.assertEqual(send.calls[0][1], ["deliver", "wa"])

    def test_unparsed_line_hidden_when_zero(self):
        self.run_day(1)
        _, send, _ = self.run_day(2)
        self.assertNotIn("Unparsed", send.calls[0][0])

    def test_real_sender_appends_base64(self):
        import base64
        with mock.patch.object(lp.subprocess, "run") as fake_run:
            fake_run.side_effect = None
            fake_run.return_value = mock.Mock(returncode=0)
            self.assertEqual(lp.send_via_command("héllo", ["deliver", "wa"]), 0)
        argv = fake_run.call_args[0][0]
        self.assertEqual(argv[:2], ["deliver", "wa"])
        self.assertEqual(base64.b64decode(argv[2]).decode("utf-8"), "héllo")
        self.assertEqual(fake_run.call_args[1]["timeout"], 60)
        self.assertNotIn("shell", fake_run.call_args[1])


# ---------------------------------------------------------------- privacy

class PrivacyTests(BaseCase):
    def fixture_ips(self):
        found = set()
        for name in ("caddy_day1.jsonl", "caddy_day2.jsonl"):
            text = slurp(os.path.join(FIXTURES, name), "r")
            found.update(re.findall(r'"(?:remote_ip|client_ip)": "([^"]+)"', text))
        found.discard("not-an-ip")
        return found

    def test_no_raw_ip_in_db_or_message(self):
        texts = []
        for day in (1, 2):
            _, send, out = self.run_day(day)
            texts.extend([send.calls[0][0], out])
        blob = slurp(os.path.join(self.state, "state.db"))
        ips = self.fixture_ips()
        self.assertGreater(len(ips), 10)
        for ip in ips:
            self.assertNotIn(ip.encode(), blob, ip)
            for text in texts:
                self.assertNotIn(ip, text, ip)
        salt = slurp(os.path.join(self.state, "salt"))
        self.assertEqual(len(salt), 32)
        self.assertNotIn(salt, blob)
        self.assertNotIn(salt.hex(), "".join(texts))

    def test_salt_file_mode_0600(self):
        self.run_day(1)
        self.assertEqual(os.stat(os.path.join(self.state, "salt")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(self.state, "state.db")).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)
        salt = slurp(os.path.join(self.state, "salt"))
        self.run_day(1, resend=True)
        self.assertEqual(slurp(os.path.join(self.state, "salt")), salt)

    def test_send_failure_logs_no_ip(self):
        send = FakeSend(rc=5)
        rc, _, _ = self.run_day(1, send=send)
        self.assertEqual(rc, 2)
        log = slurp(os.path.join(self.state, "run.log"), "r")
        self.assertEqual(len(log.splitlines()), 1)
        self.assertIn("send_rc=5", log)
        self.assertIn("2026-11-02T19:00:00Z", log)
        for ip in self.fixture_ips():
            self.assertNotIn(ip, log)
        self.assertNotIn("Sessions", log)
        self.assertIsNone(self.window_rows()[0]["sent_at_utc"])
        # a later run in the same hour retries once and does not double count
        send.rc = 0
        rc, _, _ = self.run_day(1, send=send)
        self.assertEqual(rc, 0)
        self.assertEqual(len(send.calls), 2)
        self.assertEqual(self.count("sessions"), 12)
        self.assertIsNotNone(self.window_rows()[0]["sent_at_utc"])
        self.assertEqual(os.stat(os.path.join(self.state, "run.log")).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- rework 1

class RobustnessTests(BaseCase):
    def entries(self):
        return [log_entry(self.at(10 + i), f"203.0.113.{i + 1}") for i in range(40)]

    def gz_log(self, truncate=0):
        import gzip
        cfg = self.custom_log(self.entries())
        plain = os.path.join(self.logs, "custom.jsonl")
        gz = os.path.join(self.logs, "custom.jsonl.gz")
        with open(plain, "rb") as src, gzip.open(gz, "wb") as dst:
            dst.write(src.read())
        os.remove(plain)
        if truncate:
            with open(gz, "rb") as fh:
                data = fh.read()
            with open(gz, "wb") as fh:
                fh.write(data[:-truncate])
        self.touch(gz)
        cfg["caddy_log_globs"] = [gz]
        return cfg

    def test_intact_gz_baseline(self):
        rc, _, _ = self.run_day(1, cfg=self.gz_log())
        self.assertEqual((rc, self.window_rows()[0]["sessions"]), (0, 40))

    def test_truncated_gz_is_a_problem_not_a_lower_count(self):
        send = FakeSend()
        rc, _, _ = self.run_day(1, cfg=self.gz_log(truncate=40), send=send)
        self.assertEqual(rc, 0)
        text = send.calls[0][0]
        self.assertIn("DATA SOURCE PROBLEM: log file unreadable: custom.jsonl.gz.", text)
        self.assertNotIn("Sessions:", text)
        self.assertEqual((self.count("windows"), self.count("sessions")), (0, 0))

    def test_unreadable_file_is_a_problem(self):
        cfg = self.custom_log(self.entries())
        bad = os.path.join(self.logs, "broken.jsonl")
        os.mkdir(bad)  # opening a directory as a file fails like a permission error
        self.touch(bad)
        cfg["caddy_log_globs"] = [os.path.join(self.logs, "custom.jsonl"), bad]
        send = FakeSend()
        self.run_day(1, cfg=cfg, send=send)
        self.assertIn("DATA SOURCE PROBLEM: log file unreadable: broken.jsonl.", send.calls[0][0])
        self.assertEqual(self.count("windows"), 0)

    def test_old_unreadable_file_still_reported(self):
        # A file older than the window is skipped before opening; only a failure to stat is reported.
        cfg = self.custom_log(self.entries())
        dangling = os.path.join(self.logs, "gone.jsonl")
        os.symlink(os.path.join(self.logs, "nowhere"), dangling)
        cfg["caddy_log_globs"] = [os.path.join(self.logs, "custom.jsonl"), dangling]
        send = FakeSend()
        self.run_day(1, cfg=cfg, send=send)
        self.assertIn("log file unreadable: nowhere", send.calls[0][0])

    def test_future_window_refused(self):
        early = dt.datetime(2026, 11, 3, 15, 0, tzinfo=TZ).astimezone(UTC)
        send = FakeSend()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO), \
                mock.patch("sys.stderr", new_callable=__import__("io").StringIO) as err:
            rc = lp.run_report(self.config("caddy_day1.jsonl"), END1, early, send, force=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(send.calls, [])
        self.assertIn("has not ended", err.getvalue())
        self.assertFalse(os.path.exists(self.state))

    def test_future_window_dry_run_says_incomplete(self):
        early = dt.datetime(2026, 11, 3, 15, 0, tzinfo=TZ).astimezone(UTC)
        send = FakeSend()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            rc = lp.run_report(self.config("caddy_day1.jsonl"), END1, early, send, force=True, dry_run=True)
        self.assertEqual((rc, send.calls), (0, []))
        self.assertIn("INCOMPLETE WINDOW", out.getvalue())
        self.assertFalse(os.path.exists(self.state))

    def seed_history(self, days, salt=True):
        conn = lp.open_state(self.state)
        with conn:
            for day in days:
                conn.execute("INSERT INTO windows(window_start_utc) VALUES(?)",
                             (day.strftime("%Y-%m-%dT19:00:00Z"),))
        conn.close()
        if salt:
            with open(os.path.join(self.state, "salt"), "wb") as fh:
                fh.write(b"s" * 32)

    def test_gap_days_named_in_history_warning(self):
        day = dt.date(2026, 10, 27)
        self.seed_history([day + dt.timedelta(days=n) for n in (0, 1, 2, 5)])
        _, send, _ = self.run_day(1)
        text = send.calls[0][0]
        self.assertIn("MISSING DAYS: 2026-10-30, 2026-10-31 (no stored report):", text)
        self.assertIn('"new" may be overstated.', text)

    def test_no_gap_line_when_contiguous(self):
        self.seed_history([dt.date(2026, 11, 1)])
        _, send, _ = self.run_day(1)
        self.assertNotIn("MISSING", send.calls[0][0])

    def test_missing_salt_with_history_is_a_problem(self):
        self.run_day(1)
        os.remove(os.path.join(self.state, "salt"))
        send = FakeSend()
        rc, _, _ = self.run_day(2, send=send)
        self.assertEqual(rc, 0)
        self.assertIn("DATA SOURCE PROBLEM: salt file missing while history exists", send.calls[0][0])
        self.assertNotIn("Sessions:", send.calls[0][0])
        self.assertFalse(os.path.exists(os.path.join(self.state, "salt")))
        self.assertEqual(self.count("windows"), 1)

    def test_missing_salt_dry_run_reports_not_comparable(self):
        self.run_day(1)
        os.remove(os.path.join(self.state, "salt"))
        rc, send, out = self.run_day(2, dry_run=True)
        self.assertEqual((rc, send.calls), (0, []))
        self.assertIn("not comparable", out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "salt")))

    def test_empty_salt_file_is_a_problem(self):
        os.makedirs(self.state, mode=0o700)
        open(os.path.join(self.state, "salt"), "wb").close()
        rc, send, _ = self.run_day(1)
        self.assertEqual(rc, 0)
        self.assertIn("DATA SOURCE PROBLEM: salt file empty", send.calls[0][0])
        self.assertEqual(os.path.getsize(os.path.join(self.state, "salt")), 0)
        self.assertEqual(self.count("windows"), 0)

    def test_fresh_state_creates_salt(self):
        self.run_day(1)
        self.assertEqual(len(slurp(os.path.join(self.state, "salt"))), 32)

    def window_text(self, end_local):
        start_local, end = lp.compute_window(end_local, TZ)
        stamps = [start_local - dt.timedelta(minutes=1), start_local,
                  start_local + dt.timedelta(hours=5), end - dt.timedelta(seconds=1), end,
                  end + dt.timedelta(minutes=5)]
        entries = [log_entry(w.astimezone(UTC), f"203.0.113.{i + 1}") for i, w in enumerate(stamps)]
        entries.append(log_entry((start_local - dt.timedelta(minutes=2)).astimezone(UTC),
                                 "198.51.100.1", method="HEAD"))
        entries.append(log_entry((end + dt.timedelta(minutes=10)).astimezone(UTC),
                                 "198.51.100.1", method="HEAD"))
        path = os.path.join(self.logs, "dst.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in entries) + "\n")
        self.touch(path)
        cfg = self.config("dst.jsonl")
        now = (end + dt.timedelta(minutes=15)).astimezone(UTC)
        send = FakeSend()
        with mock.patch("sys.stdout", new_callable=__import__("io").StringIO):
            rc = lp.run_report(cfg, end_local, now, send, force=True)
        self.assertEqual(rc, 0)
        return send.calls[0][0]

    def test_dst_days_count_only_inside_window(self):
        for end, hours in ((dt.datetime(2026, 3, 27, 21, 0), 23), (dt.datetime(2026, 10, 25, 21, 0), 25)):
            with self.subTest(hours=hours):
                shutil.rmtree(self.state, ignore_errors=True)
                text = self.window_text(end)
                self.assertIn(f", {hours} h)", text)
                self.assertIn("Sessions: 3\n", text)  # start (inclusive), mid, last second; not end

    def test_truncated_mmdb_never_crashes(self):
        blob = build_mmdb(GEO_ENTRIES)
        for cut in (1, 10, 30, len(blob) // 2, len(blob) - 5, len(blob) - 60):
            path = os.path.join(self.tmp, f"cut{cut}.mmdb")
            with open(path, "wb") as fh:
                fh.write(blob[:cut])
            try:
                reader = MmdbReader(path)
            except ValueError:
                continue
            try:
                for ip in ("203.0.113.7", "192.0.2.5", "2001:db8::1"):
                    try:
                        reader.lookup(ip)
                    except ValueError:
                        pass
            finally:
                reader.close()
            _, send, _ = self.run_day(1, cfg=self.config("caddy_day1.jsonl", mmdb_path=path), resend=True)
            self.assertIn("Sessions: 12", send.calls[0][0])
            self.assertNotIn("Countries", send.calls[0][0])

    def hostile(self, data_prefix, fill):
        blob = bytearray(build_mmdb(GEO_ENTRIES))
        probe_path = os.path.join(self.tmp, "probe.mmdb")
        with open(probe_path, "wb") as fh:
            fh.write(bytes(blob))
        probe = MmdbReader(probe_path)
        start, meta = probe._data_start, bytes(blob).rfind(b"\xab\xcd\xefMaxMind.com")
        probe.close()
        body = (data_prefix + fill * ((meta - start) // len(fill) + 1))[:meta - start]
        blob[start:meta] = body
        path = os.path.join(self.tmp, "hostile.mmdb")
        with open(path, "wb") as fh:
            fh.write(bytes(blob))
        return path

    def check_hostile(self, path):
        reader = MmdbReader(path)
        try:
            for ip in ("203.0.113.7", "203.0.113.200", "192.0.2.5", "2001:db8::1"):
                try:
                    reader.lookup(ip)
                except ValueError:
                    pass
        finally:
            reader.close()
        _, send, _ = self.run_day(1, cfg=self.config("caddy_day1.jsonl", mmdb_path=path))
        self.assertIn("Sessions: 12", send.calls[0][0])
        self.assertIn("Unique listeners: 9", send.calls[0][0])

    def test_hostile_mmdb_pointer_loop(self):
        self.check_hostile(self.hostile(b"", b"\x20\x00"))

    def test_hostile_mmdb_huge_map(self):
        self.check_hostile(self.hostile(b"\xff\xff\xff\xff", b"\x00"))

    def test_duplicate_glob_paths_count_once(self):
        cfg = self.custom_log([log_entry(self.at(10), "203.0.113.7")])
        os.symlink(os.path.join(self.logs, "custom.jsonl"), os.path.join(self.logs, "custom_link.jsonl"))
        cfg["caddy_log_globs"] = [os.path.join(self.logs, "custom.jsonl"),
                                  os.path.join(self.logs, "custom_link.jsonl"),
                                  os.path.join(self.logs, "custom*.jsonl")]
        self.run_day(1, cfg=cfg)
        self.assertEqual(self.window_rows()[0]["sessions"], 1)
        self.assertEqual(self.count("sessions"), 1)

    def test_run_lock_busy_does_nothing(self):
        import fcntl
        os.makedirs(self.state, mode=0o700)
        fd = os.open(os.path.join(self.state, "run.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc, send, out = self.run_day(1)
        finally:
            os.close(fd)
        self.assertEqual((rc, send.calls), (0, []))
        self.assertIn("another run is in progress", out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "state.db")))
        rc, send, _ = self.run_day(1)
        self.assertEqual(len(send.calls), 1)

    def test_sent_at_uses_injected_clock(self):
        self.run_day(1)
        self.assertEqual(self.window_rows()[0]["sent_at_utc"], lp.utc_iso(NOW1))

    def test_own_network_line_only_when_not_configured(self):
        _, send, _ = self.run_day(1)
        self.assertTrue(send.calls[0][0].endswith(OWN_LINE))
        cfg = self.config("caddy_day1.jsonl", own_public_ips=["198.51.100.99"])
        _, send, _ = self.run_day(1, cfg=cfg, resend=True)
        self.assertNotIn("Own network not excluded", send.calls[0][0])

    def test_narrow_bot_markers(self):
        for ua in ("RoboticBotanist/1.0 (phone app)", "MyUptimeRadio/2"):
            self.assertFalse(lp.is_bot(ua), ua)
        for ua in ("Mozilla/5.0 (compatible; Googlebot/2.1)", "UptimeRobot/2.0", "Amazonbot/0.1"):
            self.assertTrue(lp.is_bot(ua), ua)


# ---------------------------------------------------------------- real entry path (load_config / main)

class FakeClockDt:
    """Stand-in for the dt module inside listener_report, with a fixed 'now'."""

    def __init__(self, now):
        ns = dict(vars(dt))

        class FixedDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)

        ns["datetime"] = FixedDatetime
        self.__dict__.update(ns)


# ---------------------------------------------------------------- per-show listeners

def sample(local_text, show):
    return (dt.datetime.strptime(local_text, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ), show)


def make_fetch(by_date, calls=None):
    """Fake fetch(url): by_date maps YYYY-MM-DD to a payload (or an Exception to raise)."""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        value = by_date[url.rsplit("date=", 1)[1]]
        if isinstance(value, Exception):
            raise value
        return value
    return fetch


def full_day_records(day, show="Rocky", only=None):
    out = []
    for i in range(96):
        hh, mm = divmod(i * 15, 60)
        out.append({"ts": f"{day}T{hh:02d}:{mm:02d}", "show": show, "local": 1, "title": "t"})
    return [r for r in out if only is None or only(r)]


def window_payloads(overrides=None, drop=0):
    """Complete records for the 2026-11-02 21:00 -> 2026-11-03 21:00 window, optional show overrides."""
    d1 = [r for r in full_day_records("2026-11-02") if r["ts"] >= "2026-11-02T21:00"]
    d2 = [r for r in full_day_records("2026-11-03") if r["ts"] < "2026-11-03T21:00"]
    allr = d1 + d2
    for ts, show in (overrides or {}).items():
        for r in allr:
            if r["ts"] == ts:
                r["show"] = show
    if drop:
        allr = allr[:-drop]
    return {"2026-11-02": {"records": [r for r in allr if r["ts"].startswith("2026-11-02")]},
            "2026-11-03": {"records": [r for r in allr if r["ts"].startswith("2026-11-03")]}}


def T(minutes_from_start):
    return START1.timestamp() + minutes_from_start * 60


class ShowOverlapTests(unittest.TestCase):
    SAMPLES = [sample("2026-11-02T22:00", "Show A"), sample("2026-11-02T22:15", "Show B")]

    def counts(self, sessions, samples=None):
        return dict(lp.show_listeners(sessions, samples or self.SAMPLES))

    def test_show_overlap_straddles_two_shows(self):
        # 22:10 -> 22:20 touches both samples
        sess = [(T(80), "d1", T(70))]
        self.assertEqual(self.counts(sess), {"Show A": 1, "Show B": 1})

    def test_session_at_boundary_not_counted(self):
        # ends exactly at 22:15 (T of Show B) and starts exactly where Show A ends
        sess = [(T(75), "d1", T(65))]
        self.assertEqual(self.counts(sess), {"Show A": 1})
        # starts exactly at the end of the last sample (22:30)
        self.assertEqual(self.counts([(T(100), "d2", T(90))]), {})

    def test_zero_length_session_point(self):
        self.assertEqual(self.counts([(T(60), "d1", T(60))]), {"Show A": 1})  # at T: counts
        self.assertEqual(self.counts([(T(75), "d1", T(75))]), {"Show B": 1})  # belongs to the later sample
        self.assertEqual(self.counts([(T(90), "d1", T(90))]), {})  # at the end of the last sample

    def test_rocky_rotation_line(self):
        samples = [sample("2026-11-02T22:00", "Rocky"), sample("2026-11-02T22:15", "")]
        self.assertEqual(self.counts([(T(70), "d1", T(70)), (T(80), "d2", T(80))], samples),
                         {"Rocky rotation (no show)": 2})
        text = lp.render_per_show({"shows": [("Rocky rotation (no show)", 2)], "error": None,
                                   "missing": 0, "expected": 96})
        self.assertEqual(text, ["Listeners per show:", "  Rocky rotation (no show): 2"])

    def test_shows_sorted_desc(self):
        samples = [sample("2026-11-02T22:00", "B"), sample("2026-11-02T22:15", "A"),
                   sample("2026-11-02T22:30", "C")]
        sess = [(T(a), f"d{i}", T(a)) for i, a in enumerate((61, 62, 76, 77, 91))]
        sess += [(T(63), "d9", T(63))]
        self.assertEqual(lp.show_listeners(sess, samples), [("B", 3), ("A", 2), ("C", 1)])
        ties = lp.show_listeners([(T(61), "d1", T(61)), (T(76), "d2", T(76))], samples)
        self.assertEqual(ties, [("A", 1), ("B", 1)])

    def test_host_segment_collapsed(self):
        self.assertEqual(lp.normalize_show("A \u2014 B \u2014 B"), "A \u2014 B")
        self.assertEqual(lp.normalize_show("A \u2014 B"), "A \u2014 B")
        self.assertEqual(lp.normalize_show("Al Haroker"), "Al Haroker")

    def test_same_device_counted_once_per_show(self):
        sess = [(T(61), "d1", T(61)), (T(70), "d1", T(65)), (T(62), "d2", T(62))]
        self.assertEqual(self.counts(sess), {"Show A": 2})

    def test_hebrew_show_names_pass_through(self):
        samples = [sample("2026-11-02T22:00", "\u05e1\u05df \u2014 \u05d3\u05e0\u05d4 \u2014 \u05d3\u05e0\u05d4")]
        self.assertEqual(lp.show_listeners([(T(61), "d", T(61))], samples),
                         [("\u05e1\u05df \u2014 \u05d3\u05e0\u05d4", 1)])


class TimelineTests(BaseCase):
    START = dt.datetime(2026, 11, 2, 21, 0, tzinfo=TZ)
    END = dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ)

    def test_timeline_fetch_failure_unavailable(self):
        fetch = make_fetch({"2026-11-02": OSError("x"), "2026-11-03": TimeoutError()})
        samples, err = lp.fetch_show_timeline(self.START, self.END, TZ, fetch)
        self.assertEqual((samples, err), ([], "timeline fetch failed: connection error"))
        _, send, _ = self.run_day(1, timeline_fetch=make_fetch(
            {"2026-11-02": TimeoutError(), "2026-11-03": TimeoutError()}))
        self.assertIn("Per show: UNAVAILABLE (timeline fetch failed: timed out)", send.calls[0][0])
        self.assertIn("Sessions: 12", send.calls[0][0])
        self.assertEqual(len(self.window_rows()), 1)
        _, err = lp.fetch_show_timeline(self.START, self.END, TZ, make_fetch(
            {"2026-11-02": {"nope": 1}, "2026-11-03": "text"}))
        self.assertEqual(err, "unreadable timeline")

    def test_fetch_retries_once(self):
        calls = []
        outcomes = [OSError("x"), window_payloads()["2026-11-02"], window_payloads()["2026-11-03"]]

        def fetch(url):
            calls.append(url)
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        samples, err = lp.fetch_show_timeline(self.START, self.END, TZ, fetch)
        self.assertEqual((len(calls), err, len(samples)), (3, None, 96))

    def test_timeline_no_samples_unavailable(self):
        fetch = make_fetch({"2026-11-02": {"records": []}, "2026-11-03": {"records": []}})
        _, send, _ = self.run_day(1, timeline_fetch=fetch)
        self.assertIn("Per show: UNAVAILABLE (no samples in window)", send.calls[0][0])

    def test_timeline_incomplete_line(self):
        payloads = window_payloads({"2026-11-02T22:00": "Show A"}, drop=3)
        _, send, _ = self.run_day(1, timeline_fetch=make_fetch(payloads))
        lines = send.calls[0][0].splitlines()
        self.assertIn("Per-show timeline incomplete: 3 of 96 samples missing", lines)
        i = lines.index("Per-show timeline incomplete: 3 of 96 samples missing")
        self.assertTrue(lines[i - 1].startswith("  ") or lines[i - 1] == "Listeners per show: none")
        self.assertNotIn("UNAVAILABLE", send.calls[0][0])

    def test_complete_timeline_lists_shows(self):
        # fixture day 1 sessions have no duration: points. Put a show on the whole window.
        payloads = window_payloads({f"2026-11-02T21:{m:02d}": "Show A" for m in (0, 15, 30, 45)})
        _, send, _ = self.run_day(1, timeline_fetch=make_fetch(payloads))
        text = send.calls[0][0]
        self.assertNotIn("incomplete", text)
        self.assertIn("Listeners per show:\n  Rocky rotation (no show):", text)

    def test_two_dates_fetched_and_filtered(self):
        calls = []
        payloads = window_payloads()
        payloads["2026-11-02"]["records"].append({"ts": "2026-11-02T20:45", "show": "Out"})
        payloads["2026-11-03"]["records"].append({"ts": "2026-11-03T21:00", "show": "Out"})
        payloads["2026-11-03"]["records"] = [r for r in payloads["2026-11-03"]["records"]
                                             if r["ts"] != "2026-11-03T10:00"]
        payloads["2026-11-03"]["records"] += [{"ts": "garbage", "show": "x"}, {"ts": 5, "show": "x"},
                                              "junk", {"ts": "2026-11-03T10:00", "show": 7}]
        samples, err = lp.fetch_show_timeline(self.START, self.END, TZ, make_fetch(payloads, calls))
        self.assertIsNone(err)
        self.assertEqual([u.rsplit("date=", 1)[1] for u in calls], ["2026-11-02", "2026-11-03"])
        self.assertTrue(all(u.startswith("http://192.168.1.166:5000/api/listener-stats/history?date=")
                            for u in calls))
        self.assertEqual(len(samples), 95)  # the show=7 sample is malformed and missing
        self.assertTrue(all(self.START <= t < self.END for t, _ in samples))
        self.assertNotIn("Out", [show for _, show in samples])

    def test_one_date_failing_gives_missing_samples_not_unavailable(self):
        payloads = window_payloads()
        payloads["2026-11-02"] = OSError("x")
        samples, err = lp.fetch_show_timeline(self.START, self.END, TZ, make_fetch(payloads))
        self.assertIsNone(err)
        self.assertEqual(len(samples), 84)

    def test_session_duration_gives_start(self):
        entries = [dict(log_entry(self.at(80), "203.0.113.7"), duration=600.0),
                   dict(log_entry(self.at(80), "203.0.113.8"), duration=-5),
                   dict(log_entry(self.at(80), "203.0.113.9"), duration=True),
                   dict(log_entry(self.at(80), "203.0.113.10"), duration="x")]
        scan = lp.scan_logs(lp.normalize_config(self.custom_log(entries)), START1.timestamp(),
                            START1.timestamp() + 86400, b"s" * 32)
        starts = sorted(round(ts - start) for ts, _d, start in scan["sessions"])
        self.assertEqual(starts, [0, 0, 0, 600])

    def test_no_geo_lines_or_footer(self):
        _, send, _ = self.run_day(1)
        text = send.calls[0][0]
        for word in ("Countries", "Cities", "DB-IP", "Locations approximate", "Geolocation"):
            self.assertNotIn(word, text)

    def test_mmdb_path_accepted_ignored(self):
        cfg = self.config("caddy_day1.jsonl", mmdb_path=os.path.join(self.tmp, "does-not-exist.mmdb"))
        with mock.patch.object(MmdbReader, "__init__", side_effect=AssertionError("must not open")):
            _, send, _ = self.run_day(1, cfg=cfg)
        self.assertIn("Sessions: 12", send.calls[0][0])
        self.assertEqual(self.window_rows()[0]["geo_status"], "off")

    def test_footer_per_show_note(self):
        _, send, _ = self.run_day(1)
        self.assertIn("Per-show numbers do not add up to the total (a listener present during two "
                      "shows counts in both); show boundaries are approximate to 15 minutes.",
                      send.calls[0][0])


class DstAndRobustTimelineTests(unittest.TestCase):
    UTC = dt.timezone.utc

    def payloads(self, start, end, skip=(), extra=()):
        """Records every 15 real minutes in [start, end); local labels, grouped by local date."""
        out, t = {}, start.timestamp()
        while t < end.timestamp():
            loc = dt.datetime.fromtimestamp(t, self.UTC).astimezone(TZ)
            if loc.strftime("%Y-%m-%dT%H:%M") not in skip or loc.fold:
                out.setdefault(f"{loc:%Y-%m-%d}", []).append(
                    {"ts": f"{loc:%Y-%m-%dT%H:%M}", "show": "Show A"})
            t += 900
        for ts in extra:
            out.setdefault(ts[:10], []).append({"ts": ts, "show": "Show A"})
        return {d: {"records": r} for d, r in out.items()}

    def check(self, day, expected):
        start = dt.datetime(*day, 21, 0, tzinfo=TZ)
        end = start + dt.timedelta(days=1)
        end = dt.datetime(end.year, end.month, end.day, 21, 0, tzinfo=TZ)
        res = lp.per_show_result([], start, end, TZ, make_fetch(self.payloads(start, end)))
        self.assertEqual((res["expected"], res["missing"], res["error"]), (expected, 0, None))
        self.assertNotIn("incomplete", "\n".join(lp.render_per_show(res)))

    def test_spring_dst_complete_timeline(self):
        self.check((2026, 3, 26), 92)

    def test_fall_dst_complete_timeline(self):
        self.check((2026, 10, 24), 100)

    def test_fall_back_hour_keeps_both_occurrences(self):
        recs = [{"ts": f"2026-10-25T01:{m:02d}", "show": "S"} for m in (0, 15, 30, 45)] * 2
        out = lp._parse_samples({"records": recs}, TZ)
        self.assertEqual(len(out), 8)
        offs = sorted({t.utcoffset() for t, _ in out.values()})
        self.assertEqual(len(offs), 2)

    def test_unresolvable_duplicate_reported_incomplete(self):
        recs = [{"ts": "2026-10-25T01:00", "show": "S"}] * 3
        self.assertEqual(lp._parse_samples({"records": recs}, TZ), {})
        recs = [{"ts": "2026-11-02T10:00", "show": "S"}] * 2
        self.assertEqual(lp._parse_samples({"records": recs}, TZ), {})

    def test_off_grid_sample_does_not_mask_missing(self):
        start = dt.datetime(2026, 11, 2, 21, 0, tzinfo=TZ)
        end = dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ)
        pl = self.payloads(start, end, skip=("2026-11-02T22:00",), extra=("2026-11-02T22:07",))
        res = lp.per_show_result([], start, end, TZ, make_fetch(pl))
        self.assertEqual((res["expected"], res["missing"]), (96, 1))

    def test_rejects_non_http_urls(self):
        for url in ("file:///etc/passwd", "ftp://x/y"):
            with self.assertRaises(lp._Unreadable):
                lp._http_get_json(url)
        _, err = lp.fetch_show_timeline(
            dt.datetime(2026, 11, 2, 21, 0, tzinfo=TZ), dt.datetime(2026, 11, 3, 21, 0, tzinfo=TZ),
            TZ, url_base="file:///etc/passwd")
        self.assertEqual(err, "unreadable timeline")

    def test_show_name_sanitised(self):
        res = {"shows": [("A\nB\x00\x1bC", 2), ("\u05e9\u05d5 Show", 1)], "error": None,
               "missing": 0, "expected": 96}
        lines = lp.render_per_show(res)
        self.assertEqual(lines, ["Listeners per show:", "  A B  C: 2", "  \u05e9\u05d5 Show: 1"])


class RealPathTests(BaseCase):
    def write_cfg(self, **overrides):
        cfg = {"caddy_log_globs": [os.path.join(self.logs, "caddy_day1.jsonl")],
               "site_host": "rocky.kupernet.com", "state_dir": self.state,
               "own_public_ips": [], "stale_after_minutes": 30, "timezone": "Asia/Jerusalem",
               "report_hour": 21, "delivery_argv": ["deliver", "wa"]}
        cfg.update(overrides)
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        return path

    def main_dry(self, cfg_path):
        argv = ["--config", cfg_path, "--window-end", "2026-11-03T21:00", "--force", "--dry-run"]
        send = FakeSend()
        with mock.patch.object(lp, "dt", FakeClockDt(NOW1)), \
                mock.patch.object(lp, "send_via_command", send), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
            rc = lp.main(argv)
        return rc, send, out.getvalue()

    def test_parse_ip_accepts_address_objects(self):
        self.assertEqual(lp.parse_ip(ipaddress.ip_address("203.0.113.5")), ipaddress.ip_address("203.0.113.5"))
        self.assertEqual(lp.parse_ip(ipaddress.ip_address("::ffff:203.0.113.5")),
                         ipaddress.ip_address("203.0.113.5"))
        self.assertIsNone(lp.parse_ip(5))

    def test_normalize_config_idempotent(self):
        once = lp.normalize_config({"caddy_log_globs": ["x"], "state_dir": "y",
                                    "own_public_ips": ["203.0.113.5", "2001:db8::1"]})
        twice = lp.normalize_config(once)
        self.assertEqual(once["own_public_ips"], twice["own_public_ips"])

    def test_load_config_ipv4_ipv6(self):
        cfg = lp.load_config(self.write_cfg(own_public_ips=["203.0.113.5", "2001:db8::1"]))
        self.assertEqual(cfg["own_public_ips"],
                         [ipaddress.ip_address("203.0.113.5"), ipaddress.ip_address("2001:db8::1")])

    def test_load_config_invalid_entry_raises(self):
        with self.assertRaisesRegex(ValueError, "invalid entry in own_public_ips"):
            lp.load_config(self.write_cfg(own_public_ips=["203.0.113.5", "not-an-ip"]))

    def test_main_dry_run_with_own_ip_excludes_and_writes_nothing(self):
        rc, send, base = self.main_dry(self.write_cfg())
        self.assertEqual(rc, 0)
        self.assertIn("Own network not excluded", base)
        m = re.search(r"Excluded internal \(own systems, own network\): (\d+)", base)
        self.assertIsNotNone(m)
        rc, send, text = self.main_dry(self.write_cfg(own_public_ips=["198.51.100.20"]))
        self.assertEqual(rc, 0)
        self.assertNotIn("Own network not excluded", text)
        m2 = re.search(r"Excluded internal \(own systems, own network\): (\d+)", text)
        self.assertGreater(int(m2.group(1)), int(m.group(1)))
        self.assertEqual(send.calls, [])
        self.assertFalse(os.path.exists(self.state))

    def test_main_full_production_keys_dry_run_never_sends(self):
        path = self.write_cfg(own_public_ips=["203.0.113.5"])
        rc, send, text = self.main_dry(path)
        self.assertEqual(rc, 0)
        self.assertIn("Window: 2026-11-02 21:00 -> 2026-11-03 21:00", text)
        self.assertEqual(send.calls, [])
