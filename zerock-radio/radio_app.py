#!/usr/bin/env python3
"""
ZeRock Radio — Web interface & show scheduler
Runs on port 5000. Communicates with Liquidsoap via telnet on port 1234.
"""

import os, glob, json, random, socket, threading, time, shutil, hashlib, secrets, calendar as _calendar, subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests as _requests
from datetime import datetime, timedelta, date as _date
from flask import Flask, render_template, request, jsonify, redirect, url_for

_lq_lock = threading.Lock()

# ─── Config ───────────────────────────────────────────────────────────────────
RADIO_DIR    = "/home/roy/zerock-radio"
LOCAL_TEMP   = f"{RADIO_DIR}/shows"          # fast local landing pad for uploads
NAS_TEMP     = "/mnt/nas/Music/ZeRock_Temp"  # final destination on NAS
SCHEDULE_FILE = f"{RADIO_DIR}/schedule.json"
BOARD_CANCELLATIONS_FILE = f"{RADIO_DIR}/board_cancellations.json"

# Shows that only appear on the weekly board when an episode is queued (uploaded).
# All other shows with a fixed day appear every week automatically.
QUEUE_ONLY_BOARD_SHOWS = {'al_harocker', 'erev_albumim'}
NOW_PLAYING_FILE = f"{RADIO_DIR}/now_playing.txt"
HISTORY_FILE  = f"{RADIO_DIR}/play_history.json"
JINGLES_DIR  = "/mnt/nas/Music/Music Reorganized/jingles"
QUIET_JINGLE = f"{JINGLES_DIR}/quiet.wav"   # played at zikaron mode start/end
LQ_HOST      = "127.0.0.1"
LQ_PORT      = 1234

PLAYLIST_DIR     = f"{RADIO_DIR}/playlists"
ENGLISH_PLAYLIST = f"{PLAYLIST_DIR}/english.m3u"
HEBREW_PLAYLIST  = f"{PLAYLIST_DIR}/hebrew.m3u"
JINGLES_PLAYLIST = f"{PLAYLIST_DIR}/jingles.m3u"
ZIKARON_PLAYLIST = f"{PLAYLIST_DIR}/zikaron.m3u"
ENGLISH_MUSIC_DIR = "/mnt/nas/Music/Music Reorganized/English"
HEBREW_MUSIC_DIR  = "/mnt/nas/Music/Music Reorganized/Hebrew"
ZIKARON_DIR      = "/mnt/nas/Music/Zikaron"
EXCLUDED_FILE    = f"{RADIO_DIR}/excluded_tracks.json"
ZIKARON_FILE     = f"{RADIO_DIR}/zikaron_schedule.json"
STREAM_STATES_FILE = f"{RADIO_DIR}/stream_states.json"
MITZAD_DIR       = "/mnt/nas/Music/mitsad"

# פל"ש insertion points: after מקום N → use פל"ש index (0-based)
MATZAD_PALASH_AFTER = {17: 0, 14: 1, 11: 2, 7: 3, 4: 4}

# Badge jingles played after MAKAOM N and before the song
MATZAD_BADGE_FILES = {
    'aliya':     'העלייה הגבוהה.mp3',
    'yerida':    'הירידה הגדולה.mp3',
    'knisa':     'הכניסה הגבוהה.mp3',
    'knisa_new': 'כניסה חדשה.mp3',
}

UPLOADER_URL      = "http://192.168.1.114:3001/api/upload"
UPLOADER_BASE_URL = "http://192.168.1.114:3001"

# ─── WordPress direct API (for WP post creation/verification without re-upload) ─
WP_URL      = os.environ.get('WP_URL',      'https://zerockradio.com')
WP_USERNAME = os.environ.get('WP_USERNAME', '')
WP_APP_PASS = os.environ.get('WP_APP_PASSWORD', '')

# Mirrored from server.js — slug prefix and featured image media ID per show name
_WP_SHOW_SLUGS = {
    'Beat-oN מקומי':      'beat-on',
    'Black Parade':        'black-parade',
    'ON AIR':              'on-air',
    'On the Mend':         'mend',
    'Oy Vavoy':            'oy-vavoy',
    'RockTrip':            'rocktrip',
    'Shabi On The Rocks':  'sotr',
    'Stage Dive':          'stage-dive',
    'The Breakdown':       'breakdown',
    'Time Warp':           'time-warp',
    'אני לא בפסקול':      'pascal',
    'האחות':               'nurse',
    'השאלטר':              'hash',
    'זה פרוג':             'prog',
    'זה רוק פורטה':        'forte',
    'נגד כיוון הזיפים':   'zifim',
    'סינגלס':              'singles',
    'סן פטרוק':            'patrock',
    'על הרוקר':            'al-harocker',
    'פטרוק לילה':          'patrock',
}
_WP_FEATURED_IMAGES = {
    'Beat-oN מקומי':      14326,
    'Black Parade':        375,
    'ON AIR':              10872,
    'On the Mend':         14312,
    'Oy Vavoy':            8064,
    'RockTrip':            14447,
    'Shabi On The Rocks':  563,
    'Stage Dive':          12842,
    'The Breakdown':       8062,
    'Time Warp':           12266,
    'אני לא בפסקול':      461,
    'האחות':               12987,
    'השאלטר':              10875,
    'זה פרוג':             2085,
    'זה רוק פורטה':        4382,
    'נגד כיוון הזיפים':   450,
    'סינגלס':              374,
    'סן פטרוק':            389,
    'על הרוקר':            769,
    'פטרוק לילה':          388,
}
# WP shows taxonomy term IDs (from /wp-json/wp/v2/shows)
_WP_SHOW_IDS = {
    'Beat-oN מקומי':      317,
    'Black Parade':        49,
    'ON AIR':              305,
    'On the Mend':         316,
    'Oy Vavoy':            71,
    'RockTrip':            318,
    'Shabi On The Rocks':  53,
    'Stage Dive':          313,
    'The Breakdown':       253,
    'Time Warp':           308,
    'אני לא בפסקול':      43,
    'האחות':               314,
    'השאלטר':              306,
    'זה פרוג':             149,
    'זה רוק פורטה':        45,
    'נגד כיוון הזיפים':   42,
    'סינגלס':              48,
    'סן פטרוק':            44,
    'ערב של אלבומים':      60,
    'על הרוקר':            38,
    'פטרוק לילה':          50,
}

# Podbean API credentials (for fetching CDN media_url when WP post needs to be created)
PODBEAN_CLIENT_ID     = os.environ.get('PODBEAN_CLIENT_ID', '')
PODBEAN_CLIENT_SECRET = os.environ.get('PODBEAN_CLIENT_SECRET', '')

# ─── Al HaRoker self-service scheduling ───────────────────────────────────────
AL_HAROKER_BOOKINGS_FILE    = f"{RADIO_DIR}/al_haroker_bookings.json"
AL_HAROKER_SUBSCRIBERS_FILE = f"{RADIO_DIR}/al_haroker_subscribers.json"
AL_HAROKER_MONTHLY_SENT_FILE= f"{RADIO_DIR}/al_haroker_monthly_sent.json"
AL_HAROKER_SCHEDULE_START   = _date(2026, 5, 1)   # first bookable date
AL_HAROKER_BROADCAST_HOUR   = 7                   # 07:00
AL_HAROKER_UPLOAD_HOUR      = 8                   # 08:00
# Python weekday: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
AL_HAROKER_AVAILABLE_WEEKDAYS = {6, 0, 1, 2, 3}   # Sun–Thu

# Email config — set on the Rocky server via environment variables:
#   export ZEROCK_SMTP_HOST=smtp.gmail.com
#   export ZEROCK_SMTP_PORT=587
#   export ZEROCK_SMTP_USER=radio@zerockradio.com
#   export ZEROCK_SMTP_PASS=your_app_password
#   export ZEROCK_SMTP_FROM=ZeRock Radio <radio@zerockradio.com>
#   export ZEROCK_PUBLIC_URL=http://zerock.kupernet.com:5000
SMTP_HOST         = os.environ.get('ZEROCK_SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT         = int(os.environ.get('ZEROCK_SMTP_PORT', '587'))
SMTP_USER         = os.environ.get('ZEROCK_SMTP_USER', '')
SMTP_PASS         = os.environ.get('ZEROCK_SMTP_PASS', '')
SMTP_FROM_ADDR    = os.environ.get('ZEROCK_SMTP_FROM', 'ZeRock Radio <radio@zerockradio.com>')
ZEROCK_PUBLIC_URL = os.environ.get('ZEROCK_PUBLIC_URL', 'http://zerock.kupernet.com:5000')

# Shows excluded from the auto-rerun feature (no Podbean episodes to pull from)
AUTO_RERUN_EXCLUDED = {'al_harocker', 'erev_albumim', 'matzad_harok'}
# Auth token for zerock uploader API (SHA-256 of the login password)
_UPLOADER_AUTH = hashlib.sha256(b'YudaKaka2026!').hexdigest()

app = Flask(__name__, template_folder=f"{RADIO_DIR}/templates")
os.makedirs(LOCAL_TEMP, exist_ok=True)
os.makedirs(NAS_TEMP, exist_ok=True)

# ─── Show broadcast schedule ──────────────────────────────────────────────────
# day: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun  None=manual date
# rerun_days_offset: days after first broadcast (0=same day, None=no rerun)
# wp_show_id: WordPress show taxonomy term ID (fill in from WP admin)
SHOW_SCHEDULE = [
    {'key': 'al_harocker',          'name': 'על הרוקר',           'broadcaster': '',              'day': None, 'time': '07:00', 'upload_time': '08:00', 'rerun_days_offset': None, 'rerun_time': None,  'wp_show_id': ''},
    {'key': 'rocktrip',             'name': 'RockTrip',            'broadcaster': 'אלעד אביגן',   'day': 3,    'time': '09:00', 'upload_time': '10:00', 'rerun_days_offset': 3,    'rerun_time': '08:00','wp_show_id': ''},
    {'key': 'zifim',                'name': 'נגד כיוון הזיפים',   'broadcaster': 'אחיעד לוק',    'day': 6,    'time': '09:00', 'upload_time': '11:00', 'rerun_days_offset': 2,    'rerun_time': '13:00','wp_show_id': ''},
    {'key': 'black_parade',         'name': 'Black Parade',        'broadcaster': 'מתן בכור',     'day': 6,    'time': '13:00', 'upload_time': '14:00', 'rerun_days_offset': 3,    'rerun_time': '09:00','wp_show_id': ''},
    {'key': 'pascal',               'name': 'אני לא בפסקול',      'broadcaster': 'שיר אסולין',   'day': 6,    'time': '17:00', 'upload_time': '19:00', 'rerun_days_offset': 3,    'rerun_time': '14:00','wp_show_id': ''},
    {'key': 'patrock_laila_eyal',   'name': 'פטרוק לילה',         'broadcaster': 'איל אורטל',    'day': 6,    'time': '19:00', 'upload_time': '20:00', 'rerun_days_offset': 6,    'rerun_time': '09:00','wp_show_id': ''},
    {'key': 'patrock_laila_eliran', 'name': 'פטרוק לילה',         'broadcaster': 'אלירן קטנוב',  'day': 1,    'time': '19:00', 'upload_time': '20:00', 'rerun_days_offset': 4,    'rerun_time': '12:00','wp_show_id': ''},
    {'key': 'patrock_laila_meir',   'name': 'פטרוק לילה',         'broadcaster': 'מאיר הוברמן',  'day': 2,    'time': '20:00', 'upload_time': '21:00', 'rerun_days_offset': 3,    'rerun_time': '13:00','wp_show_id': ''},
    {'key': 'hashulter',            'name': 'השאלטר',              'broadcaster': 'דוד שאבי',     'day': 0,    'time': '08:00', 'upload_time': '09:00', 'rerun_days_offset': 3,    'rerun_time': '12:00','wp_show_id': ''},
    {'key': 'on_air',               'name': 'On Air',              'broadcaster': 'רועי קופרמן',  'day': 0,    'time': '09:00', 'upload_time': '10:00', 'rerun_days_offset': 2,    'rerun_time': '18:00','wp_show_id': ''},
    {'key': 'oy_vavoy',             'name': 'Oy Vavoy',            'broadcaster': 'יותם "דפיילר" אבני', 'day': 1,    'time': '16:00', 'upload_time': '13:00', 'rerun_days_offset': 1,    'rerun_time': '12:00','wp_show_id': ''},
    {'key': 'san_patrock_assaf',    'name': 'סן פטרוק',            'broadcaster': 'אסף פלג',      'day': 0,    'time': '19:00', 'upload_time': '20:00', 'rerun_days_offset': 5,    'rerun_time': '10:00','wp_show_id': ''},
    {'key': 'san_patrock_itamar',   'name': 'סן פטרוק',            'broadcaster': 'איתמר עדן',    'day': 0,    'time': '20:00', 'upload_time': '21:00', 'rerun_days_offset': 5,    'rerun_time': '11:00','wp_show_id': ''},
    {'key': 'san_patrock_roi',      'name': 'סן פטרוק',            'broadcaster': 'רועי כנפו',    'day': 3,    'time': '19:00', 'upload_time': '20:00', 'rerun_days_offset': 2,    'rerun_time': '14:00','wp_show_id': ''},
    {'key': 'san_patrock_roni',     'name': 'סן פטרוק',            'broadcaster': 'רוני אורן',    'day': 3,    'time': '20:00', 'upload_time': '21:00', 'rerun_days_offset': 2,    'rerun_time': '15:00','wp_show_id': ''},
    {'key': 'time_warp',            'name': 'Time Warp',           'broadcaster': 'רועי קופרמן',  'day': 1,    'time': '08:00', 'upload_time': '09:00', 'rerun_days_offset': 0,    'rerun_time': '18:00','wp_show_id': ''},
    {'key': 'breakdown',            'name': 'The Breakdown',       'broadcaster': 'עדן גולן',     'day': 1,    'time': '10:00', 'upload_time': '11:00', 'rerun_days_offset': 1,    'rerun_time': '10:00','wp_show_id': ''},
    {'key': 'singles',              'name': 'סינגלס',              'broadcaster': 'יובל ביטון',   'day': 1,    'time': '12:00', 'upload_time': '13:00', 'rerun_days_offset': 2,    'rerun_time': '11:00','wp_show_id': ''},
    {'key': 'haachot',              'name': 'האחות',               'broadcaster': 'אפרת קוטגרו',  'day': 2,    'time': '08:00', 'upload_time': '09:00', 'rerun_days_offset': 6,    'rerun_time': '15:00','wp_show_id': ''},
    {'key': 'ze_prog',              'name': 'זה פרוג',             'broadcaster': 'ערן הר-פז',    'day': 2,    'time': '11:00', 'upload_time': '12:00', 'rerun_days_offset': 4,    'rerun_time': '11:00','wp_show_id': ''},
    {'key': 'on_the_mend',          'name': 'On the Mend',         'broadcaster': 'נופר נירן',    'day': 2,    'time': '17:00', 'upload_time': '18:00', 'rerun_days_offset': 1,    'rerun_time': '10:00','wp_show_id': ''},
    {'key': 'shabi',                'name': 'Shabi on the Rocks',  'broadcaster': 'דוד שאבי',     'day': 2,    'time': '19:00', 'upload_time': '20:00', 'rerun_days_offset': 5,    'rerun_time': '18:00','wp_show_id': ''},
    {'key': 'forte',                'name': 'זה רוק פורטה',        'broadcaster': 'אחיעד לוק',    'day': 3,    'time': '08:00', 'upload_time': '09:00', 'rerun_days_offset': 0,    'rerun_time': '16:00','wp_show_id': ''},
    {'key': 'beat_on',              'name': 'Beat-On מקומי',       'broadcaster': 'יובל ביטון',   'day': 3,    'time': '15:00', 'upload_time': '16:00', 'rerun_days_offset': 4,    'rerun_time': '10:00','wp_show_id': ''},
    {'key': 'stage_dive',           'name': 'Stage Dive',          'broadcaster': 'עדן גולן',     'day': 3,    'time': '18:00', 'upload_time': '19:00', 'rerun_days_offset': 3,    'rerun_time': '12:00','wp_show_id': ''},
    {'key': 'erev_albumim',         'name': 'ערב של אלבומים',      'broadcaster': '',              'day': 4,    'time': '17:00', 'upload_time': '17:00', 'rerun_days_offset': None,  'rerun_time': None,  'wp_show_id': '', 'no_podbean': True},
    {'key': 'matzad_harok',         'name': 'מצעד הרוק של ישראל', 'broadcaster': '',              'day': 3,    'time': '13:00', 'upload_time': '13:00', 'rerun_days_offset': 1,    'rerun_time': '10:00','wp_show_id': '', 'no_podbean': True},
]

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

def _next_broadcast_dt(show_cfg, manual_date_str=None):
    """Return the next datetime for a show's first broadcast."""
    h, m = map(int, show_cfg['time'].split(':'))
    if show_cfg['day'] is None:
        # Manual date
        if not manual_date_str:
            return None
        d = datetime.strptime(manual_date_str, '%Y-%m-%d')
        return d.replace(hour=h, minute=m, second=0, microsecond=0)
    target_wd = show_cfg['day']
    now = datetime.now()
    days_ahead = (target_wd - now.weekday()) % 7
    if days_ahead == 0:
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= candidate:
            days_ahead = 7
    target_date = (now + timedelta(days=days_ahead)).date()
    return datetime(target_date.year, target_date.month, target_date.day, h, m)

def _calc_upload_dt(broadcast_dt, show_cfg):
    h, m = map(int, show_cfg['upload_time'].split(':'))
    return broadcast_dt.replace(hour=h, minute=m, second=0, microsecond=0)

def _calc_rerun_dt(broadcast_dt, show_cfg):
    if show_cfg['rerun_days_offset'] is None:
        return None
    h, m = map(int, show_cfg['rerun_time'].split(':'))
    d = (broadcast_dt + timedelta(days=show_cfg['rerun_days_offset'])).date()
    return datetime(d.year, d.month, d.day, h, m)

def _show_label(s):
    return f"{s['name']} — {s['broadcaster']}" if s['broadcaster'] else s['name']

def _show_slug(s):
    """Generate a URL slug: {name}-{broadcaster} with spaces→hyphens."""
    parts = [s['name']]
    if s['broadcaster']:
        parts.append(s['broadcaster'])
    return '-'.join(parts).replace(' ', '-')

def _slug_en(s):
    """Short English slug derived from the show key (underscores→hyphens)."""
    return s['key'].replace('_', '-')

def _resolve_broadcaster(show_cfg):
    """Return the fixed broadcaster for a show config, or empty string."""
    return show_cfg.get('broadcaster', '') if show_cfg else ''

def _make_rerun_entry(show):
    """Build a rerun schedule entry from an original show entry. Returns None if no rerun."""
    if not show.get('rerun_time'):
        return None
    rerun_id = str(int(time.time() * 1000) + 1)
    # Fall back to show_cfg broadcaster if the entry itself has an empty one
    _skey = show.get('show_key', '')
    _scfg = next((s for s in SHOW_SCHEDULE if s['key'] == _skey), None)
    broadcaster = show.get('broadcaster') or _resolve_broadcaster(_scfg)
    return {
        'id':             rerun_id,
        'name':           show['name'],
        'show_key':       _skey,
        'broadcaster':    broadcaster,
        'mode':           'queue_only',         # reruns skip Podbean/WP upload
        'episode_num':    show.get('episode_num', ''),
        'description':    show.get('description', ''),
        'scheduled_time': show['rerun_time'],
        'upload_time':    None,
        'rerun_time':     None,
        'file_path':      show.get('file_path', ''),
        'nas_path':       show.get('nas_path', ''),
        'nas_ready':      show.get('nas_ready', False),
        'original_name':  show.get('original_name', ''),
        'triggered':      False,
        'rerun_scheduled':False,
        'upload_done':    False,
        'is_rerun':       True,
        'added_at':       datetime.now().isoformat(),
    }

# ─── Schedule helpers ─────────────────────────────────────────────────────────

def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        return []
    try:
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_schedule(data):
    with open(SCHEDULE_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

_schedule_lock = threading.Lock()

def _move_to_nas(show_id, local_path, nas_path):
    """Background: copy file from local temp to NAS, then update schedule."""
    try:
        shutil.copy2(local_path, nas_path)
        os.remove(local_path)
        print(f"[NAS] Moved show {show_id} to NAS: {nas_path}")
        with _schedule_lock:
            schedule = load_schedule()
            for s in schedule:
                # Update the original AND any rerun that shares the same nas_path
                if s['id'] == show_id or s.get('nas_path') == nas_path:
                    s['file_path'] = nas_path
                    s['nas_ready'] = True
            save_schedule(schedule)
    except Exception as e:
        print(f"[NAS] Error moving {local_path} to NAS: {e}")
        # Keep local path as fallback — show can still play from local

# ─── Liquidsoap telnet ────────────────────────────────────────────────────────

def _lq_connect_send(commands, timeout=5):
    """Internal: connect to Liquidsoap telnet, send commands, return response strings."""
    s = socket.socket()
    s.settimeout(timeout)
    s.connect((LQ_HOST, LQ_PORT))
    # Drain banner
    time.sleep(0.15)
    try:
        s.recv(8192)
    except Exception:
        pass
    results = []
    for cmd in commands:
        s.sendall((cmd + "\n").encode())
        time.sleep(0.3)  # wait for response to arrive
        raw = b""
        s.settimeout(0.5)  # short timeout for reading
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
        except Exception:
            pass
        s.settimeout(timeout)
        results.append(raw.decode(errors='replace'))
    try:
        s.sendall(b"quit\n")
        s.close()
    except Exception:
        pass
    return results

def lq_send(commands):
    """Send commands to Liquidsoap (serialized via lock). Returns joined response."""
    with _lq_lock:
        try:
            return "\n".join(_lq_connect_send(commands))
        except Exception as e:
            return f"ERROR: {e}"

def lq_query(commands):
    """Send commands and return list of clean response lines per command."""
    with _lq_lock:
        try:
            raw_list = _lq_connect_send(commands)
            results = []
            for raw in raw_list:
                lines = [l.strip() for l in raw.splitlines()
                         if l.strip() and not l.strip().upper().startswith("END")]
                results.append(lines)
            return results
        except Exception:
            return [[] for _ in commands]

def lq_send_direct(commands):
    """Send commands directly without the shared lock — for time-sensitive calls like skip."""
    try:
        return "\n".join(_lq_connect_send(commands))
    except Exception as e:
        return f"ERROR: {e}"

def _fix_encoding(s):
    """Fix Hebrew strings double-encoded as CP1255 bytes → Latin-1 → UTF-8."""
    if not s:
        return s
    try:
        s.encode('ascii')
        return s   # pure ASCII, no fix needed
    except UnicodeEncodeError:
        pass
    try:
        return s.encode('latin-1').decode('cp1255')
    except Exception:
        return s

def _decode_tag(b):
    """Decode a metadata tag byte string: try UTF-8 first, fall back to CP1255."""
    if not b:
        return ""
    try:
        return _fix_encoding(b.decode('utf-8'))
    except UnicodeDecodeError:
        return _fix_encoding(b.decode('cp1255', errors='replace'))

def _read_now_playing_file():
    """Parse now_playing.txt → (title, artist, full_path).
    Reads as binary so Hebrew CP1255 ID3 tags don't crash UTF-8 decode.
    """
    try:
        if os.path.exists(NOW_PLAYING_FILE):
            with open(NOW_PLAYING_FILE, 'rb') as f:
                raw = f.read().rstrip(b'\r\n')
            parts = raw.split(b'\t')
            if len(parts) >= 3:
                title  = _decode_tag(parts[0])
                artist = _decode_tag(parts[1])
                path   = parts[2].decode('utf-8', errors='replace')
                if not title and path:
                    title = os.path.splitext(os.path.basename(path))[0]
                return title or "Rocky", artist, path
            elif parts and parts[0]:
                return _decode_tag(parts[0]), "", ""
    except Exception:
        pass
    return "Rocky", "", ""

# ── Background now-playing cache (no telnet — uses file + elapsed time) ───────
_np_cache = {"title": "Rocky", "artist": "", "filename": "",
             "duration": 0.0, "remaining": 0.0, "elapsed": 0.0}
_np_cache_lock    = threading.Lock()
_np_last_path     = ""   # last path seen (reset on skip to force duration re-read)
_np_last_dur      = 0.0
_np_track_start   = None   # datetime when current track was first detected

def _np_updater():
    global _np_last_path, _np_last_dur, _np_track_start
    while True:
        try:
            now = datetime.now()

            # now_playing.txt is written by Liquidsoap's on_track callback instantly
            # on every track start — authoritative for path detection.
            np_title, np_artist, np_path = _read_now_playing_file()

            # Queue cache (3-second poll) adds richer metadata when it has caught up.
            with _queue_cache_lock:
                on_air = _queue_cache.get('on_air')

            oa_uri = (on_air.get('uri') or '') if on_air else ''

            if oa_uri and oa_uri == np_path:
                # Queue cache is current — use its enriched ID3 title/artist
                title     = on_air.get('title') or on_air.get('label') or np_title
                artist    = on_air.get('artist') or np_artist
                full_path = oa_uri
            elif on_air and (oa_uri or on_air.get('title')) and not np_path:
                # No Liquidsoap file yet — fall back to queue cache (e.g. shows.push items)
                title     = on_air.get('title') or on_air.get('label', 'Rocky')
                artist    = on_air.get('artist', '')
                full_path = oa_uri
                if not title and full_path:
                    title = os.path.splitext(os.path.basename(full_path))[0]
            else:
                # Liquidsoap file is ahead of queue cache (short tracks like jingles).
                # Trust the file — queue cache will catch up on its next cycle.
                title, artist, full_path = np_title, np_artist, np_path

            if full_path != _np_last_path:
                # Track changed (or skip reset _np_last_path) — re-read duration
                duration = 0.0
                if full_path and os.path.exists(full_path):
                    try:
                        from mutagen import File as MFile
                        audio = MFile(full_path)
                        if audio is not None and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                            duration = float(audio.info.length)
                    except Exception:
                        pass
                _np_last_path   = full_path
                _np_last_dur    = duration
                _np_track_start = now
                # Log every path change to history (full_path != _np_last_path already guards this)
                if full_path:
                    log_title     = title
                    log_artist    = artist
                    log_auto_rerun = False
                    # For show files, use the scheduled show name (not the MP3's ID3 tags)
                    if full_path.startswith(LOCAL_TEMP) or NAS_TEMP in full_path:
                        try:
                            sched = load_schedule()
                            match = next((s for s in sched
                                          if s.get('file_path') == full_path
                                          or s.get('nas_path') == full_path), None)
                            if match:
                                log_title      = match.get('name', title)
                                log_artist     = match.get('broadcaster', artist)
                                log_auto_rerun = bool(match.get('auto_rerun'))
                        except Exception:
                            pass
                    _append_history(log_title, log_artist, os.path.basename(full_path),
                                    full_path, auto_rerun=log_auto_rerun)
            else:
                duration = _np_last_dur

            # Compute elapsed/remaining from wall-clock time — no telnet needed
            if _np_track_start and duration > 0:
                elapsed   = min(duration, (now - _np_track_start).total_seconds())
                remaining = max(0.0, duration - elapsed)
            else:
                elapsed = remaining = 0.0

            with _np_cache_lock:
                _np_cache.update({
                    "title":     title,
                    "artist":    artist,
                    "filename":  os.path.basename(full_path) if full_path else "",
                    "full_path": full_path or "",
                    "duration":  duration,
                    "remaining": remaining,
                    "elapsed":   elapsed,
                })
        except Exception:
            pass
        time.sleep(1)

def get_now_playing():
    with _np_cache_lock:
        return dict(_np_cache)

# ── Play history ──────────────────────────────────────────────────────────────
_history_lock = threading.Lock()

def _append_history(title, artist, filename, full_path="", auto_rerun=False):
    # Classify track type from its path
    if full_path.startswith(LOCAL_TEMP) or NAS_TEMP in full_path:
        track_type = "show"
    elif JINGLES_DIR in full_path or "/jingles" in full_path.lower():
        track_type = "jingle"
    else:
        track_type = "rocky"
    with _history_lock:
        try:
            history = []
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE) as f:
                    history = json.load(f)
        except Exception:
            history = []
        entry = {
            "title":     title,
            "artist":    artist,
            "filename":  filename,
            "full_path": full_path,
            "type":      track_type,
            "played_at": datetime.now().isoformat()
        }
        if auto_rerun:
            entry["auto_rerun"] = True
        history.append(entry)
        # Keep only last 7 days worth (cap at 5000 entries)
        history = history[-5000:]
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, ensure_ascii=False)
        except Exception:
            pass

def get_history_24h():
    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    with _history_lock:
        try:
            if not os.path.exists(HISTORY_FILE):
                return []
            with open(HISTORY_FILE) as f:
                history = json.load(f)
            return [e for e in history if e.get("played_at", "") >= cutoff]
        except Exception:
            return []

threading.Thread(target=_np_updater, daemon=True).start()

def get_stream_states():
    """Return (local_active, ext_active) in one lockless telnet connection."""
    def _parse_bool(raw):
        for line in raw.splitlines():
            line = line.strip().lower()
            if line in ("true", "false"):
                return line == "true"
        return False
    try:
        results = _lq_connect_send(["var.get local_active", "var.get ext_active"])
        local = _parse_bool(results[0]) if results else False
        ext   = _parse_bool(results[1]) if len(results) > 1 else False
        return local, ext
    except Exception:
        return False, False

def get_stream_active():
    """Check if local stream is active."""
    local, _ = get_stream_states()
    return local

def liquidsoap_running():
    """Check if Liquidsoap telnet is responsive."""
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((LQ_HOST, LQ_PORT))
        s.sendall(b"quit\n")
        s.close()
        return True
    except Exception:
        return False

# ─── Playlist rebuild ────────────────────────────────────────────────────────

def rebuild_playlists():
    """Rescan NAS music folders and rewrite the M3U playlist files.
    Respects the excluded_tracks list. Safe to call while Liquidsoap is playing —
    it re-reads the file on its next poll cycle (≤1 hour), no restart needed."""
    AUDIO_EXTS = {'.mp3', '.flac', '.ogg', '.wav', '.aac', '.m4a'}
    try:
        excluded = []
        if os.path.exists(EXCLUDED_FILE):
            with open(EXCLUDED_FILE) as f:
                excluded = json.load(f)
        excluded_set = set(excluded)
    except Exception:
        excluded_set = set()

    def scan_dir(root):
        tracks = []
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                    full = os.path.join(dirpath, fn)
                    if full not in excluded_set:
                        tracks.append(full)
        tracks.sort()
        return tracks

    results = {}
    for name, src_dir, dest_file in [
        ('english', ENGLISH_MUSIC_DIR, ENGLISH_PLAYLIST),
        ('hebrew',  HEBREW_MUSIC_DIR,  HEBREW_PLAYLIST),
        ('jingles', JINGLES_DIR,       JINGLES_PLAYLIST),
        ('zikaron', ZIKARON_DIR,       ZIKARON_PLAYLIST),
    ]:
        try:
            tracks = scan_dir(src_dir)
            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            with open(dest_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tracks) + '\n')
            results[name] = len(tracks)
            print(f"[rebuild_playlists] {name}: {len(tracks)} tracks → {dest_file}", flush=True)
        except Exception as e:
            results[name] = f'ERROR: {e}'
            print(f"[rebuild_playlists] {name} failed: {e}", flush=True)
    return results

# ─── Show triggering ──────────────────────────────────────────────────────────

def get_random_jingle(min_duration=10.0):
    """Pick a random jingle >= min_duration seconds from the NAS."""
    patterns = [
        f"{JINGLES_DIR}/**/*.mp3",
        f"{JINGLES_DIR}/**/*.wav",
        f"{JINGLES_DIR}/*.mp3",
        f"{JINGLES_DIR}/*.wav",
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p, recursive=True))

    valid = []
    for f in files:
        try:
            from mutagen import File as MFile
            audio = MFile(f)
            if audio is not None and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                if audio.info.length >= min_duration:
                    valid.append(f)
            else:
                valid.append(f)   # include if duration unreadable
        except Exception:
            valid.append(f)

    candidates = valid if valid else files
    return random.choice(candidates) if candidates else None

def get_mitzad_jingle():
    """Pick a random jingle from the mitzad folder (excludes MAKAOM announcement files)."""
    try:
        files = glob.glob(f"{MITZAD_DIR}/*.mp3") + glob.glob(f"{MITZAD_DIR}/*.wav")
        jingles = [f for f in files if not os.path.basename(f).upper().startswith("MAKAOM")]
        if jingles:
            return random.choice(jingles)
    except Exception:
        pass
    return get_random_jingle()

def get_makaom_file(slot_num):
    """Return path to MAKAOM N.mp3 announcement file, or None if not found."""
    path = os.path.join(MITZAD_DIR, f"MAKAOM {slot_num}.mp3")
    return path if os.path.exists(path) else None

def get_audio_duration(path):
    """Return audio file duration in seconds via ffprobe (accurate for VBR/non-standard rates).
    Falls back to mutagen, then 3600s if both fail."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=15)
        val = result.stdout.strip()
        if val:
            return float(val)
    except Exception:
        pass
    # mutagen fallback
    try:
        from mutagen import File as MFile
        audio = MFile(path)
        if audio is not None and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
            return float(audio.info.length)
    except Exception:
        pass
    return 3600.0

def trigger_show(show):
    """Push show content to Liquidsoap shows queue.

    Album show (ערב של אלבומים):
      Jingle → Album1 tracks → Jingle → Album2 tracks → … → AlbumN tracks → Jingle

    Regular show:
      Jingle → show file → Jingle

    Always flushes the queue first so shows start exactly on their scheduled time,
    even if a previous show is still playing (e.g. a recovery episode).
    """
    # Flush any remaining items from the previous show so this one starts immediately
    cmds = ['shows.flush_and_skip']
    all_tracks = []

    albums         = show.get('albums')
    playlist_files = show.get('playlist_files')
    if albums:
        # ── Album evening: jingle before each album, final jingle at end ──────
        for album_idx, album_tracks in enumerate(albums):
            existing = [f for f in album_tracks if f and os.path.exists(f)]
            if not existing:
                print(f"[Scheduler] WARNING: Album {album_idx + 1} has no files, skipping")
                continue
            j = get_random_jingle()
            if j and os.path.exists(j):
                cmds.append(f"shows.push {j}")
            for f in existing:
                cmds.append(f"shows.push {f}")
                all_tracks.append(f)
        # Final jingle after last album
        j_end = get_random_jingle()
        if j_end and os.path.exists(j_end):
            cmds.append(f"shows.push {j_end}")
    elif playlist_files is not None or show.get('palash_files') is not None:
        # ── Playlist show: jingle → מקום 20…1 (with announcements) → פל"ש 1…5 → jingle ──
        is_matzad   = show.get('show_key') == 'matzad_harok'
        slots       = show.get('playlist_slots') or []
        pl_files    = playlist_files or []
        pa_existing = [f for f in (show.get('palash_files') or []) if f and os.path.exists(f)]

        # Pair each path with its slot number; keep only existing files; sort descending (20 first)
        pl_pairs = sorted(
            [(slots[i] if i < len(slots) else (i + 1), f)
             for i, f in enumerate(pl_files) if f and os.path.exists(f)],
            key=lambda x: x[0], reverse=True
        )

        if not pl_pairs and not pa_existing:
            print(f"[Scheduler] ERROR: No playlist tracks found for '{show['name']}'")
            return False

        if is_matzad:
            # ── מצעד: iterate slots 20→1, insert פל"ש at defined positions ──
            pina_file    = os.path.join(MITZAD_DIR, "הפינה לשיפוטכם.mp3")
            slot_to_file = {slot: f for slot, f in pl_pairs}
            badges_list  = show.get('playlist_badges') or []
            for slot_num in range(20, 0, -1):
                f = slot_to_file.get(slot_num)
                if f:
                    makaom = get_makaom_file(slot_num)
                    if makaom:
                        cmds.append(f"shows.push {makaom}")
                    # Badge jingles: after MAKAOM, before the song
                    slot_idx     = slot_num - 1   # 0-based index into badges_list
                    slot_badges  = badges_list[slot_idx] if slot_idx < len(badges_list) else []
                    for badge in slot_badges:
                        badge_fname = MATZAD_BADGE_FILES.get(badge)
                        if badge_fname:
                            badge_path = os.path.join(MITZAD_DIR, badge_fname)
                            if os.path.exists(badge_path):
                                cmds.append(f"shows.push {badge_path}")
                    cmds.append(f"shows.push {f}")
                # Insert פל"ש after this slot if defined
                palash_idx = MATZAD_PALASH_AFTER.get(slot_num)
                if palash_idx is not None and palash_idx < len(pa_existing):
                    if os.path.exists(pina_file):
                        cmds.append(f"shows.push {pina_file}")
                    cmds.append(f"shows.push {pa_existing[palash_idx]}")
            all_tracks = list(slot_to_file.values()) + pa_existing
        else:
            # ── Regular playlist show ─────────────────────────────────────────
            j1 = get_random_jingle()
            if j1 and os.path.exists(j1):
                cmds.append(f"shows.push {j1}")
            for slot_num, f in pl_pairs:
                cmds.append(f"shows.push {f}")
            for f in pa_existing:
                cmds.append(f"shows.push {f}")
            j2 = get_random_jingle()
            if j2 and os.path.exists(j2):
                cmds.append(f"shows.push {j2}")
            all_tracks = [f for _, f in pl_pairs] + pa_existing
    else:
        # ── Regular single-file show: jingle → show → jingle ─────────────────
        j1 = get_random_jingle()
        j2 = get_random_jingle()
        show_file = show.get('file_path', '')
        if not show_file or not os.path.exists(show_file):
            print(f"[Scheduler] ERROR: Show file not found: {show_file}")
            return False
        if j1 and os.path.exists(j1):
            cmds.append(f"shows.push {j1}")
        cmds.append(f"shows.push {show_file}")
        if j2 and os.path.exists(j2):
            cmds.append(f"shows.push {j2}")
        all_tracks = [show_file]

    if not all_tracks:
        print(f"[Scheduler] ERROR: No playable tracks found for '{show['name']}'")
        return False

    print(f"[Scheduler] Pushing {len(cmds)} items to queue for '{show['name']}'")
    for cmd in cmds:
        print(f"  {cmd}")

    resp = lq_send(cmds)
    success = "ERROR" not in resp
    print(f"[Scheduler] Result: {resp.strip()[:200]}")

    if success:
        total_duration = sum(get_audio_duration(f) for f in all_tracks)
        show['delete_after'] = (datetime.now() + timedelta(seconds=total_duration + 600)).isoformat()

    return success

# ─── Background scheduler ─────────────────────────────────────────────────────

_podbean_token_cache = {'token': None, 'expires': 0.0}

def _get_podbean_access_token():
    """Return a cached Podbean OAuth2 client-credentials token."""
    now = time.time()
    if _podbean_token_cache['token'] and now < _podbean_token_cache['expires'] - 60:
        return _podbean_token_cache['token']
    if not PODBEAN_CLIENT_ID or not PODBEAN_CLIENT_SECRET:
        return None
    try:
        resp = _requests.post(
            'https://api.podbean.com/v1/oauth/token',
            data={'grant_type': 'client_credentials'},
            auth=(PODBEAN_CLIENT_ID, PODBEAN_CLIENT_SECRET),
            timeout=15
        )
        data = resp.json()
        token = data.get('access_token')
        expires_in = data.get('expires_in', 3600)
        if token:
            _podbean_token_cache['token']   = token
            _podbean_token_cache['expires'] = now + expires_in
        return token
    except Exception as e:
        print(f"[Podbean] Token error: {e}", flush=True)
        return None

def _get_podbean_media_url(podbean_permalink: str) -> str:
    """Given a Podbean episode permalink URL, return the direct CDN audio URL.
    Searches recent episodes (up to 100) for a permalink match.
    Returns None if not found or credentials unavailable."""
    if not podbean_permalink:
        return None
    token = _get_podbean_access_token()
    if not token:
        return None
    needle = podbean_permalink.rstrip('/')
    try:
        for offset in range(0, 100, 20):
            resp = _requests.get(
                'https://api.podbean.com/v1/episodes',
                params={'access_token': token, 'limit': 20, 'offset': offset},
                timeout=15
            )
            episodes = resp.json().get('episodes', [])
            if not episodes:
                break
            for ep in episodes:
                if ep.get('permalink_url', '').rstrip('/') == needle:
                    return ep.get('media_url')
    except Exception as e:
        print(f"[Podbean] media_url lookup error: {e}", flush=True)
    return None


def _create_wp_post_direct(show) -> tuple:
    """Create a WordPress episode post directly via REST API (no audio re-upload).
    Used when the uploader's WP call failed but Podbean succeeded, or as a fallback
    at trigger time when no wp_post_id exists.
    Returns (success: bool, wp_post_id: int|None)."""
    import base64
    if not WP_USERNAME or not WP_APP_PASS:
        print("[WP-Direct] Credentials not set — skipping direct WP creation", flush=True)
        return False, None
    try:
        show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')), None)
        if not show_cfg:
            print(f"[WP-Direct] Unknown show_key: {show.get('show_key')}", flush=True)
            return False, None
        show_name  = show_cfg['name']
        broadcaster = (show.get('broadcaster') or show_cfg.get('broadcaster', ''))
        broadcast_dt = datetime.fromisoformat(show['scheduled_time'])
        date_str   = broadcast_dt.strftime('%Y-%m-%d')   # YYYY-MM-DD
        d, m, y    = broadcast_dt.strftime('%d'), broadcast_dt.strftime('%m'), broadcast_dt.strftime('%Y')
        fmt_date   = f"{d}/{m}/{y[2:]}"                  # DD/MM/YY for title
        episode_num = show.get('episode_num', '')
        parts = [show_name]
        if episode_num:
            parts.append(episode_num)
        parts.append(f"- {broadcaster} {fmt_date}")
        title = ' '.join(p for p in parts if p)

        playlist = show.get('description', '')
        content  = '\n'.join([
            f'<strong>Show:</strong> {show_name}',
            f'<strong>Episode:</strong> {episode_num}',
            f'<strong>Broadcaster:</strong> {broadcaster}',
            f'<strong>Date:</strong> {fmt_date}',
            '',
            '<strong>Playlist:</strong>',
            playlist.replace('\n', '<br/>')
        ])

        # Publish time: same logic as _do_podbean_wp_upload
        if show_cfg.get('day') is None and show_cfg.get('upload_time'):
            _uh, _um = map(int, show_cfg['upload_time'].split(':'))
            pub_dt = broadcast_dt.replace(hour=_uh, minute=_um, second=0, microsecond=0)
        else:
            pub_dt = broadcast_dt
        now_ts  = int(datetime.now().timestamp())
        pub_ts  = int(pub_dt.timestamp())
        status  = 'future' if pub_ts > now_ts + 60 else 'publish'

        body = {'title': title, 'content': content, 'status': status}
        if status == 'future':
            # pub_dt is a naive local-time datetime; convert to UTC before sending as date_gmt
            body['date_gmt'] = datetime.utcfromtimestamp(pub_dt.timestamp()).strftime('%Y-%m-%dT%H:%M:%S')

        slug_prefix = _WP_SHOW_SLUGS.get(show_name)
        if slug_prefix:
            body['slug'] = f"{slug_prefix}-{d}{m}{y[2:]}"

        featured = _WP_FEATURED_IMAGES.get(show_name)
        if featured:
            body['featured_media'] = featured

        # Show taxonomy — prefer hardcoded map over the (often empty) wp_show_id field
        show_tax_id = _WP_SHOW_IDS.get(show_name) or (int(show_cfg['wp_show_id']) if show_cfg.get('wp_show_id') else None)
        if show_tax_id:
            body['shows'] = [show_tax_id]

        # podbean_link must be the direct CDN audio URL (not the episode page URL)
        # so the WP theme can embed the audio player.
        podbean_permalink = show.get('podbean_url', '')
        media_url = _get_podbean_media_url(podbean_permalink) if podbean_permalink else None
        body['acf'] = {
            'date': date_str.replace('-', ''),   # YYYYMMDD
        }
        if media_url:
            body['acf']['podbean_link'] = media_url
        elif podbean_permalink:
            # Fallback: store permalink — better than nothing, but audio player won't work
            body['acf']['podbean_link'] = podbean_permalink
            print(f"[WP-Direct] Warning: using permalink as podbean_link (media_url lookup failed)", flush=True)

        creds = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
        headers = {'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'}
        resp = _requests.post(
            f"{WP_URL}/wp-json/wp/v2/episodes",
            json=body, headers=headers, timeout=30
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            wp_id = data.get('id')
            link  = data.get('link', '')
            print(f"[WP-Direct] Created post {wp_id} → {link} (status={data.get('status')})", flush=True)
            return True, wp_id
        else:
            print(f"[WP-Direct] HTTP {resp.status_code}: {resp.text[:300]}", flush=True)
            return False, None
    except Exception as e:
        print(f"[WP-Direct] Error: {e}", flush=True)
        return False, None


def _do_podbean_wp_upload(show):
    """POST show file + metadata to the uploader server for Podbean & WordPress."""
    # Use NAS copy only when fully written (nas_ready=True); otherwise use local file.
    # This prevents sending a half-written NAS file when _move_to_nas races with upload.
    nas   = show.get('nas_path', '')
    local = show.get('file_path', '')
    if show.get('nas_ready') and nas and os.path.exists(nas):
        file_path = nas
    elif local and os.path.exists(local):
        file_path = local
    elif nas and os.path.exists(nas):
        file_path = nas   # last resort: nas exists but nas_ready not set
    else:
        print(f"[Upload] File not found: nas={nas} local={local}", flush=True)
        return False, None, None
    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')), None)
    if not show_cfg:
        print(f"[Upload] Unknown show_key: {show.get('show_key')}", flush=True)
        return False, None, None
    broadcast_dt = datetime.fromisoformat(show['scheduled_time'])
    # broadcaster is required by the uploader; fall back to show's fixed host if set,
    # otherwise leave blank (do NOT fall back to show name — that corrupts the WP field)
    _FIXED_HOSTS = {
        'zifim':        'אחיעד לוק',
        'black_parade': 'מתן בכור',
        'pascal':       'שיר אסולין',
        'hashulter':    'דוד שאבי',
        'on_air':       'רועי קופרמן',
        'oy_vavoy':     'יותם "דפיילר" אבני',
        'haachot':      'אפרת קוטגרו',
        'ze_prog':      'ערן הר-פז',
        'on_the_mend':  'נופר נירן',
        'shabi':        'דוד שאבי',
        'forte':        'אחיעד לוק',
        'beat_on':      'יובל ביטון',
        'stage_dive':   'עדן גולן',
        'time_warp':    'רועי קופרמן',
        'breakdown':    'עדן גולן',
        'singles':      'יובל ביטון',
        'rocktrip':     'אלעד אביגן',
    }
    broadcaster = (show.get('broadcaster')
                   or show_cfg.get('broadcaster', '')
                   or _FIXED_HOSTS.get(show_cfg['key'], ''))
    # For manual-date shows (day=None, e.g. על הרוקר): publish at upload_time so the
    # Podbean/WP post goes live 1 hour after the broadcast, not during it.
    if show_cfg.get('day') is None and show_cfg.get('upload_time'):
        _uh, _um = map(int, show_cfg['upload_time'].split(':'))
        _pub_dt = broadcast_dt.replace(hour=_uh, minute=_um, second=0, microsecond=0)
        publish_ts = str(int(_pub_dt.timestamp()))
    else:
        publish_ts = str(int(broadcast_dt.timestamp()))
    try:
        with open(file_path, 'rb') as f:
            files  = {'audioFile': (show.get('original_name', 'show.mp3'), f, 'audio/mpeg')}
            data   = {
                'showName':        show_cfg['name'],
                'broadcaster':     broadcaster,
                'date':            broadcast_dt.strftime('%Y-%m-%d'),
                'scheduleTime':    broadcast_dt.strftime('%H:%M'),
                'episodeNumber':   show.get('episode_num', ''),
                'playlist':        show.get('description', ''),
                'publishTimestamp': publish_ts,
                'wpShowId':        show_cfg.get('wp_show_id', ''),
                'wpBroadcasterId': '',
                'scheduleToSam':   '0',
                'samOnly':         '0',
            }
            resp = _requests.post(
                UPLOADER_URL, files=files, data=data, timeout=300, stream=True,
                cookies={'auth': _UPLOADER_AUTH}
            )
            if resp.status_code != 200:
                print(f"[Upload] HTTP {resp.status_code} from uploader: {resp.text[:300]}", flush=True)
                return False, None, None
            wp_post_id  = None
            podbean_url = None
            had_error   = False
            for line in resp.iter_lines():
                if line:
                    try:
                        msg = json.loads(line).get('message', '')
                        print(f"[Upload] {msg}", flush=True)
                        if msg.startswith('ERROR:'):
                            had_error = True
                        # Parse Podbean URL + WP post ID from SCHEDULED or SUCCESS line
                        # Format: "SCHEDULED:podbeanUrl|timestamp|wpPostId"
                        #      or "SUCCESS:Episode published! URL: podbeanUrl|wpPostId"
                        for prefix in ('SCHEDULED:', 'SUCCESS:'):
                            if msg.startswith(prefix):
                                parts = msg[len(prefix):].split('|')
                                # SCHEDULED: parts[0]=url, parts[1]=ts, parts[2]=wpId
                                # SUCCESS:   parts[0]="Episode published! URL: url", parts[1]=wpId
                                raw_url = parts[0]
                                if raw_url.startswith('Episode published! URL: '):
                                    raw_url = raw_url[len('Episode published! URL: '):]
                                if raw_url.startswith('http'):
                                    podbean_url = raw_url.strip()
                                if len(parts) >= 3 and parts[2].isdigit():
                                    wp_post_id = int(parts[2])
                                elif len(parts) == 2 and parts[1].isdigit():
                                    wp_post_id = int(parts[1])
                    except Exception:
                        pass
            if had_error:
                return False, None, None
        return True, wp_post_id, podbean_url
    except Exception as e:
        print(f"[Upload] Error: {e}", flush=True)
        return False, None, None

def _upload_and_mark_done(show_id):
    """Run upload in background thread; update schedule with result (success or retry)."""
    with _schedule_lock:
        schedule = load_schedule()
    show = next((s for s in schedule if s.get('id') == show_id), None)
    if not show:
        print(f"[Upload] Show {show_id} not found in schedule", flush=True)
        return
    success, wp_post_id, podbean_url = _do_podbean_wp_upload(show)
    with _schedule_lock:
        schedule = load_schedule()
        for s in schedule:
            if s.get('id') == show_id:
                s['upload_in_progress'] = False
                if success:
                    s['upload_done'] = True
                    s['upload_done_at'] = datetime.now().isoformat()
                    if podbean_url:
                        s['podbean_url'] = podbean_url
                    if wp_post_id:
                        s['wp_post_id'] = wp_post_id
                        s.pop('wp_post_missing', None)  # clear flag if WP succeeded
                    else:
                        # Podbean OK but WP creation failed — flag for retry
                        s['wp_post_missing'] = True
                        print(f"[Upload] ⚠ WP post missing for '{show.get('name')}' — flagged for retry", flush=True)
                    print(f"[Upload] Complete: '{show.get('name')}' wp_post_id={wp_post_id}", flush=True)
                else:
                    attempts = s.get('upload_attempts', 0) + 1
                    s['upload_attempts'] = attempts
                    print(f"[Upload] Failed (attempt {attempts}): '{show.get('name')}'", flush=True)
                    if attempts >= 3:
                        s['upload_done'] = True   # give up after 3 failures
                        s['upload_failed'] = True
                break
        save_schedule(schedule)

def _make_show_title(show, broadcast_dt=None):
    """Build the episode title string, matching _create_wp_post_direct format.
    Falls back gracefully if show_cfg is not found."""
    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')), None)
    show_name   = show_cfg['name'] if show_cfg else show.get('name', '')
    broadcaster = show.get('broadcaster') or (show_cfg.get('broadcaster', '') if show_cfg else '')
    if broadcast_dt is None:
        try:
            broadcast_dt = datetime.fromisoformat(show['scheduled_time'])
        except Exception:
            broadcast_dt = datetime.now()
    fmt_date    = broadcast_dt.strftime('%d/%m/%y')   # DD/MM/YY
    episode_num = show.get('episode_num', '')
    parts = [show_name]
    if episode_num:
        parts.append(episode_num)
    parts.append(f"- {broadcaster} {fmt_date}")
    return ' '.join(p for p in parts if p)


def _update_wp_title(wp_post_id, new_title):
    """PATCH a WordPress episode post's title via REST API. Non-fatal."""
    import base64
    if not WP_USERNAME or not WP_APP_PASS:
        return
    try:
        creds   = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
        headers = {'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'}
        resp = _requests.post(
            f"{WP_URL}/wp-json/wp/v2/episodes/{wp_post_id}",
            json={'title': new_title},
            headers=headers,
            timeout=20
        )
        if resp.status_code in (200, 201):
            print(f"[WP] Updated title for post {wp_post_id} → {new_title}", flush=True)
        else:
            print(f"[WP] Title update failed ({resp.status_code}): {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"[WP] Title update error: {e}", flush=True)


def _update_podbean_title(podbean_url, new_title):
    """Update a Podbean episode's title via API. Non-fatal."""
    if not podbean_url:
        return
    try:
        token = _get_podbean_access_token()
        if not token:
            return
        needle = podbean_url.rstrip('/')
        for offset in range(0, 100, 20):
            resp = _requests.get(
                'https://api.podbean.com/v1/episodes',
                params={'access_token': token, 'limit': 20, 'offset': offset},
                timeout=15
            )
            episodes = resp.json().get('episodes', [])
            if not episodes:
                break
            for ep in episodes:
                if ep.get('permalink_url', '').rstrip('/') == needle:
                    ep_id = ep.get('id')
                    patch = _requests.post(
                        f"https://api.podbean.com/v1/episodes/{ep_id}",
                        data={'access_token': token, 'title': new_title},
                        timeout=15
                    )
                    if patch.status_code in (200, 201):
                        print(f"[Podbean] Updated title for episode {ep_id} → {new_title}", flush=True)
                    else:
                        print(f"[Podbean] Title update failed ({patch.status_code}): {patch.text[:200]}", flush=True)
                    return
        print(f"[Podbean] Episode not found for URL: {podbean_url}", flush=True)
    except Exception as e:
        print(f"[Podbean] Title update error: {e}", flush=True)


def _publish_wp_post(show_id, wp_post_id=None, show_name=None, broadcast_date=None):
    """Explicitly PATCH a WP episode post to 'publish' at air time, bypassing wp-cron.
    If no wp_post_id exists, falls back to creating a new post directly via REST API."""
    try:
        if wp_post_id:
            # Post exists — ask uploader to publish it (handles the future→publish transition)
            payload = {'wp_post_id': wp_post_id}
            resp = _requests.post(
                f"{UPLOADER_BASE_URL}/api/wp-publish",
                json=payload, timeout=30,
                cookies={'auth': _UPLOADER_AUTH}
            )
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get('ok'):
                print(f"[WP] Published post {data.get('post_id') or wp_post_id} → {data.get('link','')}", flush=True)
            else:
                print(f"[WP] Publish returned {resp.status_code}: {data.get('error', resp.text[:200])}", flush=True)
        else:
            # No WP post exists — create one directly via REST API
            with _schedule_lock:
                schedule = load_schedule()
            show = next((s for s in schedule if s.get('id') == show_id), None)
            if not show:
                print(f"[WP] Cannot create post: show {show_id} not in schedule", flush=True)
                return
            print(f"[WP] No wp_post_id for '{show.get('name')}' — creating via direct API", flush=True)
            ok, new_wp_id = _create_wp_post_direct(show)
            if ok and new_wp_id:
                with _schedule_lock:
                    schedule = load_schedule()
                    for s in schedule:
                        if s.get('id') == show_id:
                            s['wp_post_id'] = new_wp_id
                            s.pop('wp_post_missing', None)
                            break
                    save_schedule(schedule)
    except Exception as e:
        print(f"[WP] Publish error for show {show_id}: {e}", flush=True)

# ─── Zikaron (Memorial/Holocaust Day) mode ────────────────────────────────────

def load_zikaron_schedule():
    """Load zikaron schedule. Returns {holocaust: {from,until}, memorial: {from,until}}.
    Migrates old single-window format {from, until} → memorial automatically."""
    empty = {'holocaust': {'from': None, 'until': None},
             'memorial':  {'from': None, 'until': None}}
    try:
        if os.path.exists(ZIKARON_FILE):
            with open(ZIKARON_FILE) as f:
                data = json.load(f)
            # Migrate old single-window format → memorial
            if 'from' in data or 'until' in data:
                migrated = {'holocaust': {'from': None, 'until': None},
                            'memorial':  {'from': data.get('from'), 'until': data.get('until')}}
                save_zikaron_schedule(migrated)
                return migrated
            # Ensure both keys exist
            data.setdefault('holocaust', {'from': None, 'until': None})
            data.setdefault('memorial',  {'from': None, 'until': None})
            return data
    except Exception:
        pass
    return empty

def save_zikaron_schedule(data):
    with open(ZIKARON_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False)

def get_zikaron_type():
    """Return 'holocaust', 'memorial', or None based on current time."""
    try:
        s = load_zikaron_schedule()
        now = datetime.now()
        for ztype in ('holocaust', 'memorial'):
            w = s.get(ztype, {})
            if w.get('from') and w.get('until'):
                if datetime.fromisoformat(w['from']) <= now <= datetime.fromisoformat(w['until']):
                    return ztype
    except Exception:
        pass
    return None

def is_zikaron_window():
    """Return True if current time is within any configured zikaron window."""
    return get_zikaron_type() is not None

_zikaron_lq_state = None   # last value sent to Liquidsoap

def _save_stream_states(local, ext):
    try:
        with open(STREAM_STATES_FILE, 'w') as f:
            json.dump({'local_active': local, 'ext_active': ext}, f)
    except Exception as e:
        print(f"[StreamState] Save failed: {e}", flush=True)

def _load_stream_states():
    try:
        if os.path.exists(STREAM_STATES_FILE):
            with open(STREAM_STATES_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {'local_active': True, 'ext_active': False}

def _restore_stream_states():
    """Called after Liquidsoap restarts — re-applies saved stream states."""
    s = _load_stream_states()
    cmds = []
    cmds.append('var.set local_active = ' + ('true' if s['local_active'] else 'false'))
    cmds.append('var.set ext_active = '   + ('true' if s['ext_active']   else 'false'))
    try:
        _lq_connect_send(cmds)
        print(f"[StreamState] Restored: local={s['local_active']} ext={s['ext_active']}", flush=True)
    except Exception as e:
        print(f"[StreamState] Restore failed: {e}", flush=True)

def _sync_zikaron_to_lq():
    """Send var.set zikaron_active to Liquidsoap only when state changes.
    Plays the quiet jingle at the transition (both entering and exiting Zikaron mode)."""
    global _zikaron_lq_state
    should_be = is_zikaron_window()
    if should_be == _zikaron_lq_state:
        return
    val = 'true' if should_be else 'false'
    try:
        # Push quiet jingle first (shows queue has top priority — it plays immediately
        # regardless of zikaron state, bridging the transition cleanly).
        cmds = [
            f'shows.push {QUIET_JINGLE}',
            f'var.set zikaron_active = {val}',
        ]
        _lq_connect_send(cmds)
        _zikaron_lq_state = should_be
        print(f"[Zikaron] Set zikaron_active={val} + quiet jingle (type={get_zikaron_type()})", flush=True)
        # Reload jingle source after zikaron transition — the show/queue interaction
        # can cause rotate() to lose the pre-fetched jingle track.
        threading.Timer(5.0, _reload_jingle_source, args=('after zikaron transition',)).start()
    except Exception as e:
        print(f"[Zikaron] Telnet error: {e}", flush=True)

# ─── Fixed broadcaster map (mirrors _do_podbean_wp_upload) ───────────────────
_SHOW_FIXED_HOSTS = {
    'zifim':        'אחיעד לוק',
    'black_parade': 'מתן בכור',
    'pascal':       'שיר אסולין',
    'hashulter':    'דוד שאבי',
    'on_air':       'רועי קופרמן',
    'oy_vavoy':     'יותם "דפיילר" אבני',
    'haachot':      'אפרת קוטגרו',
    'ze_prog':      'ערן הר-פז',
    'on_the_mend':  'נופר נירן',
    'shabi':        'דוד שאבי',
    'forte':        'אחיעד לוק',
    'beat_on':      'יובל ביטון',
    'stage_dive':   'עדן גולן',
    'time_warp':    'רועי קופרמן',
    'breakdown':    'עדן גולן',
    'singles':      'יובל ביטון',
    'rocktrip':     'אלעד אביגן',
}

def _resolve_broadcaster(show_cfg):
    """Return the effective broadcaster name for a show (same logic as uploader)."""
    return (show_cfg.get('broadcaster', '')
            or _SHOW_FIXED_HOSTS.get(show_cfg['key'], ''))


# ─── Auto-rerun: fetch latest Podbean episode when no upload exists ───────────

def _fetch_and_schedule_auto_rerun(show_cfg, broadcast_dt, placeholder_id):
    """Background thread: download latest Podbean episode for this specific show,
    populate the main broadcast entry and create a rerun entry at the regular rerun time.
    No Podbean/WP upload — the episode already exists on Podbean.
    """
    show_key    = show_cfg['key']
    bcast_iso   = broadcast_dt.isoformat()
    show_name   = show_cfg['name']
    broadcaster = _resolve_broadcaster(show_cfg)

    try:
        # Ask the uploader server for the latest Podbean episode for this specific show
        params = {'showName': show_name}
        if broadcaster:
            params['broadcaster'] = broadcaster
        resp = _requests.get(
            f"{UPLOADER_BASE_URL}/api/latest-podbean-episode",
            params=params, timeout=30,
            cookies={'auth': _UPLOADER_AUTH}
        )
        if resp.status_code == 404:
            raise Exception(f"No Podbean episodes found for '{show_name}'")
        if resp.status_code != 200:
            raise Exception(f"Uploader returned {resp.status_code}: {resp.text[:200]}")

        data      = resp.json()
        media_url = data.get('mediaUrl')
        ep_title  = data.get('title', show_name)
        if not media_url:
            raise Exception("No media_url in response")

        # Download the MP3 to NAS_TEMP
        safe_key = show_key.replace('/', '_')
        filename = f"autorerun_{safe_key}_{int(time.time())}.mp3"
        nas_path = os.path.join(NAS_TEMP, filename)
        print(f"[AutoRerun] Downloading '{ep_title}' → {nas_path}", flush=True)

        dl = _requests.get(media_url, timeout=600, stream=True)
        dl.raise_for_status()
        with open(nas_path, 'wb') as fout:
            for chunk in dl.iter_content(chunk_size=65536):
                if chunk:
                    fout.write(chunk)

        file_size_mb = os.path.getsize(nas_path) / 1024 / 1024
        print(f"[AutoRerun] Downloaded {file_size_mb:.1f} MB — '{ep_title}'", flush=True)

        # Calculate rerun time from SHOW_SCHEDULE offsets
        rerun_iso = None
        if show_cfg.get('rerun_days_offset') is not None and show_cfg.get('rerun_time'):
            rh, rm  = map(int, show_cfg['rerun_time'].split(':'))
            rerun_d = (broadcast_dt + timedelta(days=show_cfg['rerun_days_offset'])).date()
            rerun_iso = datetime(rerun_d.year, rerun_d.month, rerun_d.day, rh, rm).isoformat()

        with _schedule_lock:
            sched = load_schedule()

            # Update the main (broadcast-time) placeholder:
            # - is_rerun stays False — it plays at the primary broadcast time
            # - wp_published = True   → skip WP publish (old episode, already on Podbean)
            # - upload_done  = True   → skip Podbean/WP upload retry
            # - rerun_scheduled = True → suppress automatic rerun creation at trigger time
            #                            (we create the rerun entry explicitly below)
            for s in sched:
                if s['id'] == placeholder_id:
                    s['file_path']          = nas_path
                    s['nas_path']           = nas_path
                    s['nas_ready']          = True
                    s['original_name']      = filename
                    s['auto_rerun_status']  = 'ready'
                    s['auto_rerun_title']   = ep_title
                    s['wp_published']       = True
                    s['upload_done']        = True
                    s['rerun_scheduled']    = True
                    break

            # Create explicit rerun entry at the show's regular rerun time
            if rerun_iso:
                _rerun_name = f"{show_name} — {broadcaster}" if broadcaster else show_name
                rerun_entry = {
                    'id':                       str(int(time.time() * 1000) + 1),
                    'show_key':                 show_key,
                    'name':                     _rerun_name,
                    'broadcaster':              broadcaster,
                    'scheduled_time':           rerun_iso,
                    'auto_rerun_for_broadcast': bcast_iso,   # links back to main entry
                    'upload_time':              None,
                    'rerun_time':               None,
                    'mode':                     'queue_only',
                    'is_rerun':                 True,
                    'auto_rerun':               True,
                    'auto_rerun_status':        'ready',
                    'auto_rerun_title':         ep_title,
                    'triggered':                False,
                    'rerun_scheduled':          False,
                    'upload_done':              True,
                    'nas_ready':                True,
                    'file_path':                nas_path,
                    'nas_path':                 nas_path,
                    'original_name':            filename,
                    'added_at':                 datetime.now().isoformat(),
                }
                sched.append(rerun_entry)
                print(f"[AutoRerun] Rerun entry created for {rerun_iso}", flush=True)

            save_schedule(sched)

        print(f"[AutoRerun] Ready: '{show_name}' broadcast={bcast_iso} "
              f"rerun={rerun_iso or 'none'} — '{ep_title}'", flush=True)

    except Exception as e:
        print(f"[AutoRerun] FAILED for '{show_name}': {e}", flush=True)
        with _schedule_lock:
            sched = load_schedule()
            for s in sched:
                if s['id'] == placeholder_id:
                    s['auto_rerun_status'] = 'failed'
                    s['auto_rerun_error']  = str(e)[:300]
                    break
            save_schedule(sched)


def _check_auto_reruns(schedule, now):
    """
    Called each scheduler cycle.

    Two passes:
    1. CANCEL: if a real upload now exists for a slot that had auto-rerun entries,
       remove those auto-rerun entries (and their downloaded files) so they don't
       double-trigger alongside the real show.
    2. CREATE: for shows airing in ~60 min with no real upload, fetch the latest
       Podbean episode and schedule it at the broadcast time + the regular rerun time.

    Shows in AUTO_RERUN_EXCLUDED or with day=None are always skipped.
    """
    # ── Pass 1: cancel auto-reruns that have been superseded by a real upload ──
    cancel_ids = set()
    for s in schedule:
        if not s.get('auto_rerun') or s.get('triggered'):
            continue
        show_key  = s.get('show_key')
        # Both main and rerun auto entries store the original broadcast time
        bcast_ref = s.get('auto_rerun_for_broadcast') or s.get('scheduled_time')
        has_real_upload = any(
            r.get('show_key') == show_key
            and r.get('scheduled_time') == bcast_ref
            and not r.get('auto_rerun')
            and not r.get('is_rerun')
            for r in schedule
        )
        if has_real_upload:
            cancel_ids.add(s['id'])

    if cancel_ids:
        with _schedule_lock:
            sched = load_schedule()
            for s in sched:
                if s['id'] in cancel_ids:
                    # Delete the downloaded file if it exists and no other entry uses it
                    for fpath in (s.get('file_path', ''), s.get('nas_path', '')):
                        if not fpath or not os.path.exists(fpath):
                            continue
                        still_needed = any(
                            r['id'] not in cancel_ids
                            and (r.get('file_path') == fpath or r.get('nas_path') == fpath)
                            for r in sched if r['id'] != s['id']
                        )
                        if not still_needed:
                            try:
                                os.remove(fpath)
                                print(f"[AutoRerun] Cancelled — deleted {fpath}", flush=True)
                            except Exception as e:
                                print(f"[AutoRerun] Cancel file delete error: {e}", flush=True)
            sched = [s for s in sched if s['id'] not in cancel_ids]
            save_schedule(sched)
        print(f"[AutoRerun] Cancelled {len(cancel_ids)} auto-rerun entries (real upload arrived)",
              flush=True)
        # Refresh schedule for pass 2
        with _schedule_lock:
            schedule = load_schedule()

    # ── Pass 2: create auto-reruns for shows with no upload at T-60min ─────────
    # During Zikaron mode the station plays memorial music — skip auto-reruns entirely.
    if is_zikaron_window():
        return

    for show_cfg in SHOW_SCHEDULE:
        if show_cfg['key'] in AUTO_RERUN_EXCLUDED:
            continue
        if show_cfg.get('day') is None:
            continue

        next_bcast = _next_broadcast_dt(show_cfg)
        if next_bcast is None:
            continue

        mins_until = (next_bcast - now).total_seconds() / 60
        # Window: 46–61 minutes before broadcast (one 15-second scheduler band wide enough)
        if not (46 <= mins_until <= 61):
            continue

        bcast_iso = next_bcast.isoformat()

        # Is there a REAL (non-auto) upload for this slot?
        slot_has_real = any(
            s.get('show_key') == show_cfg['key']
            and s.get('scheduled_time') == bcast_iso
            and not s.get('auto_rerun')
            and not s.get('is_rerun')
            for s in schedule
        )
        if slot_has_real:
            continue  # Real upload exists — use normal routine

        # Is there already an auto-rerun placeholder or entry for this slot?
        auto_exists = any(
            s.get('auto_rerun')
            and s.get('show_key') == show_cfg['key']
            and (s.get('scheduled_time') == bcast_iso
                 or s.get('auto_rerun_for_broadcast') == bcast_iso)
            for s in schedule
        )
        if auto_exists:
            continue  # Already being handled

        # Insert placeholder at the broadcast time (is_rerun=False — it IS the main broadcast)
        placeholder_id = str(int(time.time() * 1000))
        _ph_broadcaster = _resolve_broadcaster(show_cfg)
        _ph_name = f"{show_cfg['name']} — {_ph_broadcaster}" if _ph_broadcaster else show_cfg['name']
        placeholder = {
            'id':                       placeholder_id,
            'show_key':                 show_cfg['key'],
            'name':                     _ph_name,
            'broadcaster':              _ph_broadcaster,
            'scheduled_time':           bcast_iso,
            'auto_rerun_for_broadcast': bcast_iso,
            'upload_time':              None,
            'rerun_time':               None,
            'mode':                     'queue_only',
            'is_rerun':                 False,   # plays at the primary broadcast slot
            'auto_rerun':               True,
            'auto_rerun_status':        'fetching',
            'triggered':                False,
            'rerun_scheduled':          True,    # rerun created explicitly by fetch thread
            'upload_done':              True,    # no Podbean upload needed
            'wp_published':             True,    # no WP post needed (old episode)
            'nas_ready':                False,
            'file_path':                '',
            'nas_path':                 '',
            'original_name':            '',
            'added_at':                 now.isoformat(),
        }
        with _schedule_lock:
            sched = load_schedule()
            sched.append(placeholder)
            save_schedule(sched)

        print(f"[AutoRerun] No upload for '{show_cfg['name']}' at {bcast_iso} "
              f"(T-{mins_until:.0f}min) — fetching latest from Podbean…", flush=True)

        threading.Thread(
            target=_fetch_and_schedule_auto_rerun,
            args=(show_cfg, next_bcast, placeholder_id),
            daemon=True
        ).start()


def _reload_jingle_source(reason=''):
    """Reload the Liquidsoap jingle playlist source (src_j).
    Called after show/zikaron transitions that can cause the rotate() to lose
    the pre-fetched jingle track, resulting in silent skips for that slot."""
    try:
        lq_send(['src_j.reload'])
        print(f"[Health] src_j reloaded{' — ' + reason if reason else ''}", flush=True)
    except Exception:
        pass


_last_wp_check = 0.0   # epoch time of last WP verification run

def _check_wp_posts(schedule):
    """Scan schedule for shows whose Podbean upload succeeded but WP post is missing.
    Runs at most every 30 minutes (throttled by _last_wp_check). Calls
    _create_wp_post_direct() for any show flagged wp_post_missing=True, then
    clears the flag and saves the schedule on success.

    Candidates: queue_to_broadcast, not a rerun, upload_done, wp_post_missing,
                not already abandoned (upload_failed + 3 attempts)."""
    global _last_wp_check
    now = time.time()
    if now - _last_wp_check < 1800:   # 30-minute throttle
        return
    candidates = [
        s for s in schedule
        if (s.get('wp_post_missing')
            and s.get('upload_done')
            and not s.get('is_rerun')
            and not s.get('upload_failed')
            and s.get('mode') == 'queue_to_broadcast')
    ]
    if not candidates:
        _last_wp_check = now
        return
    _last_wp_check = now   # set even before requests so we don't spam on errors
    print(f"[WP-Check] {len(candidates)} show(s) missing WP post — retrying…", flush=True)
    changed = False
    for show in candidates:
        ok, wp_id = _create_wp_post_direct(show)
        if ok and wp_id:
            with _schedule_lock:
                schedule_fresh = load_schedule()
                for s in schedule_fresh:
                    if s.get('id') == show['id']:
                        s['wp_post_id'] = wp_id
                        s.pop('wp_post_missing', None)
                        break
                save_schedule(schedule_fresh)
            print(f"[WP-Check] ✓ Created WP post {wp_id} for '{show.get('name')}'", flush=True)
            changed = True
        else:
            print(f"[WP-Check] ✗ Still failed for '{show.get('name')}' — will retry later", flush=True)
    return changed


def scheduler_loop():
    """Every 15s: trigger broadcasts, auto-schedule reruns, trigger Podbean/WP uploads."""
    _lq_was_running = False
    while True:
        try:
            with _schedule_lock:
                schedule = load_schedule()
            now = datetime.now()
            changed = False
            to_add = []

            for show in schedule:
                # ── Trigger first broadcast ────────────────────────────────────
                if not show.get('triggered'):
                    try:
                        show_time = datetime.fromisoformat(show['scheduled_time'])
                    except Exception as e:
                        print(f"[Scheduler] Bad time for '{show.get('name')}': {e}")
                        continue
                    diff = (show_time - now).total_seconds()
                    if show.get('show_key'):  # only log scheduled shows
                        print(f"[Scheduler] '{show['name']}' in {diff:.0f}s")
                    if -600 <= diff <= 45:
                        # Skip reruns during zikaron window — station plays memorial music only
                        if show.get('is_rerun', False) and is_zikaron_window():
                            print(f"[Scheduler] Skipping rerun '{show['name']}' — zikaron mode active", flush=True)
                            continue
                        # Skip auto-rerun entries whose file is still downloading
                        if show.get('auto_rerun') and show.get('auto_rerun_status') in ('fetching', 'failed'):
                            print(f"[Scheduler] Auto-rerun '{show['name']}' not ready "
                                  f"(status={show.get('auto_rerun_status')}) — skipping trigger")
                            continue
                        print(f"[Scheduler] >>> Triggering '{show['name']}'!")
                        if trigger_show(show):
                            show['triggered']    = True
                            show['triggered_at'] = now.isoformat()
                            changed = True
                            # Reload jingle source after show trigger — rotate() can lose
                            # the pre-fetched jingle track when shows queue interrupts.
                            threading.Timer(10.0, _reload_jingle_source, args=('after show trigger',)).start()
                            # Sync WP schedule board so it reflects the new "now playing" show
                            threading.Thread(target=_sync_wp_board, daemon=True).start()

                            # Publish WP post at air time — don't rely on wp-cron
                            if not show.get('is_rerun') and not show.get('wp_published'):
                                wp_id   = show.get('wp_post_id')
                                sname   = show.get('show_key', '')
                                # derive broadcast date for slug fallback
                                try:
                                    bdate = datetime.fromisoformat(show['scheduled_time']).strftime('%Y-%m-%d')
                                except Exception:
                                    bdate = None
                                show_cfg_wp = next((s for s in SHOW_SCHEDULE if s['key'] == sname), None)
                                wp_show_name = show_cfg_wp['name'] if show_cfg_wp else None
                                threading.Thread(
                                    target=_publish_wp_post,
                                    args=(show['id'],),
                                    kwargs={'wp_post_id': wp_id, 'show_name': wp_show_name, 'broadcast_date': bdate},
                                    daemon=True
                                ).start()
                                show['wp_published'] = True

                            # Auto-schedule rerun (only for first-broadcast shows with rerun info)
                            if show.get('rerun_time') and not show.get('rerun_scheduled'):
                                rerun_id   = str(int(time.time() * 1000) + 1)
                                rerun_show = {
                                    'id':             rerun_id,
                                    'name':           show['name'],
                                    'show_key':       show.get('show_key', ''),
                                    'broadcaster':    show.get('broadcaster', ''),
                                    'scheduled_time': show['rerun_time'],
                                    'file_path':      show.get('file_path', ''),
                                    'nas_path':       show.get('nas_path', ''),
                                    'nas_ready':      show.get('nas_ready', False),
                                    'original_name':  show.get('original_name', ''),
                                    'is_rerun':       True,
                                    'triggered':      False,
                                    'added_at':       now.isoformat(),
                                }
                                to_add.append(rerun_show)
                                show['rerun_scheduled'] = True
                                print(f"[Scheduler] Rerun scheduled for {show['rerun_time']}")

                # ── Retry Podbean/WP upload (if immediate upload failed) ────────
                # The primary upload fires from api_add_show at schedule time.
                # The scheduler retries if that failed (upload_in_progress cleared by thread).
                if (show.get('mode') == 'queue_to_broadcast'
                        and not show.get('is_rerun')
                        and not show.get('upload_done')
                        and not show.get('upload_in_progress')
                        and show.get('upload_attempts', 0) < 3
                        and show.get('added_at')):
                    try:
                        added_at = datetime.fromisoformat(show['added_at'])
                        if now >= added_at + timedelta(minutes=5):
                            print(f"[Scheduler] Podbean/WP retry upload for '{show['name']}'")
                            show['upload_in_progress'] = True
                            changed = True
                            threading.Thread(
                                target=_upload_and_mark_done, args=(show['id'],), daemon=True
                            ).start()
                    except Exception as e:
                        print(f"[Scheduler] Upload trigger error: {e}")

            # Append auto-scheduled reruns
            if to_add:
                schedule.extend(to_add)
                changed = True

            if changed:
                with _schedule_lock:
                    save_schedule(schedule)

            # ── Auto-rerun: schedule latest Podbean episode if no upload at T-60min ──
            # Runs outside the lock — _check_auto_reruns manages its own locking
            try:
                _check_auto_reruns(schedule, now)
            except Exception as e:
                print(f"[AutoRerun] Check error: {e}", flush=True)

            # ── WP post verification: retry creation for shows missing WP post ──
            # Throttled internally to once per 30 min; no-op if nothing is missing.
            try:
                _check_wp_posts(schedule)
            except Exception as e:
                print(f"[WP-Check] Error: {e}", flush=True)

            # ── Cleanup: delete show files after play + buffer ─────────────────
            for show in schedule:
                if (show.get('triggered') and show.get('delete_after')
                        and not show.get('file_deleted')):
                    delete_after = datetime.fromisoformat(show['delete_after'])
                    # Don't delete before upload is done (keep file until upload_time + 10 min)
                    if show.get('upload_time') and not show.get('upload_done'):
                        try:
                            upload_dt = datetime.fromisoformat(show['upload_time'])
                            delete_after = max(delete_after, upload_dt + timedelta(minutes=10))
                        except Exception:
                            pass
                    if now >= delete_after:
                        # ── Guard: don't delete if Podbean upload failed — retry first ──
                        # Applies only to primary queue_to_broadcast episodes (not reruns,
                        # auto-reruns, queue_only, or shows with no_podbean flag).
                        if (show.get('upload_failed')
                                and show.get('mode') == 'queue_to_broadcast'
                                and not show.get('is_rerun')
                                and not show.get('auto_rerun')
                                and not show.get('upload_in_progress')):
                            _up_scfg = next(
                                (s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')),
                                None)
                            _needs_podbean = not (_up_scfg and _up_scfg.get('no_podbean'))
                            if _needs_podbean:
                                # Check retry cooldown (avoid hammering uploader every 15 s)
                                _retry_after = show.get('podbean_retry_after')
                                _retry_due = True
                                if _retry_after:
                                    try:
                                        _retry_due = now >= datetime.fromisoformat(_retry_after)
                                    except Exception:
                                        pass
                                # Check that the file actually still exists
                                _fpath_chk = show.get('nas_path') or show.get('file_path', '')
                                _file_ok = bool(_fpath_chk and os.path.exists(_fpath_chk))
                                if _file_ok and _retry_due:
                                    print(
                                        f"[Cleanup] Upload failed for '{show.get('name')}' "
                                        f"— keeping file, retrying Podbean upload…", flush=True)
                                    show['upload_done']        = False
                                    show['upload_failed']      = False
                                    show['upload_attempts']    = 0
                                    show['upload_in_progress'] = True
                                    show['podbean_retry_after'] = (
                                        now + timedelta(minutes=30)).isoformat()
                                    changed = True
                                    threading.Thread(
                                        target=_upload_and_mark_done,
                                        args=(show['id'],), daemon=True).start()
                                elif _file_ok:
                                    print(
                                        f"[Cleanup] Keeping '{show.get('name')}' "
                                        f"— upload retry pending (due at {_retry_after})",
                                        flush=True)
                                else:
                                    print(
                                        f"[Cleanup] Upload failed for '{show.get('name')}' "
                                        f"and file is missing — cannot retry, marking deleted",
                                        flush=True)
                                    show['file_deleted'] = True
                                    changed = True
                                continue  # skip normal deletion logic for this show

                        # Collect all file paths (single-file and multi-file album shows)
                        paths_to_delete = set()
                        for fpath in [show.get('file_path', ''), show.get('nas_path', '')]:
                            if fpath:
                                paths_to_delete.add(fpath)
                        for fpath in (show.get('files') or []):
                            if fpath:
                                paths_to_delete.add(fpath)
                        for fpath in paths_to_delete:
                            if not os.path.exists(fpath):
                                continue
                            # Don't delete if a pending rerun still references this file.
                            # A rerun is still "live" if it hasn't been triggered yet,
                            # OR if it was triggered but its own delete_after hasn't passed yet
                            # (i.e. the file is still actively playing).
                            def _rerun_still_needs_file(s):
                                if s['id'] == show['id']:
                                    return False
                                if s.get('file_deleted'):
                                    return False
                                if not (s.get('file_path') == fpath or s.get('nas_path') == fpath):
                                    return False
                                if not s.get('triggered'):
                                    return True   # hasn't played yet — always keep
                                # triggered — keep until its delete_after passes.
                                # If delete_after is not set yet, the rerun just
                                # triggered this loop iteration (trigger_show hasn't
                                # returned yet to set it) — keep the file.
                                da = s.get('delete_after')
                                if not da:
                                    return True  # just triggered, delete_after pending
                                try:
                                    return now < datetime.fromisoformat(da)
                                except Exception:
                                    return True  # parse error → play it safe
                            rerun_pending = any(_rerun_still_needs_file(s) for s in schedule)
                            if rerun_pending:
                                print(f"[Cleanup] Keeping {fpath} — rerun pending")
                                continue
                            try:
                                os.remove(fpath)
                                print(f"[Cleanup] Deleted: {fpath}")
                            except Exception as e:
                                print(f"[Cleanup] Error: {e}")
                        show['file_deleted'] = True
                        changed = True

        except Exception as e:
            print(f"[Scheduler] Error: {e}")

        # Detect Liquidsoap (re)start and restore stream states
        lq_now = liquidsoap_running()
        if lq_now and not _lq_was_running:
            print("[Scheduler] Liquidsoap came online — restoring stream states", flush=True)
            time.sleep(2)   # give LQ a moment to finish initialising
            _restore_stream_states()
        _lq_was_running = lq_now

        _sync_zikaron_to_lq()
        time.sleep(15)

threading.Thread(target=scheduler_loop, daemon=True).start()

# Sync WP board on startup so service restarts don't leave a stale schedule
def _startup_sync():
    time.sleep(10)   # give Flask/LQ a moment to initialise
    print("[Startup] Syncing WP schedule board…", flush=True)
    try:
        _sync_wp_board()
    except Exception as e:
        print(f"[Startup] WP board sync failed: {e}", flush=True)
threading.Thread(target=_startup_sync, daemon=True).start()

# ── Nightly playlist rebuild (midnight) ───────────────────────────────────────
def _nightly_rebuild_loop():
    """Rebuilds M3U playlists from NAS once per day at midnight.
    Also syncs the WP schedule board every Saturday midnight (new week starts Sunday)."""
    while True:
        now = datetime.now()
        # Seconds until next midnight
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_midnight - now).total_seconds())
        print("[Nightly] Rebuilding playlists…", flush=True)
        try:
            results = rebuild_playlists()
            print(f"[Nightly] Playlist rebuild complete: {results}", flush=True)
        except Exception as e:
            print(f"[Nightly] Playlist rebuild failed: {e}", flush=True)
        # Saturday night → Sunday midnight: new broadcast week starts; refresh WP board
        # (weekday()==6 at midnight = Sunday 00:00 = end of Saturday)
        if datetime.now().weekday() == 6:  # Sunday = 6
            print("[Nightly] Saturday midnight — syncing WP schedule board for new week…", flush=True)
            try:
                _sync_wp_board(force=True)
                print("[Nightly] WP board sync complete.", flush=True)
            except Exception as e:
                print(f"[Nightly] WP board sync failed: {e}", flush=True)

threading.Thread(target=_nightly_rebuild_loop, daemon=True).start()

# ── Background queue cache ────────────────────────────────────────────────────
# Rocky rotation order matches rocky.liq: rotate([english1, english2, hebrew1, english3, hebrew2, jingle])
# Each source has an explicit id= set in rocky.liq so these names are stable.
ROCKY_ROTATION = ['src_e1', 'src_e2', 'src_h1', 'src_e3', 'src_h2', 'src_j']

_queue_cache = {'queue': [], 'next_tracks': [], 'on_air': None, '_cycle': 0, '_updated': ''}
_queue_cache_lock = threading.Lock()

def _rid_label(meta_raw, rid):
    """Return (label, is_jingle) for a RID's metadata."""
    title  = _get_metadata_field(meta_raw, 'title')
    artist = _get_metadata_field(meta_raw, 'artist')
    uri    = _get_metadata_field(meta_raw, 'filename') or _get_metadata_field(meta_raw, 'uri')
    is_jingle = 'jingle' in (uri or '').lower()
    if is_jingle:
        label = title or os.path.splitext(os.path.basename(uri))[0] if uri else f"Item {rid}"
        return label, True
    if title and artist:
        label = f"{title} — {artist}"
    elif title:
        label = title
    elif uri:
        label = os.path.splitext(os.path.basename(uri))[0]
    else:
        label = ''
    return label, False

def _lq_session(commands):
    """Open one telnet connection, send all commands, collect responses delimited by END."""
    s = socket.socket()
    s.settimeout(5)
    s.connect((LQ_HOST, LQ_PORT))
    buf = b""
    # Drain banner (Liquidsoap sends one on connect)
    s.settimeout(1)
    try:
        buf = s.recv(8192)
    except Exception:
        pass

    results = []
    for cmd in commands:
        s.sendall((cmd + "\n").encode())
        # Read until END\r\n appears
        response = b""
        s.settimeout(3)
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\nEND" in response or b"\rEND" in response:
                    break
            except socket.timeout:
                break
            except Exception:
                break
        results.append(response.decode(errors='replace'))

    try:
        s.sendall(b"quit\n")
        s.close()
    except Exception:
        pass
    return results

def _queue_updater():
    cycle = 0
    while True:
        cycle += 1
        try:
            # Single connection for phase 1
            raw = _lq_session(["shows.queue", "request.on_air", "request.all"])
            if len(raw) < 3:
                time.sleep(5)
                continue
            shows_raw, on_air_raw, all_raw = raw[0], raw[1], raw[2]

            queue_rids = [l.strip() for l in shows_raw.splitlines() if l.strip().isdigit()]
            on_air_ids = set(p for p in on_air_raw.split() if p.isdigit())
            shows_set  = set(queue_rids)
            rocky_rids = [p for p in all_raw.split()
                          if p.isdigit() and p not in shows_set]  # includes on_air

            # Fetch metadata for everything in one session
            all_rids = list(dict.fromkeys(queue_rids + rocky_rids))  # deduplicated
            meta_map = {}
            if all_rids:
                meta_results = _lq_session([f"request.metadata {r}" for r in all_rids])
                for i, rid in enumerate(all_rids):
                    meta_map[rid] = meta_results[i] if i < len(meta_results) else ""

            # Parse scheduled show queue items
            queue_items = []
            for rid in queue_rids:
                try:
                    meta_raw = meta_map.get(rid, "")
                    title = _get_metadata_field(meta_raw, 'title')
                    uri   = _get_metadata_field(meta_raw, 'filename') or _get_metadata_field(meta_raw, 'uri')
                    name  = title or (os.path.splitext(os.path.basename(uri))[0] if uri else f"Item {rid}")
                    queue_items.append(name)
                except Exception:
                    queue_items.append(f"Item {rid}")

            # Find next Rocky track using rotation order
            # 1. Find on-air source → 2. Step to next in rotation → 3. Find that RID
            next_tracks = []
            try:
                on_air_source = ''
                for rid in on_air_ids:
                    on_air_source = _get_metadata_field(meta_map.get(rid, ''), 'source')
                    if on_air_source:
                        break

                if on_air_source in ROCKY_ROTATION:
                    cur_idx  = ROCKY_ROTATION.index(on_air_source)
                    next_src = ROCKY_ROTATION[(cur_idx + 1) % len(ROCKY_ROTATION)]
                    # Find the buffered RID belonging to next_src
                    for rid in rocky_rids:
                        if rid in on_air_ids:
                            continue
                        src = _get_metadata_field(meta_map.get(rid, ''), 'source')
                        if src == next_src:
                            label, is_jingle = _rid_label(meta_map.get(rid, ''), rid)
                            if label:
                                next_tracks.append({'label': label, 'jingle': is_jingle})
                            break
                else:
                    # Rotation source IDs not yet updated (Liquidsoap not restarted yet)
                    # Fall back to first non-on-air candidate
                    for rid in rocky_rids:
                        if rid not in on_air_ids:
                            label, is_jingle = _rid_label(meta_map.get(rid, ''), rid)
                            if label:
                                next_tracks.append({'label': label, 'jingle': is_jingle})
                            break
            except Exception as e:
                print(f"[QUEUE] next-track lookup error: {e}", flush=True)

            # Build on-air entry (what's playing right now)
            # Also exposes title/artist/uri so _np_updater can stay in sync.
            # Priority: shows (0) > src_zikaron when active (1) > everything else (2).
            # Within same priority, prefer the most recently started RID.
            # Stale RIDs (on_air_timestamp > 4h ago) are skipped unless they're the only option.
            _zikaron_on = _zikaron_lq_state  # bool — is zikaron currently active in LQ
            _SRC_PRIORITY = {'shows': 0, 'src_zikaron': 1 if _zikaron_on else 99}
            _now_ts       = time.time()
            _STALE_SECS   = 4 * 3600   # 4 hours
            on_air_info   = None
            _best_pri     = 999
            _best_ts      = -1
            try:
                for rid in on_air_ids:
                    meta_raw  = meta_map.get(rid, '')
                    uri = (_get_metadata_field(meta_raw, 'filename')
                           or _get_metadata_field(meta_raw, 'uri'))
                    label, is_jingle = _rid_label(meta_raw, rid)
                    if not (label or uri):
                        continue
                    source = _get_metadata_field(meta_raw, 'source') or ''
                    pri = _SRC_PRIORITY.get(source, 2)
                    try:
                        ts = float(_get_metadata_field(meta_raw, 'on_air_timestamp') or '0')
                    except Exception:
                        ts = 0
                    # Skip RIDs that have been "on air" for more than 4 hours — they are
                    # stale Liquidsoap artifacts from a previous session/source
                    if ts > 0 and (_now_ts - ts) > _STALE_SECS:
                        continue
                    if pri < _best_pri or (pri == _best_pri and ts > _best_ts):
                        _best_pri  = pri
                        _best_ts   = ts
                        title_oa   = _get_metadata_field(meta_raw, 'title')
                        artist_oa  = _get_metadata_field(meta_raw, 'artist')
                        is_show    = bool(uri) and (LOCAL_TEMP in uri or NAS_TEMP in uri)
                        on_air_info = {
                            'label':  label or (os.path.splitext(os.path.basename(uri))[0] if uri else ''),
                            'title':  title_oa,
                            'artist': artist_oa,
                            'uri':    uri,
                            'jingle': is_jingle,
                            'show':   is_show,
                        }
            except Exception as e:
                print(f"[QUEUE] on-air info error: {e}", flush=True)

            with _queue_cache_lock:
                _queue_cache['queue']       = queue_items
                _queue_cache['next_tracks'] = next_tracks
                _queue_cache['on_air']      = on_air_info
                _queue_cache['_cycle']      = cycle
                _queue_cache['_updated']    = datetime.now().isoformat()
        except Exception as e:
            print(f"[QUEUE] cycle {cycle} ERROR: {e}", flush=True)
        time.sleep(3)

threading.Thread(target=_queue_updater, daemon=True).start()

# Files uploaded via play-now that need cleanup after playing
_play_now_cleanup = []   # list of {'file': path, 'delete_after': datetime}
_play_now_lock = threading.Lock()

def _play_now_cleanup_loop():
    while True:
        time.sleep(30)
        now = datetime.now()
        with _play_now_lock:
            remaining = []
            for entry in _play_now_cleanup:
                if now >= entry['delete_after']:
                    try:
                        if os.path.exists(entry['file']):
                            os.remove(entry['file'])
                            print(f"[Cleanup] Deleted play-now file: {entry['file']}")
                    except Exception as e:
                        print(f"[Cleanup] Error deleting {entry['file']}: {e}")
                else:
                    remaining.append(entry)
            _play_now_cleanup[:] = remaining

threading.Thread(target=_play_now_cleanup_loop, daemon=True).start()

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    schedule = load_schedule()
    now = datetime.now()
    upcoming = sorted(
        [s for s in schedule if not s.get('triggered') and
         datetime.fromisoformat(s['scheduled_time']) > now],
        key=lambda x: x['scheduled_time']
    )
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    past = sorted(
        [s for s in schedule if s.get('triggered') and
         s.get('triggered_at', '') >= cutoff_7d],
        key=lambda x: x.get('triggered_at', ''),
        reverse=True
    )

    # Al HaRoker self-scheduling data for the admin panel
    all_bookings = _load_al_haroker_bookings()
    today_str    = now.date().isoformat()
    # Upcoming = not yet past, sorted by date
    ah_upcoming  = sorted(
        [b for b in all_bookings if b['date'] >= today_str],
        key=lambda b: b['date']
    )
    # Calendar link: show next month if we're in the last 3 days of current month
    if now.month == 12:
        last_day = datetime(now.year + 1, 1, 1) - timedelta(days=1)
        cal_year, cal_month = (now.year + 1, 1) if (last_day.date() - now.date()).days <= 2 else (now.year, 12)
    else:
        last_day = datetime(now.year, now.month + 1, 1) - timedelta(days=1)
        if (last_day.date() - now.date()).days <= 2:
            cal_year, cal_month = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
        else:
            cal_year, cal_month = now.year, now.month
    # Clamp to schedule start
    if (cal_year, cal_month) < (AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month):
        cal_year, cal_month = AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month
    ah_calendar_url = f"{ZEROCK_PUBLIC_URL}/al-haroker-schedule/{cal_year}/{cal_month}"
    ah_upload_base  = ZEROCK_PUBLIC_URL

    from flask import make_response
    resp = make_response(render_template('index.html',
        upcoming=upcoming,
        past=past,
        lq_running=liquidsoap_running(),
        ah_upcoming=ah_upcoming,
        ah_calendar_url=ah_calendar_url,
        ah_upload_base=ah_upload_base,
        heb_months=_HEB_MONTHS,
        heb_days=_HEB_DAYS,
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/<path:slug>')
def show_upload_page(slug):
    show_cfg = next((s for s in SHOW_SCHEDULE if _show_slug(s) == slug or _slug_en(s) == slug), None)
    if not show_cfg:
        return "Show not found", 404
    broadcast_dt = _next_broadcast_dt(show_cfg)
    upload_dt    = _calc_upload_dt(broadcast_dt, show_cfg) if broadcast_dt else None
    rerun_dt     = _calc_rerun_dt(broadcast_dt, show_cfg)  if broadcast_dt else None
    return render_template('show_form.html',
        show      = show_cfg,
        label     = _show_label(show_cfg),
        broadcast = broadcast_dt.isoformat() if broadcast_dt else None,
        upload    = upload_dt.isoformat()    if upload_dt    else None,
        rerun     = rerun_dt.isoformat()     if rerun_dt     else None,
    )

@app.route('/api/nowplaying')
def api_nowplaying():
    np = get_now_playing()
    with _queue_cache_lock:
        on_air = _queue_cache.get('on_air')
    if on_air:
        np['on_air_label']  = on_air.get('label', '') or on_air.get('title', '')
        np['on_air_artist'] = on_air.get('artist', '')
        np['on_air_show']   = bool(on_air.get('show'))
        np['on_air_jingle'] = bool(on_air.get('jingle'))
    return jsonify(np)

@app.route('/api/history')
def api_history():
    return jsonify(list(reversed(get_history_24h())))

@app.route('/api/exclude-track', methods=['POST'])
def api_exclude_track():
    """Remove a Rocky track from playlists so it never plays again.
    Accepts either a full path or just a filename (basename).
    If only a filename is given, the playlists are searched for a matching line.
    """
    data = request.get_json(silent=True) or {}
    track_path = data.get('path', '').strip()
    if not track_path:
        return jsonify({'error': 'path required'}), 400

    # If we only have a basename (no directory separator), search the playlists
    # to resolve it to a full path so we can remove the right line.
    if '/' not in track_path:
        basename = track_path
        for pl_path in [ENGLISH_PLAYLIST, HEBREW_PLAYLIST]:
            if not os.path.exists(pl_path):
                continue
            try:
                with open(pl_path) as f:
                    for line in f:
                        candidate = line.rstrip('\r\n')
                        if os.path.basename(candidate) == basename:
                            track_path = candidate
                            break
            except Exception:
                pass
            if '/' in track_path:
                break  # found

    # Load existing exclusion list
    try:
        if os.path.exists(EXCLUDED_FILE):
            with open(EXCLUDED_FILE) as f:
                excluded = json.load(f)
        else:
            excluded = []
    except Exception:
        excluded = []

    if track_path not in excluded:
        excluded.append(track_path)
        try:
            with open(EXCLUDED_FILE, 'w') as f:
                json.dump(excluded, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[exclude-track] Failed to save excluded list: {e}")

    # Remove from playlist file(s) — match by full path OR by basename fallback
    removed_from = []
    basename_fallback = os.path.basename(track_path)
    for pl_path in [ENGLISH_PLAYLIST, HEBREW_PLAYLIST]:
        if not os.path.exists(pl_path):
            continue
        try:
            with open(pl_path) as f:
                lines = f.readlines()
            new_lines = [l for l in lines
                         if l.rstrip('\r\n') != track_path
                         and os.path.basename(l.rstrip('\r\n')) != basename_fallback]
            if len(new_lines) < len(lines):
                with open(pl_path, 'w') as f:
                    f.writelines(new_lines)
                removed_from.append(os.path.basename(pl_path))
                print(f"[exclude-track] Removed '{track_path}' from {pl_path}")
        except Exception as e:
            print(f"[exclude-track] Error modifying {pl_path}: {e}")

    # Reload Liquidsoap playlists so it picks up the change
    if removed_from:
        reload_cmds = ['src_e1.reload', 'src_e2.reload', 'src_h1.reload', 'src_e3.reload', 'src_h2.reload']
        lq_send(reload_cmds)

    return jsonify({'ok': True, 'removed_from': removed_from})

def get_external_stream_active():
    """Check if external stream is active."""
    _, ext = get_stream_states()
    return ext

@app.route('/api/status')
def api_status():
    lq = liquidsoap_running()
    local_active, ext_active = get_stream_states() if lq else (False, False)
    zikaron_sched = load_zikaron_schedule()
    zikaron_on    = is_zikaron_window()
    zikaron_type  = get_zikaron_type()
    np = get_now_playing()
    return jsonify({
        "liquidsoap":             lq,
        "stream_active":          local_active,
        "external_stream_active": ext_active,
        "now_playing":            np,
        "zikaron_active":         zikaron_on,
        "zikaron_type":           zikaron_type,
        "zikaron_schedule":       zikaron_sched,
    })

@app.route('/api/zikaron', methods=['GET'])
def api_zikaron_get():
    sched = load_zikaron_schedule()
    return jsonify({
        'schedule':    sched,
        'active':      is_zikaron_window(),
        'active_type': get_zikaron_type(),
    })

@app.route('/api/zikaron', methods=['POST'])
def api_zikaron_post():
    data  = request.get_json() or {}
    ztype = data.get('type', 'memorial')
    if ztype not in ('holocaust', 'memorial'):
        return jsonify({'error': 'type must be holocaust or memorial'}), 400

    sched = load_zikaron_schedule()

    if data.get('clear'):
        sched[ztype] = {'from': None, 'until': None}
        save_zikaron_schedule(sched)
        _sync_zikaron_to_lq()
        _sync_wp_board()
        return jsonify({'ok': True})

    from_iso  = data.get('from')
    until_iso = data.get('until')
    if not from_iso or not until_iso:
        return jsonify({'error': 'from and until are required'}), 400
    try:
        dt_from  = datetime.fromisoformat(from_iso)
        dt_until = datetime.fromisoformat(until_iso)
    except Exception:
        return jsonify({'error': 'Invalid datetime format'}), 400
    if dt_until <= dt_from:
        return jsonify({'error': 'until must be after from'}), 400

    sched[ztype] = {'from': dt_from.isoformat(), 'until': dt_until.isoformat()}
    save_zikaron_schedule(sched)
    _sync_zikaron_to_lq()
    _sync_wp_board()
    return jsonify({'ok': True, 'active': is_zikaron_window(), 'active_type': get_zikaron_type()})

def _get_metadata_field(meta_str, field):
    """Extract a metadata field value from Liquidsoap request.metadata output."""
    for line in meta_str.splitlines():
        line = line.strip()
        if line.lower().startswith(field + '='):
            return _fix_encoding(line.split('=', 1)[1].strip().strip('"'))
    return ''

@app.route('/api/queue-status')
def api_queue_status():
    """Return shows queue items + next buffered Rocky track (from background cache)."""
    with _queue_cache_lock:
        return jsonify(dict(_queue_cache))

def _update_stream_state(key, value):
    """Update one key in stream_states.json without a Liquidsoap read-back."""
    s = _load_stream_states()
    s[key] = value
    _save_stream_states(s['local_active'], s['ext_active'])

@app.route('/api/stream/stop', methods=['POST'])
def api_stream_stop():
    resp = lq_send(["var.set local_active = false"])
    _update_stream_state('local_active', False)
    return jsonify({"success": True, "response": resp.strip()[:200]})

@app.route('/api/stream/start', methods=['POST'])
def api_stream_start():
    resp = lq_send(["var.set local_active = true"])
    _update_stream_state('local_active', True)
    return jsonify({"success": True, "response": resp.strip()[:200]})

@app.route('/api/stream/external/start', methods=['POST'])
def api_stream_external_start():
    resp = lq_send(["var.set ext_active = true"])
    _update_stream_state('ext_active', True)
    return jsonify({"success": True, "response": resp.strip()[:200]})

@app.route('/api/stream/external/stop', methods=['POST'])
def api_stream_external_stop():
    resp = lq_send(["var.set ext_active = false"])
    _update_stream_state('ext_active', False)
    return jsonify({"success": True, "response": resp.strip()[:200]})

@app.route('/live')
def live_stream_page():
    """Simple toggle page for external stream (live broadcast from remote location)."""
    _, ext = get_stream_states()
    return render_template('live_stream.html', ext_active=ext)

@app.route('/api/stream/external/status', methods=['GET'])
def api_stream_external_status():
    _, ext = get_stream_states()
    return jsonify({"ext_active": ext})

# ─── WordPress schedule board sync ────────────────────────────────────────────

WP_REST_BASE  = "https://zerockradio.com/wp-json"
WP_USER       = "eranharpaz@gmail.com"
WP_APP_PASS   = "WPp6 TLRs oghX cTCo lpzV sR0C"
WP_SCHEDULE_PAGE_ID = 254

# WP show page slugs (used to build links in the schedule board)
_WP_SLUGS = {
    'al_harocker':          'al-harocker',
    'rocktrip':             'rocktrip',
    'zifim':                'zifim',
    'black_parade':         'black-parade',
    'pascal':               'lo-bapaskol',
    'patrock_laila_eyal':   'patrock-laila',
    'patrock_laila_eliran': 'patrock-laila',
    'patrock_laila_meir':   'patrock-laila',
    'hashulter':            'theshulter',
    'on_air':               'onair',
    'oy_vavoy':             'oyvavoy',
    'san_patrock_assaf':    'st-patrock',
    'san_patrock_itamar':   'st-patrock',
    'san_patrock_roi':      'st-patrock',
    'san_patrock_roni':     'st-patrock',
    'time_warp':            'timewarp',
    'breakdown':            'breakdown',
    'singles':              'singles',
    'haachot':              'nurse',
    'ze_prog':              'ze-prog',
    'on_the_mend':          'onthemend',
    'shabi':                'sotr',
    'forte':                'forte',
    'beat_on':              'beat-on',
    'stage_dive':           'stage-dive',
    'erev_albumim':         'erev-albumim',
    'matzad_harok':         'mitzad-harok',
}

# Show durations in hours (used to calculate slot height + end time)
_SHOW_DURATIONS_H = {
    'al_harocker': 1, 'rocktrip': 1, 'zifim': 2, 'black_parade': 1,
    'pascal': 2, 'patrock_laila_eyal': 1, 'patrock_laila_eliran': 1,
    'patrock_laila_meir': 1, 'hashulter': 1, 'on_air': 1, 'oy_vavoy': 2,
    'san_patrock_assaf': 1, 'san_patrock_itamar': 1, 'san_patrock_roi': 1,
    'san_patrock_roni': 1, 'time_warp': 1, 'breakdown': 1, 'singles': 1,
    'haachot': 1, 'ze_prog': 1, 'on_the_mend': 1, 'shabi': 1, 'forte': 1,
    'beat_on': 1, 'stage_dive': 1, 'erev_albumim': 7, 'matzad_harok': 2,
}

# Broadcaster display prefix rules
_WP_BROADCASTER_PREFIX = {
    'al_harocker':          'בעריכת ',
    'patrock_laila_eyal':   'בעריכת ',
    'patrock_laila_eliran': 'בעריכת ',
    'patrock_laila_meir':   'בעריכת ',
    'san_patrock_assaf':    'בעריכת ',
    'san_patrock_itamar':   'בעריכת ',
    'san_patrock_roi':      'בעריכת ',
    'san_patrock_roni':     'בעריכת ',
    'time_warp':            'רוק קלאסי עם ',
    'singles':              'רוק ישראלי חדש עם ',
}

def _wp_broadcaster_str(show_cfg, is_rerun=False):
    """Return the broadcaster display string for the WP board."""
    key  = show_cfg['key']
    name = _resolve_broadcaster(show_cfg)
    if not name:
        name = 'רוקי'
    prefix = _WP_BROADCASTER_PREFIX.get(key, '')
    result = prefix + name
    if is_rerun:
        result += ' / ש.ח.'
    return result

# ─── Board cancellation helpers ───────────────────────────────────────────────
def _week_start_sunday():
    """ISO date string of the most-recent Sunday (start of broadcast week)."""
    today = datetime.now().date()
    days_since_sunday = (today.weekday() + 1) % 7   # Mon=1…Sat=6…Sun=0
    return (today - timedelta(days=days_since_sunday)).isoformat()

def _load_board_cancellations():
    """Return set of show_keys cancelled from the board for this week."""
    try:
        with open(BOARD_CANCELLATIONS_FILE) as f:
            data = json.load(f)
        if data.get('week_start') != _week_start_sunday():
            return set()   # stale — different week
        return set(data.get('cancelled', []))
    except Exception:
        return set()

def _cancel_show_on_board(show_key):
    """Mark show_key as cancelled on the board for the current week."""
    cancelled = _load_board_cancellations()
    cancelled.add(show_key)
    try:
        with open(BOARD_CANCELLATIONS_FILE, 'w') as f:
            json.dump({'week_start': _week_start_sunday(), 'cancelled': list(cancelled)}, f)
    except Exception:
        pass

def _clear_board_cancellations():
    """Clear all board cancellations (called on Sunday midnight)."""
    try:
        with open(BOARD_CANCELLATIONS_FILE, 'w') as f:
            json.dump({'week_start': _week_start_sunday(), 'cancelled': []}, f)
    except Exception:
        pass

def _build_wp_schedule_slots():
    """Build a dict of day_index → list of show slot dicts for the WP schedule board.

    day_index: 0=Sun, 1=Mon, ..., 6=Sat
    SHOW_SCHEDULE day: 0=Mon..6=Sun  →  WP day: {0:1, 1:2, 2:3, 3:4, 4:5, 5:6, 6:0}

    Rules:
      - All shows with a fixed day appear every week at their regular time.
      - QUEUE_ONLY_BOARD_SHOWS (על הרוקר, ערב של אלבומים) appear ONLY when an
        upcoming non-triggered episode is found in the queue within 14 days.
      - Shows manually removed (deleted) from Rocky this week are hidden
        (tracked in board_cancellations.json, cleared each Sunday midnight).
      - Queue overrides: if a primary episode is rescheduled to a different
        day/time, the board reflects the new time.
    """
    DAY_MAP = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
    slots = {d: [] for d in range(7)}

    board_cancelled = _load_board_cancellations()

    # ── Scan queue for overrides + queue-only shows ────────────────────────────
    queue_overrides    = {}   # show_key → (wp_day, start_h)   for regular rescheduled shows
    queue_only_entries = {}   # show_key → {wp_day, start_h, broadcaster}  for QUEUE_ONLY_BOARD_SHOWS
    try:
        queue = load_schedule()
        now   = datetime.now()
        for entry in sorted(queue, key=lambda e: e.get('scheduled_time', '')):
            if entry.get('triggered') or entry.get('is_rerun', False):
                continue
            key = entry.get('show_key')
            if not key:
                continue
            show_cfg_q = next((s for s in SHOW_SCHEDULE if s['key'] == key), None)
            try:
                t = datetime.fromisoformat(entry['scheduled_time'])
                if t < now - timedelta(hours=2) or t > now + timedelta(days=14):
                    continue
                ep_wp_day  = DAY_MAP[t.weekday()]
                ep_start_h = t.hour + t.minute / 60.0
                if key in QUEUE_ONLY_BOARD_SHOWS or (show_cfg_q and show_cfg_q['day'] is None):
                    if key not in queue_only_entries:
                        _bc_raw = (entry.get('broadcaster', '')
                                   or (show_cfg_q.get('broadcaster', '') if show_cfg_q else ''))
                        _bc_prefix = _WP_BROADCASTER_PREFIX.get(key, '')
                        queue_only_entries[key] = {
                            'wp_day':      ep_wp_day,
                            'start_h':     ep_start_h,
                            'broadcaster': (_bc_prefix + _bc_raw) if _bc_raw else '',
                        }
                else:
                    if key not in queue_overrides:
                        queue_overrides[key] = (ep_wp_day, ep_start_h)
            except Exception:
                pass
    except Exception:
        pass

    # ── Regular fixed-schedule shows ───────────────────────────────────────────
    for show in SHOW_SCHEDULE:
        if show['day'] is None:
            continue
        key = show['key']
        if key in QUEUE_ONLY_BOARD_SHOWS:
            continue   # handled via queue_only_entries below
        if key in board_cancelled:
            continue   # manually removed this week

        dur  = _SHOW_DURATIONS_H.get(key, 1)
        slug = _WP_SLUGS.get(key, '')

        override = queue_overrides.get(key)
        if override:
            wp_day, start_h = override
            is_queue_override = True
        else:
            h, m    = map(int, show['time'].split(':'))
            start_h = h + m / 60.0
            wp_day  = DAY_MAP[show['day']]
            is_queue_override = False

        end_h = start_h + dur
        slots[wp_day].append({
            'start_h':        start_h,
            'end_h':          end_h,
            'key':            key,
            'name':           show['name'],
            'slug':           slug,
            'broadcaster':    _wp_broadcaster_str(show, is_rerun=False),
            'rerun':          False,
            'queue_override': is_queue_override,
        })

        if show['rerun_days_offset'] is not None and show['rerun_time']:
            r_day_raw = (show['day'] + show['rerun_days_offset']) % 7
            wp_rday = DAY_MAP[r_day_raw]
            rh, rm  = map(int, show['rerun_time'].split(':'))
            r_start = rh + rm / 60.0
            r_end   = r_start + dur
            slots[wp_rday].append({
                'start_h':        r_start,
                'end_h':          r_end,
                'key':            key,
                'name':           show['name'],
                'slug':           slug,
                'broadcaster':    _wp_broadcaster_str(show, is_rerun=True),
                'rerun':          True,
                'queue_override': False,
            })

    # ── Queue-only shows (על הרוקר, ערב של אלבומים) + day=None shows ──────────
    for show in SHOW_SCHEDULE:
        key = show['key']
        if show['day'] is not None and key not in QUEUE_ONLY_BOARD_SHOWS:
            continue
        info = queue_only_entries.get(key)
        if not info:
            continue
        dur  = _SHOW_DURATIONS_H.get(key, 1)
        slug = _WP_SLUGS.get(key, '')
        slots[info['wp_day']].append({
            'start_h':        info['start_h'],
            'end_h':          info['start_h'] + dur,
            'key':            key,
            'name':           show['name'],
            'slug':           slug,
            'broadcaster':    info['broadcaster'],
            'rerun':          False,
            'queue_override': True,
        })

    return slots

def _build_wp_schedule_html():
    """Generate a CSS-Grid-based schedule where every column shares the same time axis.

    Layout:
      - 7 columns (Sun–Sat), each = 1fr
      - Row 1 = day headers (45 px)
      - Rows 2–N = time slots, one row per MINS_PER_ROW minutes
      - Grid starts at GRID_START_H (07:00) — overnight Rocky is implicit
      - Each show is positioned with grid-column / grid-row so all days align

    HTML structure (flat, no .schedule-day wrapper):
      #zerock-board.schedule-grid
        .schedule-top  (grid-column:C; grid-row:1)   × 7
        .schedule-show (grid-column:C; grid-row:R1/R2) × many
    """
    JUST_ROCK_SLUG    = 'just-rock'
    JUST_ROCK_NAME    = 'רוק ברצף'
    ROCKY_BROADCASTER = 'רוקי'
    WP_BASE           = 'https://zerockradio.com/shows/'

    GRID_START_H  = 7   # grid visible range start (hours)
    GRID_END_H    = 24  # grid visible range end
    MINS_PER_ROW  = 30  # one CSS grid row = 30 minutes
    ROW_PX        = 30  # pixel height per grid row

    TOTAL_TIME_ROWS = int((GRID_END_H - GRID_START_H) * 60 / MINS_PER_ROW)  # 34

    def t_to_row(h):
        """Float hour (clamped to grid range) → CSS grid row number.
        Row 1 = header; Row 2 = GRID_START_H; Row 2+TOTAL_TIME_ROWS = GRID_END_H."""
        clamped = max(GRID_START_H, min(GRID_END_H, h))
        return 2 + int(round((clamped - GRID_START_H) * 60 / MINS_PER_ROW))

    # WP day index 0=Sun..6=Sat — names + optional English subtitle
    DAY_NAMES = [
        ('יום ראשון',  ''),
        ('יום שני',    ''),
        ('יום שלישי', ''),
        ('יום רביעי', ''),
        ('יום חמישי', ''),
        ('יום שישי',  ''),
        ('שבת',        ''),
    ]

    all_slots = _build_wp_schedule_slots()

    # Zikaron (יום הזיכרון) is intentionally NOT rendered on the WP board —
    # per policy, the weekly grid shows the regular schedule only.
    zikaron_ranges = {}

    # NOTE: CSS lives in _sync_wp_board path 4 (ihaf_insert_footer), NOT here.
    # Keeping CSS out of the HTML prevents it from leaking into page meta descriptions
    # via Rank Math reading post 254 content (updated by zerock/v1/schedule).
    html_parts = ['<div id="zerock-board" class="schedule-grid">']

    # ── Day header row (grid-row: 1) ──────────────────────────────────────────
    for day_idx in range(7):
        col = day_idx + 1
        day_name, day_subtitle = DAY_NAMES[day_idx]
        sub_html = f'<span>{day_subtitle}</span>' if day_subtitle else ''
        html_parts.append(
            f'<div class="schedule-top" style="grid-column:{col};grid-row:1">'
            f'{day_name}{sub_html}</div>'
        )

    # ── Show cells (flat, each positioned by grid-column + grid-row) ──────────
    for day_idx in range(7):
        col       = day_idx + 1
        day_slots = sorted(all_slots[day_idx], key=lambda s: s['start_h'])

        # Fill gaps with Rocky
        filled = []
        cursor = 0.0
        for slot in day_slots:
            if slot['start_h'] > cursor + 0.01:
                filled.append({
                    'start_h': cursor, 'end_h': slot['start_h'],
                    'key': '__rocky__', 'name': JUST_ROCK_NAME,
                    'slug': JUST_ROCK_SLUG, 'broadcaster': ROCKY_BROADCASTER,
                    'rerun': False,
                })
            filled.append(slot)
            cursor = slot['end_h']
        if cursor < 24.0:
            filled.append({
                'start_h': cursor, 'end_h': 24.0,
                'key': '__rocky__', 'name': JUST_ROCK_NAME,
                'slug': JUST_ROCK_SLUG, 'broadcaster': ROCKY_BROADCASTER,
                'rerun': False,
            })

        prev_vis_end = GRID_START_H   # track end of previous visible show (per column)

        for slot in filled:
            s_h = slot['start_h']
            e_h = slot['end_h']

            # Clip to grid visible range; skip if entirely outside
            vis_start = max(GRID_START_H, s_h)
            vis_end   = min(GRID_END_H,   e_h)
            if vis_end <= vis_start + 0.01:
                continue  # e.g. overnight Rocky 00:00–08:00

            row_start = t_to_row(vis_start)
            row_end   = t_to_row(vis_end)
            if row_end <= row_start:
                continue

            # Add top border only when this show starts after an empty gap
            # (consecutive shows share only one border — the previous show's bottom edge)
            show_cls = 'schedule-show gap-top' if vis_start > prev_vis_end + 0.01 else 'schedule-show'
            prev_vis_end = vis_end

            # Time label shows actual (unclipped) times
            sh_i = int(s_h);          sm_i = int((s_h - sh_i) * 60)
            eh_i = int(e_h) % 24;     em_i = int((e_h - int(e_h)) * 60)
            time_str  = f"{sh_i:02d}:{sm_i:02d} - {eh_i:02d}:{em_i:02d}"
            show_url  = WP_BASE + slot['slug'] + '/' if slot['slug'] else '#'
            name_html = (f'<a href="{show_url}" class="pagelink">{slot["name"]}</a>'
                         if slot['slug'] else slot['name'])

            html_parts.append(
                f'<div class="{show_cls}" '
                f'style="grid-column:{col};grid-row:{row_start}/{row_end}">'
            )
            html_parts.append(f'<div class="schedule-show-time">{time_str}</div>')
            html_parts.append(f'<div class="schedule-show-the-show">{name_html}</div>')
            html_parts.append('<div class="broadcaster-socials"></div>')
            html_parts.append(f'<div class="schedule-show-text">{slot["broadcaster"]}</div>')
            html_parts.append('</div>')

    html_parts.append('</div>')
    return '\n'.join(html_parts)

_wp_sync_lock = threading.Lock()

def _sync_wp_board(force=False):
    """Push the current schedule HTML to the WordPress schedule board.

    Saturday-only policy: by default (force=False), skip automatic triggers.
    Only runs when force=True — that is, the scheduled Saturday→Sunday midnight
    refresh or a manual /api/wp-sync call. This prevents mid-week changes
    (uploads, deletes, show triggers, zikaron updates, restarts) from
    publishing next-week data too early.

    Priority order (each attempt is independent, all run):
    1. POST zerock/v1/schedule  — custom mu-plugin endpoint (updates option + page content).
       Works once zerock-schedule-api.php is installed in wp-content/mu-plugins/.
    2. POST wc-admin/options    — writes zerock_board_html to WP option.
       Reads when page-schedule.php contains: echo get_option('zerock_board_html','');
       WAF does NOT block this endpoint.
    3. PUT wp/v2/pages/254      — updates page post_content (visible in meta/excerpt,
       not in the schedule grid unless template calls the_content()).
       WAF blocks PATCH but NOT PUT — use PUT.

    All three run in the background; the first two are the important ones.
    """
    if not force:
        print("[WPSync] Skipped (Saturday-only policy — call with force=True to override)", flush=True)
        return

    def _do_sync():
        with _wp_sync_lock:
            try:
                html = _build_wp_schedule_html()
            except Exception as e:
                print(f"[WPSync] HTML build error: {e}", flush=True)
                return

            auth    = (WP_USER, WP_APP_PASS)
            headers = {'Content-Type': 'application/json'}
            results = {}

            # ── 1. Custom mu-plugin endpoint (best: updates option + page content) ──
            try:
                r = _requests.post(
                    f"{WP_REST_BASE}/zerock/v1/schedule",
                    json={'html': html},
                    auth=auth, headers=headers, timeout=15
                )
                results['zerock/v1'] = r.status_code
                if r.status_code == 200:
                    print("[WPSync] ✓ zerock/v1/schedule (plugin endpoint)", flush=True)
                else:
                    print(f"[WPSync] zerock/v1 → {r.status_code} (plugin not installed?)", flush=True)
            except Exception as e:
                results['zerock/v1'] = f'err:{e}'
                print(f"[WPSync] zerock/v1 error: {e}", flush=True)

            # ── 2. wc-admin/options — writes zerock_board_html option (WAF safe) ──
            try:
                r2 = _requests.post(
                    f"{WP_REST_BASE}/wc-admin/options",
                    json={'zerock_board_html': html},
                    auth=auth, headers=headers, timeout=15
                )
                results['wc-admin'] = r2.status_code
                if r2.status_code == 200:
                    print("[WPSync] ✓ wc-admin/options (zerock_board_html updated)", flush=True)
                else:
                    print(f"[WPSync] wc-admin/options → {r2.status_code}: {r2.text[:100]}", flush=True)
            except Exception as e2:
                results['wc-admin'] = f'err:{e2}'
                print(f"[WPSync] wc-admin/options error: {e2}", flush=True)

            # ── 3. PUT page 254 content (WAF blocks PATCH, not PUT) ──
            try:
                r3 = _requests.put(
                    f"{WP_REST_BASE}/wp/v2/pages/{WP_SCHEDULE_PAGE_ID}",
                    json={'content': html},
                    auth=auth, headers=headers, timeout=15
                )
                results['put-page'] = r3.status_code
                if r3.status_code == 200:
                    print("[WPSync] ✓ PUT page/254 content updated", flush=True)
                else:
                    print(f"[WPSync] PUT page/254 → {r3.status_code}", flush=True)
            except Exception as e3:
                results['put-page'] = f'err:{e3}'
                print(f"[WPSync] PUT page error: {e3}", flush=True)

            # ── 4. CSS + replacement div via WPCode ihaf_insert_footer ──────────
            # Injects the schedule HTML as a plain <div id="zerock-board"> in the
            # WP footer, then hides the PHP-rendered .schedule-grid via CSS.
            #
            # WHY CSS+HTML instead of <script>:
            #   • upress.io F5 BIG-IP WAF blocks any POST body containing <script>
            #     tags (XSS rule), regardless of IP reputation.
            #   • Pure CSS+HTML has no such restriction — posts successfully.
            #   • CSS rule: .schedule-grid:not(#zerock-board){display:none}
            #     hides the PHP grid; our div (with the class) inherits flex layout.
            try:
                # html already contains id="zerock-board" (set in _build_wp_schedule_html).
                # Border strategy:
                #  - Column separators: background-image (empty areas) + border-right (cells)
                #  - Header bottom: border-bottom on .schedule-top
                #  - Show separators: border-bottom on every show + border-top only on
                #    shows with class "gap-top" (starts after empty space) to avoid doubling
                # NOTE: no <script> — upress.io WAF permanently blocks <script> in POST bodies.
                _GRID_ROWS = 34  # (GRID_END_H=24 − GRID_START_H=7) × 60 / MINS_PER_ROW=30
                css = (
                    '<style>'
                    '.schedule-grid:not(#zerock-board){display:none!important}'
                    '#zerock-board{'
                    'display:grid!important;'
                    'grid-template-columns:repeat(7,1fr);'
                    f'grid-template-rows:45px repeat({_GRID_ROWS},40px);'
                    'gap:0;width:1140px;max-width:100%;margin:0 auto;'
                    'background-color:#2a2a2a;'
                    'border:1px solid rgba(255,255,255,.25);'
                    # Column separators in empty areas (show cells cover this with their own border-right)
                    'background-image:repeating-linear-gradient(to right,transparent 0,transparent calc(100%/7 - 1px),rgba(255,255,255,.25) calc(100%/7 - 1px),rgba(255,255,255,.25) calc(100%/7));'
                    'background-size:100% 100%;background-repeat:no-repeat;'
                    '}'
                    '#zerock-board .schedule-top{'
                    'height:auto!important;box-sizing:border-box;background-color:inherit;'
                    'border-right:1px solid rgba(255,255,255,.25);'
                    'border-bottom:1px solid rgba(255,255,255,.25);'
                    'display:flex;flex-direction:column;justify-content:center;align-items:center;'
                    'padding:5px;font-weight:bold;'
                    '}'
                    '#zerock-board .schedule-show{'
                    'height:auto!important;box-sizing:border-box;overflow:hidden;'
                    'background-color:inherit!important;'
                    'border-right:1px solid rgba(255,255,255,.25);'
                    '}'
                    '#zerock-board .schedule-show.gap-top{'
                    'border-top:1px solid rgba(255,255,255,.25);'
                    '}'
                    '</style>'
                )
                footer_content = css + '\n' + html
                r4 = _requests.post(
                    f"{WP_REST_BASE}/wc-admin/options",
                    json={'ihaf_insert_footer': footer_content},
                    auth=auth, headers=headers, timeout=15
                )
                results['js-inject'] = r4.status_code
                if r4.status_code == 200:
                    print("[WPSync] ✓ CSS+HTML injection → ihaf_insert_footer updated", flush=True)
                else:
                    print(f"[WPSync] CSS+HTML inject → {r4.status_code}: {r4.text[:100]}", flush=True)
            except Exception as e4:
                results['js-inject'] = f'err:{e4}'
                print(f"[WPSync] CSS+HTML inject error: {e4}", flush=True)

            print(f"[WPSync] done — {results}", flush=True)

    threading.Thread(target=_do_sync, daemon=True).start()

@app.route('/api/schedule', methods=['GET'])
def api_get_schedule():
    return jsonify(load_schedule())

@app.route('/api/shows')
def api_shows():
    """Return the show schedule with calculated next broadcast times."""
    result = []
    for s in SHOW_SCHEDULE:
        broadcast_dt = _next_broadcast_dt(s)
        upload_dt    = _calc_upload_dt(broadcast_dt, s) if broadcast_dt else None
        rerun_dt     = _calc_rerun_dt(broadcast_dt, s)  if broadcast_dt else None
        result.append({
            'key':          s['key'],
            'label':        _show_label(s),
            'name':         s['name'],
            'broadcaster':  s['broadcaster'],
            'slug':         _show_slug(s),
            'slug_en':      _slug_en(s),
            'manual_date':  s['day'] is None,
            'time':         s['time'],
            'upload_time':  s['upload_time'],
            'has_rerun':    s['rerun_days_offset'] is not None,
            'no_podbean':   s.get('no_podbean', False),
            'next_broadcast': broadcast_dt.isoformat() if broadcast_dt else None,
            'next_upload':    upload_dt.isoformat()    if upload_dt    else None,
            'next_rerun':     rerun_dt.isoformat()     if rerun_dt     else None,
        })
    return jsonify(result)

@app.route('/api/schedule', methods=['POST'])
def api_add_show():
    show_key              = request.form.get('show_key', '').strip()
    manual_date           = request.form.get('manual_date', '').strip()   # YYYY-MM-DD, only for על הרוקר
    al_haroker_broadcaster = request.form.get('al_haroker_broadcaster', '').strip()  # broadcaster for על הרוקר
    mode                  = request.form.get('mode', 'queue_to_broadcast').strip()
    episode_num           = request.form.get('episode_num', '').strip()
    description           = request.form.get('description', '').strip()
    manual_schedule       = request.form.get('manual_schedule', '') == 'on'
    manual_broadcast_time = request.form.get('manual_broadcast_time', '').strip()
    # Support album show (album_0…album_7) or regular single-file upload (file)
    albums_raw = []
    for i in range(8):
        slot_files = [f for f in request.files.getlist(f'album_{i}') if f and f.filename]
        if slot_files:
            albums_raw.append(sorted(slot_files, key=lambda f: os.path.basename(f.filename).lower()))
    is_album  = len(albums_raw) > 0

    playlist_raw = []
    for i in range(20):
        pf = request.files.get(f'playlist_{i}')
        if pf and pf.filename:
            playlist_raw.append(pf)
    palash_raw = []
    for i in range(5):
        pf = request.files.get(f'palash_{i}')
        if pf and pf.filename:
            palash_raw.append(pf)
    is_playlist = len(playlist_raw) > 0 or len(palash_raw) > 0

    audio_file = request.files.get('file')

    if not is_album and not is_playlist and not audio_file:
        return jsonify({'error': 'Audio file is required'}), 400

    # ── Look up show config ────────────────────────────────────────────────────
    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show_key), None)
    # Force queue_only for shows that have no Podbean/WP upload
    if show_cfg and show_cfg.get('no_podbean'):
        mode = 'queue_only'
    if not show_cfg:
        # Legacy: support free-form name + manual scheduled_time
        name           = request.form.get('name', '').strip()
        scheduled_time = request.form.get('scheduled_time', '').strip()
        if not name or not scheduled_time:
            return jsonify({'error': 'show_key or (name + scheduled_time) required'}), 400
        broadcast_dt = datetime.fromisoformat(scheduled_time)
        upload_dt    = None
        rerun_dt     = None
    elif manual_schedule and manual_broadcast_time:
        # Manual override: use the user-supplied datetime, no rerun, no auto upload
        broadcast_dt = datetime.fromisoformat(manual_broadcast_time)
        upload_dt    = None
        rerun_dt     = None
        name         = _show_label(show_cfg)
    else:
        broadcast_dt = _next_broadcast_dt(show_cfg, manual_date if show_cfg['day'] is None else None)
        if not broadcast_dt:
            return jsonify({'error': 'Manual date required for this show'}), 400
        upload_dt = _calc_upload_dt(broadcast_dt, show_cfg)
        rerun_dt  = _calc_rerun_dt(broadcast_dt, show_cfg)
        name      = _show_label(show_cfg)

    # ── 2-month advance-upload cap ────────────────────────────────────────────
    if broadcast_dt and (broadcast_dt - datetime.now()) > timedelta(days=61):
        return jsonify({'error': 'לא ניתן להעלות פרק ליותר מחודשיים מראש'}), 400

    # ── Duplicate guard ────────────────────────────────────────────────────────
    # Reject if a non-rerun entry for the same show_key + broadcast time already exists
    if show_cfg and broadcast_dt:
        existing = load_schedule()
        bcast_iso = broadcast_dt.isoformat()
        dup = next((e for e in existing
                    if e.get('show_key') == show_key
                    and e.get('scheduled_time') == bcast_iso
                    and not e.get('is_rerun')), None)
        if dup:
            print(f"[Schedule] Duplicate rejected: {show_key} @ {bcast_iso}", flush=True)
            return jsonify({'success': True, 'show': dup, '_duplicate': True}), 200

    # ── Save file(s) ───────────────────────────────────────────────────────────
    show_id = str(int(time.time() * 1000))

    if is_album:
        # Save each album's tracks; preserve order within each album
        saved_albums   = []   # list of lists of local paths
        all_tracks     = []   # flat list for cleanup
        playlist_paths = None
        for album_idx, slot_files in enumerate(albums_raw):
            album_paths = []
            for track_idx, af in enumerate(slot_files):
                safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                                    for c in os.path.basename(af.filename))
                fname = f"{show_id}_a{album_idx:02d}_t{track_idx:03d}_{safe_name}"
                lpath = os.path.join(LOCAL_TEMP, fname)
                af.save(lpath)
                album_paths.append(lpath)
                all_tracks.append(lpath)
            saved_albums.append(album_paths)

        local_path    = all_tracks[0]
        nas_path      = all_tracks[0]   # no NAS move needed (queue_only, no Podbean)
        original_name = f"{len(saved_albums)} album{'s' if len(saved_albums) != 1 else ''}, {len(all_tracks)} tracks total"
    elif is_playlist:
        # Save מקום tracks in order; record slot numbers and badge selections
        playlist_paths  = []
        playlist_slots  = []   # slot numbers (1-based) matching each path
        playlist_badges = []   # per slot (index 0=מקום1 … index 19=מקום20): list of badge keys
        for i in range(20):
            # Always record badges for this slot (even if no file uploaded)
            slot_badges = []
            if request.form.get(f'pl_{i}_aliya'):     slot_badges.append('aliya')
            if request.form.get(f'pl_{i}_yerida'):    slot_badges.append('yerida')
            if request.form.get(f'pl_{i}_knisa'):     slot_badges.append('knisa')
            if request.form.get(f'pl_{i}_knisa_new'): slot_badges.append('knisa_new')
            playlist_badges.append(slot_badges)

            pf = request.files.get(f'playlist_{i}')
            if not pf or not pf.filename:
                continue
            safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                                for c in os.path.basename(pf.filename))
            fname = f"{show_id}_pl{i:02d}_{safe_name}"
            lpath = os.path.join(LOCAL_TEMP, fname)
            pf.save(lpath)
            playlist_paths.append(lpath)
            playlist_slots.append(i + 1)   # מקום 1 = index 0, מקום 20 = index 19
        # Save פל"ש tracks in order
        palash_paths = []
        for idx, pf in enumerate(palash_raw):
            safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                                for c in os.path.basename(pf.filename))
            fname = f"{show_id}_pa{idx:02d}_{safe_name}"
            lpath = os.path.join(LOCAL_TEMP, fname)
            pf.save(lpath)
            palash_paths.append(lpath)

        saved_albums  = None
        all_tracks    = playlist_paths + palash_paths
        local_path    = all_tracks[0]
        nas_path      = all_tracks[0]  # queue_only, no NAS move needed
        n_pl  = len(playlist_paths)
        n_pa  = len(palash_paths)
        original_name = f"{n_pl} מקום + {n_pa} פל\"ש" if n_pa else f"{n_pl} מקום (playlist)"
    else:
        safe_name  = "".join(c if c.isalnum() or c in ' _-.' else '_' for c in audio_file.filename)
        filename   = f"{int(time.time())}_{safe_name}"
        local_path = os.path.join(LOCAL_TEMP, filename)
        nas_path   = os.path.join(NAS_TEMP, filename)
        audio_file.save(local_path)
        saved_albums   = None
        all_tracks     = None
        playlist_paths = None
        palash_paths   = None
        original_name  = audio_file.filename

    show = {
        'id':             show_id,
        'name':           name,
        'show_key':       show_key,
        'broadcaster':    (al_haroker_broadcaster if show_key == 'al_harocker' and al_haroker_broadcaster
                          else (show_cfg['broadcaster'] if show_cfg else '')),
        'mode':           mode,
        'episode_num':    episode_num,
        'description':    description,
        'scheduled_time': broadcast_dt.isoformat(),
        'upload_time':    upload_dt.isoformat() if upload_dt else None,
        'rerun_time':     rerun_dt.isoformat()  if rerun_dt  else None,
        'file_path':      local_path,
        'nas_path':       nas_path,
        'nas_ready':      True if (is_album or is_playlist) else False,
        'albums':         saved_albums,    # [[track, track, ...], [track, track, ...], ...]
        'playlist_files':  playlist_paths  if is_playlist else None,  # מקום paths (in slot order)
        'playlist_slots':  playlist_slots  if is_playlist else None,  # slot numbers matching paths
        'playlist_badges': playlist_badges if is_playlist else None,  # badges per slot index
        'palash_files':   palash_paths   if is_playlist else None,  # פל"ש 1-5 (in order after מקום)
        'files':          all_tracks,      # flat list for cleanup
        'original_name':  original_name,
        'triggered':      False,
        'rerun_scheduled':False,
        'upload_done':    False,
        'is_rerun':       False,
        'added_at':       datetime.now().isoformat(),
    }
    with _schedule_lock:
        schedule = load_schedule()
        schedule.append(show)
        if not is_album:
            # Immediately schedule the rerun so it appears in upcoming shows
            rerun = _make_rerun_entry(show)
            if rerun:
                schedule.append(rerun)
                show['rerun_scheduled'] = True
        save_schedule(schedule)

    # Sync WP board so the new show appears in the schedule immediately
    threading.Thread(target=_sync_wp_board, daemon=True).start()

    if not is_album and not is_playlist:
        threading.Thread(target=_move_to_nas, args=(show_id, local_path, nas_path), daemon=True).start()

    # Immediately upload to Podbean/WP for queue_to_broadcast (non-album, non-playlist, non-manual) shows
    if mode == 'queue_to_broadcast' and not is_album and not is_playlist and not manual_schedule:
        with _schedule_lock:
            schedule = load_schedule()
            for s in schedule:
                if s['id'] == show_id:
                    s['upload_in_progress'] = True
                    break
            save_schedule(schedule)
        threading.Thread(target=_upload_and_mark_done, args=(show_id,), daemon=True).start()
        print(f"[Schedule] Upload thread started for '{name}'")

    print(f"[Schedule] Queued '{name}' for {broadcast_dt.isoformat()} — {original_name}")
    return jsonify({'success': True, 'show': show})

@app.route('/api/schedule/<show_id>/reschedule', methods=['POST'])
def api_reschedule_show(show_id):
    """Move an upcoming show to a new broadcast time.
    Accepts JSON: { new_time: "YYYY-MM-DDTHH:MM" }
    Also shifts the rerun entry (if any) by the same delta.
    """
    data = request.get_json(force=True) or {}
    new_time_str = (data.get('new_time') or '').strip()
    if not new_time_str:
        return jsonify({'error': 'new_time required'}), 400
    try:
        new_broadcast = datetime.fromisoformat(new_time_str)
    except ValueError:
        return jsonify({'error': 'Invalid datetime format'}), 400

    with _schedule_lock:
        schedule = load_schedule()
        entry = next((e for e in schedule if e.get('id') == show_id), None)
        if not entry:
            return jsonify({'error': 'Show not found'}), 404
        if entry.get('triggered'):
            return jsonify({'error': 'Show already triggered — cannot reschedule'}), 400
        is_rerun = entry.get('is_rerun', False)

        # Compute delta
        try:
            old_broadcast = datetime.fromisoformat(entry['scheduled_time'])
            delta = new_broadcast - old_broadcast
        except Exception:
            delta = None

        # Update this entry's scheduled_time
        entry['scheduled_time'] = new_broadcast.isoformat()

        # Shift delete_after if present (applies to reruns too)
        if entry.get('delete_after') and delta:
            try:
                old_del = datetime.fromisoformat(entry['delete_after'])
                entry['delete_after'] = (old_del + delta).isoformat()
            except Exception:
                pass

        if not is_rerun:
            # Shift upload_time for primary entries
            if entry.get('upload_time') and delta:
                try:
                    old_upload = datetime.fromisoformat(entry['upload_time'])
                    entry['upload_time'] = (old_upload + delta).isoformat()
                except Exception:
                    pass
            # Shift the paired rerun entry by the same delta
            rerun_id = entry.get('rerun_id')
            if rerun_id and delta:
                rerun = next((e for e in schedule if e.get('id') == rerun_id), None)
                if rerun and not rerun.get('triggered'):
                    try:
                        old_rerun = datetime.fromisoformat(rerun['scheduled_time'])
                        rerun['scheduled_time'] = (old_rerun + delta).isoformat()
                    except Exception:
                        pass

        save_schedule(schedule)
        print(f"[Schedule] Rescheduled '{entry.get('name')}' ({'rerun' if is_rerun else 'primary'}) → {new_broadcast.isoformat()}", flush=True)

    # Update WordPress publish time + title — only for primary (non-rerun) entries with a WP post
    wp_post_id = entry.get('wp_post_id')
    if wp_post_id and entry.get('upload_done') and not entry.get('is_rerun'):
        try:
            new_ts = int(new_broadcast.timestamp())
            _requests.post(
                f"http://192.168.1.114:3001/api/reschedule-wp",
                json={'wp_post_id': wp_post_id, 'new_timestamp': new_ts},
                timeout=10
            )
            print(f"[Schedule] WP post {wp_post_id} rescheduled to {new_broadcast.isoformat()}", flush=True)
        except Exception as e:
            print(f"[Schedule] WP reschedule failed (non-fatal): {e}", flush=True)
        # Update WP title to reflect the new broadcast date
        new_title = _make_show_title(entry, new_broadcast)
        threading.Thread(target=_update_wp_title, args=(wp_post_id, new_title), daemon=True).start()
        # Update Podbean episode title too (if episode was uploaded)
        podbean_url = entry.get('podbean_url', '')
        if podbean_url:
            threading.Thread(target=_update_podbean_title, args=(podbean_url, new_title), daemon=True).start()

    return jsonify({'success': True, 'show': entry})

@app.route('/api/schedule-url', methods=['POST'])
def api_schedule_url():
    """Queue a show via a download URL instead of a direct file upload.
    Accepts JSON: {show_key, media_url, broadcaster, manual_date, mode, episode_num, description, original_name}
    Rocky downloads the file from media_url and schedules it.
    """
    data                  = request.get_json(force=True) or {}
    show_key              = data.get('show_key', '').strip()
    media_url             = data.get('media_url', '').strip()
    broadcaster           = data.get('broadcaster', '').strip()
    manual_date           = data.get('manual_date', '').strip()
    mode                  = data.get('mode', 'queue_to_broadcast')
    episode_num           = data.get('episode_num', '').strip()
    description           = data.get('description', '').strip()
    original_name         = data.get('original_name', 'show.mp3').strip()
    manual_schedule       = data.get('manual_schedule', False)
    manual_broadcast_time = data.get('manual_broadcast_time', '').strip()
    wp_post_id            = data.get('wp_post_id') or None   # passed by uploader after WP creation

    if not show_key or not media_url:
        return jsonify({'error': 'show_key and media_url are required'}), 400

    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show_key), None)
    if not show_cfg:
        return jsonify({'error': f'Unknown show_key: {show_key}'}), 400

    if manual_schedule and manual_broadcast_time:
        # Manual override: use the user-supplied datetime, no rerun, no auto upload
        broadcast_dt = datetime.fromisoformat(manual_broadcast_time)
        upload_dt    = None
        rerun_dt     = None
        name         = _show_label(show_cfg)
    else:
        broadcast_dt = _next_broadcast_dt(show_cfg, manual_date if show_cfg['day'] is None else None)
        if not broadcast_dt:
            return jsonify({'error': 'Manual date required for this show (day=None)'}), 400
        upload_dt = _calc_upload_dt(broadcast_dt, show_cfg)
        rerun_dt  = _calc_rerun_dt(broadcast_dt, show_cfg)
        name      = _show_label(show_cfg)

    # ── If an entry already exists for this show+time, update it in-place (no duplicate) ──
    if mode == 'queue_only':
        bcast_iso = broadcast_dt.isoformat()
        with _schedule_lock:
            schedule = load_schedule()
            existing = next((s for s in schedule
                             if s.get('show_key') == show_key
                             and s.get('scheduled_time') == bcast_iso
                             and not s.get('is_rerun')), None)
            if existing:
                existing['upload_in_progress'] = False
                if not existing.get('upload_done'):
                    existing['upload_done']    = True
                    existing['upload_done_at'] = datetime.now().isoformat()
                if wp_post_id and not existing.get('wp_post_id'):
                    existing['wp_post_id'] = wp_post_id
                save_schedule(schedule)
                print(f"[ScheduleURL] Updated existing entry {existing['id']} for '{name}' "
                      f"(skipped duplicate) wp_post_id={wp_post_id}")
                return jsonify({'ok': True, 'id': existing['id'], 'name': name,
                                'scheduled_time': existing['scheduled_time']})

    # Download file from URL
    safe_name  = "".join(c if c.isalnum() or c in ' _-.' else '_' for c in original_name)
    filename   = f"{int(time.time())}_{safe_name}"
    local_path = os.path.join(LOCAL_TEMP, filename)
    nas_path   = os.path.join(NAS_TEMP, filename)
    try:
        r = _requests.get(media_url, timeout=300, stream=True)
        r.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        print(f"[ScheduleURL] Downloaded {media_url} → {local_path} ({os.path.getsize(local_path)//1024}KB)")
    except Exception as e:
        return jsonify({'error': f'File download failed: {e}'}), 502

    show_id = str(int(time.time() * 1000))
    show = {
        'id':              show_id,
        'name':            name,
        'show_key':        show_key,
        'broadcaster':     broadcaster,
        'mode':            mode,
        'episode_num':     episode_num,
        'description':     description,
        'scheduled_time':  broadcast_dt.isoformat(),
        'upload_time':     upload_dt.isoformat() if upload_dt else None,
        'rerun_time':      rerun_dt.isoformat()  if rerun_dt  else None,
        'file_path':       local_path,
        'nas_path':        nas_path,
        'nas_ready':       False,
        'original_name':   original_name,
        'triggered':       False,
        'rerun_scheduled': False,
        'upload_done':     False,
        'is_rerun':        False,
        'added_at':        datetime.now().isoformat(),
        **({'wp_post_id': wp_post_id} if wp_post_id else {}),
    }
    with _schedule_lock:
        schedule = load_schedule()
        schedule.append(show)
        # Immediately schedule the rerun so it appears in upcoming shows
        rerun = _make_rerun_entry(show)
        if rerun:
            schedule.append(rerun)
            show['rerun_scheduled'] = True
        save_schedule(schedule)

    threading.Thread(target=_move_to_nas, args=(show_id, local_path, nas_path), daemon=True).start()

    # Immediately upload to Podbean/WP for queue_to_broadcast (non-manual) shows
    if mode == 'queue_to_broadcast' and not manual_schedule:
        with _schedule_lock:
            schedule = load_schedule()
            for s in schedule:
                if s['id'] == show_id:
                    s['upload_in_progress'] = True
                    break
            save_schedule(schedule)
        threading.Thread(target=_upload_and_mark_done, args=(show_id,), daemon=True).start()
        print(f"[ScheduleURL] Upload thread started for '{name}'")

    print(f"[ScheduleURL] Queued '{name}' for {broadcast_dt.isoformat()}")
    return jsonify({'ok': True, 'id': show_id, 'name': name, 'scheduled_time': broadcast_dt.isoformat()})

@app.route('/api/schedule/<show_id>', methods=['DELETE'])
def api_delete_show(show_id):
    schedule = load_schedule()
    to_delete = next((s for s in schedule if s['id'] == show_id), None)
    if to_delete and not to_delete.get('triggered'):
        try:
            os.remove(to_delete['file_path'])
        except Exception:
            pass
    schedule = [s for s in schedule if s['id'] != show_id]
    save_schedule(schedule)

    # If this was a primary (non-rerun) episode for a fixed-day show,
    # mark that show as cancelled on the board for this week.
    if to_delete and not to_delete.get('is_rerun') and not to_delete.get('triggered'):
        sk = to_delete.get('show_key', '')
        if sk:
            cfg = next((s for s in SHOW_SCHEDULE if s['key'] == sk), None)
            if cfg and cfg.get('day') is not None and sk not in QUEUE_ONLY_BOARD_SHOWS:
                _cancel_show_on_board(sk)
    _sync_wp_board()

    return jsonify({'success': True})

@app.route('/api/trigger/<show_id>', methods=['POST'])
def api_trigger_now(show_id):
    """Manually trigger a show immediately."""
    schedule = load_schedule()
    show = next((s for s in schedule if s['id'] == show_id), None)
    if not show:
        return jsonify({'error': 'Show not found'}), 404
    success = trigger_show(show)
    if success:
        show['triggered'] = True
        show['triggered_at'] = datetime.now().isoformat()
        save_schedule(schedule)
    return jsonify({'success': success})

@app.route('/api/skip', methods=['POST'])
def api_skip():
    """Skip current track — uses direct socket (no shared lock) so it's instant."""
    global _np_last_path, _np_track_start
    resp = lq_send_direct(["rocky_out.skip"])
    # Reset track tracking so the updater picks up the new track immediately
    _np_last_path   = ""
    _np_track_start = None
    return jsonify({'response': resp.strip()[:200]})

@app.route('/api/play-now', methods=['POST'])
def api_play_now():
    """Immediately play an uploaded file (no jingles). Push directly to shows queue."""
    audio_file = request.files.get('file')
    if not audio_file:
        return jsonify({'error': 'No file provided'}), 400

    safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_' for c in audio_file.filename)
    filename  = f"{int(time.time())}_{safe_name}"
    file_path = os.path.join(LOCAL_TEMP, filename)
    audio_file.save(file_path)

    j1 = get_random_jingle()
    j2 = get_random_jingle()
    cmds = []
    if j1 and os.path.exists(j1):
        cmds.append(f"shows.push {j1}")
    cmds.append(f"shows.push {file_path}")
    if j2 and os.path.exists(j2):
        cmds.append(f"shows.push {j2}")

    resp = lq_send(cmds)
    success = "ERROR" not in resp
    if success:
        duration = get_audio_duration(file_path)
        with _play_now_lock:
            _play_now_cleanup.append({
                'file': file_path,
                'delete_after': datetime.now() + timedelta(seconds=duration + 600)
            })
    return jsonify({'success': success, 'response': resp.strip()[:200]})

@app.route('/api/queue-file', methods=['POST'])
def api_queue_file():
    """Push a NAS file path directly to the shows queue (play immediately)."""
    file_path = request.json.get('path', '').strip() if request.is_json else request.form.get('path', '').strip()
    if not file_path:
        return jsonify({'error': 'No path provided'}), 400
    if not os.path.exists(file_path):
        return jsonify({'error': f'File not found: {file_path}'}), 404

    resp = lq_send([f"shows.push {file_path}"])
    success = "ERROR" not in resp
    return jsonify({'success': success, 'response': resp.strip()[:200]})

@app.route('/api/board-html', methods=['GET'])
def api_board_html():
    """Public endpoint: returns the current WP schedule grid HTML as JSON.
    Used by the ihaf_insert_footer JS snippet to replace the WP schedule page
    grid in real time.  No auth required — data is just schedule HTML.
    CORS: allowed from zerockradio.com so the browser fetch works.
    """
    try:
        html = _build_wp_schedule_html()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    resp = jsonify({'html': html})
    resp.headers['Access-Control-Allow-Origin'] = 'https://zerockradio.com'
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/api/wp-sync', methods=['POST'])
def api_wp_sync():
    """Manually trigger a WP schedule board sync."""
    _sync_wp_board(force=True)
    return jsonify({'ok': True, 'message': 'WP board sync triggered (background)'})

# ── Startup tasks ─────────────────────────────────────────────────────────────
# Push the current weekly schedule to the WP board on every server restart.
# This keeps the WP board current whenever SHOW_SCHEDULE is updated in code.
threading.Thread(target=lambda: (
    __import__('time').sleep(5),   # wait for Flask to be fully up first
    _sync_wp_board()
), daemon=True).start()

def _start_weekly_board_refresh():
    """Background thread: at Sunday midnight clears board cancellations and refreshes WP board."""
    def _run():
        last_refresh_week = None
        while True:
            time.sleep(300)   # check every 5 minutes
            now = datetime.now()
            if now.weekday() == 6 and now.hour == 0 and now.minute < 10:
                week_key = now.strftime('%Y-%U')
                if week_key != last_refresh_week:
                    last_refresh_week = week_key
                    print("[WeeklyRefresh] Sunday midnight — clearing board cancellations and syncing WP board", flush=True)
                    _clear_board_cancellations()
                    _sync_wp_board(force=True)
    threading.Thread(target=_run, daemon=True).start()

_start_weekly_board_refresh()

# ─── Al HaRoker self-service scheduling system ────────────────────────────────

_bookings_lock = threading.Lock()

_HEB_MONTHS = {
    1:'ינואר',2:'פברואר',3:'מרץ',4:'אפריל',5:'מאי',6:'יוני',
    7:'יולי',8:'אוגוסט',9:'ספטמבר',10:'אוקטובר',11:'נובמבר',12:'דצמבר'
}
_HEB_DAYS = {0:'שני',1:'שלישי',2:'רביעי',3:'חמישי',4:'שישי',5:'שבת',6:'ראשון'}


def _load_al_haroker_bookings():
    try:
        with open(AL_HAROKER_BOOKINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_al_haroker_bookings(data):
    with open(AL_HAROKER_BOOKINGS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _add_subscriber(name, email):
    """Add or update subscriber record (for monthly invite list)."""
    try:
        try:
            with open(AL_HAROKER_SUBSCRIBERS_FILE) as f:
                subs = json.load(f)
        except Exception:
            subs = []
        # Update if exists, else add
        existing = next((s for s in subs if s['email'].lower() == email.lower()), None)
        if existing:
            existing['last_registered'] = datetime.now().isoformat()
            existing['name'] = name   # update name in case it changed
        else:
            subs.append({
                'name':             name,
                'email':            email,
                'first_registered': datetime.now().isoformat(),
                'last_registered':  datetime.now().isoformat(),
            })
        with open(AL_HAROKER_SUBSCRIBERS_FILE, 'w') as f:
            json.dump(subs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[AlHaRoker] Subscriber save error: {e}", flush=True)


def _send_upload_email(booking):
    """Send a personal upload-link email to the registered broadcaster."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[AlHaRoker] SMTP not configured — skipping email to {booking['email']}", flush=True)
        return
    try:
        date_obj = datetime.strptime(booking['date'], '%Y-%m-%d')
        day_heb  = _HEB_DAYS[date_obj.weekday()]
        date_heb = f"יום {day_heb} {date_obj.strftime('%d/%m/%Y')}"
        upload_url = f"{ZEROCK_PUBLIC_URL}/al-haroker-upload/{booking['token']}"

        body_html = f"""<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;color:#222;line-height:1.6">
<p>שלום <strong>{booking['broadcaster']}</strong>,</p>
<p>נרשמת לשדר ב-<strong>ZeRock Radio</strong> בתכנית <em>על הרוקר</em><br>
<strong>{date_heb} בשעה 07:00.</strong></p>
<p>כדי להשלים את ההרשמה, העלה את קובץ הפרק דרך הקישור הבא:</p>
<p style="margin:24px 0">
  <a href="{upload_url}" style="background:#e63946;color:#fff;padding:14px 28px;
     text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px">
    🎙️ העלאת הפרק שלי
  </a>
</p>
<p style="color:#888;font-size:13px">
  לא עובד הכפתור? העתק לדפדפן:<br>
  <a href="{upload_url}" style="color:#e63946">{upload_url}</a>
</p>
<hr style="border:none;border-top:1px solid #ddd;margin:24px 0">
<p>שידור מוצלח! 🤘<br><strong>צוות ZeRock Radio</strong></p>
</div>"""

        body_text = (
            f"שלום {booking['broadcaster']},\n\n"
            f"נרשמת לשדר ב-ZeRock Radio ב{date_heb} בשעה 07:00.\n\n"
            f"להעלאת הפרק שלך:\n{upload_url}\n\n"
            f"שידור מוצלח!\nצוות ZeRock Radio"
        )

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"ZeRock Radio – על הרוקר {date_heb}"
        msg['From']    = SMTP_FROM_ADDR
        msg['To']      = booking['email']
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM_ADDR, [booking['email']], msg.as_bytes())
        print(f"[AlHaRoker] Upload email sent → {booking['email']} for {booking['date']}", flush=True)
    except Exception as e:
        print(f"[AlHaRoker] Email error: {e}", flush=True)


@app.route('/al-haroker-schedule')
@app.route('/al-haroker-schedule/<int:year>/<int:month>')
def al_haroker_schedule_page(year=None, month=None):
    now = datetime.now()
    if year is None or month is None:
        if now.year < AL_HAROKER_SCHEDULE_START.year or (
                now.year == AL_HAROKER_SCHEDULE_START.year
                and now.month < AL_HAROKER_SCHEDULE_START.month):
            year, month = AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month
        else:
            year, month = now.year, now.month

    # Clamp to minimum
    if (year, month) < (AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month):
        year, month = AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month

    # Build booked dates: date_str → broadcaster name
    bookings = _load_al_haroker_bookings()
    booked   = {b['date']: b['broadcaster'] for b in bookings}

    # Calendar weeks starting on Sunday (firstweekday=6)
    cal   = _calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    prev_year,  prev_month  = (year, month - 1) if month > 1 else (year - 1, 12)
    next_year,  next_month  = (year, month + 1) if month < 12 else (year + 1, 1)
    show_prev = (prev_year, prev_month) >= (
        AL_HAROKER_SCHEDULE_START.year, AL_HAROKER_SCHEDULE_START.month)

    return render_template(
        'al_haroker_schedule.html',
        year=year, month=month,
        month_name=_HEB_MONTHS[month],
        weeks=weeks,
        booked=booked,
        today=now.date(),
        start_date_limit=AL_HAROKER_SCHEDULE_START,
        prev_year=prev_year, prev_month=prev_month, show_prev=show_prev,
        next_year=next_year, next_month=next_month,
    )


@app.route('/api/al-haroker-register', methods=['POST'])
def api_al_haroker_register():
    data        = request.get_json(force=True) or {}
    date_str    = data.get('date', '').strip()
    broadcaster = data.get('broadcaster', '').strip()
    email       = data.get('email', '').strip()

    if not date_str or not broadcaster or not email:
        return jsonify({'error': 'date, broadcaster ו-email נדרשים'}), 400

    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'תאריך לא תקין'}), 400

    if date_obj < AL_HAROKER_SCHEDULE_START:
        return jsonify({'error': 'date_before_start'}), 400
    if date_obj < datetime.now().date():
        return jsonify({'error': 'date_past'}), 400
    if date_obj.weekday() not in AL_HAROKER_AVAILABLE_WEEKDAYS:
        return jsonify({'error': 'available_sun_thu'}), 400

    with _bookings_lock:
        bookings = _load_al_haroker_bookings()
        if any(b['date'] == date_str for b in bookings):
            return jsonify({'error': 'date_taken'}), 409
        # One registration per email per month
        month_str   = date_str[:7]   # "YYYY-MM"
        email_lower = email.lower()
        if any(b['date'][:7] == month_str and b['email'].lower() == email_lower
               for b in bookings):
            return jsonify({'error': 'one_per_month'}), 409

        token   = secrets.token_urlsafe(32)
        booking = {
            'token':          token,
            'date':           date_str,
            'broadcaster':    broadcaster,
            'email':          email,
            'registered_at':  datetime.now().isoformat(),
            'uploaded':       False,
            'upload_at':      None,
            'show_id':        None,
        }
        bookings.append(booking)
        _save_al_haroker_bookings(bookings)

    # Save to subscriber list (fire-and-forget)
    threading.Thread(target=_add_subscriber, args=(broadcaster, email), daemon=True).start()
    # Send email in background
    threading.Thread(target=_send_upload_email, args=(booking,), daemon=True).start()

    return jsonify({'ok': True})


@app.route('/al-haroker-upload/<token>')
def al_haroker_upload_page(token):
    bookings = _load_al_haroker_bookings()
    booking  = next((b for b in bookings if b['token'] == token), None)
    if not booking:
        return render_template('al_haroker_upload.html',
                               invalid=True, booking=None, date_obj=None,
                               already_uploaded=False, heb_days=_HEB_DAYS, heb_months=_HEB_MONTHS)
    date_obj     = datetime.strptime(booking['date'], '%Y-%m-%d')
    broadcast_dt = date_obj.replace(hour=AL_HAROKER_BROADCAST_HOUR, minute=0, second=0)
    too_early    = (broadcast_dt - datetime.now()) > timedelta(days=61)
    days_until_open = max(0, (broadcast_dt - datetime.now()).days - 61) if too_early else 0
    return render_template('al_haroker_upload.html',
                           invalid=False,
                           booking=booking,
                           date_obj=date_obj,
                           already_uploaded=booking.get('uploaded', False),
                           too_early=too_early,
                           days_until_open=days_until_open,
                           heb_days=_HEB_DAYS,
                           heb_months=_HEB_MONTHS)


@app.route('/api/al-haroker-upload/<token>', methods=['POST'])
def api_al_haroker_upload(token):
    # Validate token + not-yet-uploaded under lock
    with _bookings_lock:
        bookings = _load_al_haroker_bookings()
        booking  = next((b for b in bookings if b['token'] == token), None)
        if not booking:
            return jsonify({'error': 'invalid_token'}), 404
        if booking.get('uploaded'):
            return jsonify({'error': 'already_uploaded'}), 409

    audio_file  = request.files.get('file')
    description = request.form.get('description', '').strip()
    if not audio_file or not audio_file.filename:
        return jsonify({'error': 'קובץ אודיו נדרש'}), 400

    # Build broadcast datetimes
    broadcast_dt = datetime.strptime(booking['date'], '%Y-%m-%d').replace(
        hour=AL_HAROKER_BROADCAST_HOUR, minute=0, second=0, microsecond=0)
    upload_dt = broadcast_dt.replace(hour=AL_HAROKER_UPLOAD_HOUR)

    # 2-month upload cap: accept files only within 61 days of broadcast
    if (broadcast_dt - datetime.now()) > timedelta(days=61):
        days_left = (broadcast_dt - datetime.now()).days - 61
        return jsonify({'error': f'ניתן להעלות את הפרק עד 61 יום לפני השידור. נסה שוב בעוד {days_left} ימים.'}), 400

    # Save file locally
    show_id   = str(int(time.time() * 1000))
    safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                        for c in os.path.basename(audio_file.filename))
    filename   = f"{int(time.time())}_{safe_name}"
    local_path = os.path.join(LOCAL_TEMP, filename)
    nas_path   = os.path.join(NAS_TEMP, filename)
    audio_file.save(local_path)

    show = {
        'id':              show_id,
        'name':            'על הרוקר',
        'show_key':        'al_harocker',
        'broadcaster':     booking['broadcaster'],
        'mode':            'queue_to_broadcast',
        'episode_num':     '',
        'description':     description,
        'scheduled_time':  broadcast_dt.isoformat(),
        'upload_time':     upload_dt.isoformat(),
        'rerun_time':      None,
        'file_path':       local_path,
        'nas_path':        nas_path,
        'nas_ready':       False,
        'albums':          None,
        'files':           None,
        'original_name':   audio_file.filename,
        'triggered':       False,
        'rerun_scheduled': False,
        'upload_done':     False,
        'is_rerun':        False,
        'added_at':        datetime.now().isoformat(),
    }

    # Add to schedule
    with _schedule_lock:
        schedule = load_schedule()
        schedule.append(show)
        save_schedule(schedule)

    # Mark booking as uploaded
    with _bookings_lock:
        bookings = _load_al_haroker_bookings()
        for b in bookings:
            if b['token'] == token:
                b['uploaded']  = True
                b['upload_at'] = datetime.now().isoformat()
                b['show_id']   = show_id
                break
        _save_al_haroker_bookings(bookings)

    # Move to NAS + kick off Podbean/WP upload thread
    threading.Thread(target=_move_to_nas, args=(show_id, local_path, nas_path), daemon=True).start()
    with _schedule_lock:
        schedule = load_schedule()
        for s in schedule:
            if s['id'] == show_id:
                s['upload_in_progress'] = True
                break
        save_schedule(schedule)
    threading.Thread(target=_upload_and_mark_done, args=(show_id,), daemon=True).start()
    threading.Thread(target=_sync_wp_board, daemon=True).start()

    print(f"[AlHaRoker] File uploaded by {booking['broadcaster']} for {booking['date']}", flush=True)
    return jsonify({'ok': True})


@app.route('/api/al-haroker-booking/<token>', methods=['DELETE'])
def api_al_haroker_delete_booking(token):
    """Admin: remove an al-haroker booking. Also removes the show from schedule if already uploaded."""
    with _bookings_lock:
        bookings = _load_al_haroker_bookings()
        booking  = next((b for b in bookings if b['token'] == token), None)
        if not booking:
            return jsonify({'error': 'not found'}), 404
        show_id = booking.get('show_id')
        bookings = [b for b in bookings if b['token'] != token]
        _save_al_haroker_bookings(bookings)

    # If a file was already uploaded, also pull the show from schedule
    if show_id:
        with _schedule_lock:
            schedule = load_schedule()
            schedule = [s for s in schedule if s.get('id') != show_id]
            save_schedule(schedule)
        threading.Thread(target=_sync_wp_board, daemon=True).start()

    return jsonify({'ok': True})


@app.route('/api/al-haroker-booking/<token>/reschedule', methods=['POST'])
def api_al_haroker_reschedule_booking(token):
    """Admin: move a pending (not-yet-uploaded) booking to a different date."""
    data     = request.get_json(force=True) or {}
    new_date = data.get('date', '').strip()

    if not new_date:
        return jsonify({'error': 'date required'}), 400
    try:
        date_obj = datetime.strptime(new_date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'invalid date format'}), 400

    if date_obj < datetime.now().date():
        return jsonify({'error': 'date_past'}), 400
    if date_obj.weekday() not in AL_HAROKER_AVAILABLE_WEEKDAYS:
        return jsonify({'error': 'available_sun_thu'}), 400

    with _bookings_lock:
        bookings = _load_al_haroker_bookings()
        booking  = next((b for b in bookings if b['token'] == token), None)
        if not booking:
            return jsonify({'error': 'not found'}), 404
        if booking.get('uploaded'):
            return jsonify({'error': 'already_uploaded'}), 409
        if any(b['date'] == new_date and b['token'] != token for b in bookings):
            return jsonify({'error': 'date_taken'}), 409

        old_date       = booking['date']
        booking['date'] = new_date
        _save_al_haroker_bookings(bookings)

    print(f"[AlHaRoker] Rescheduled {booking['broadcaster']} from {old_date} → {new_date}", flush=True)
    return jsonify({'ok': True, 'old_date': old_date, 'new_date': new_date})


@app.route('/api/al-haroker-subscribers')
def api_al_haroker_subscribers():
    """Admin: list all subscribers (for sending monthly invites)."""
    try:
        with open(AL_HAROKER_SUBSCRIBERS_FILE) as f:
            subs = json.load(f)
    except Exception:
        subs = []
    return jsonify(subs)


# ─── Al HaRoker — monthly invite emails ───────────────────────────────────────

def _send_monthly_invite_email(subscriber, next_year, next_month):
    """Send a single monthly invite email to one subscriber."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[AlHaRoker] SMTP not configured — skipping invite to {subscriber['email']}", flush=True)
        return
    try:
        month_name = _HEB_MONTHS[next_month]
        link = f"{ZEROCK_PUBLIC_URL}/al-haroker-schedule/{next_year}/{next_month}"

        body_plain = (
            f"היי רוקרים ורוקריות,\n\n"
            f"מוזמנים להרשם לעריכת על הרוקר ב\u05f4רדיו זה רוק\u05f4!!!\n"
            f"הנה הלינק:\n{link}\n\n"
            f"בגלל עומס הבקשות, המערכת מאפשרת רישום אחד כל חודש.\n\n"
            f"Keep on Rockin' !!!\n"
            f"צוות רדיו זה רוק"
        )
        body_html = (
            '<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;'
            'color:#222;line-height:1.8">'
            '<p>היי רוקרים ורוקריות,</p>'
            f'<p>מוזמנים להרשם לעריכת על הרוקר ב<strong>״רדיו זה רוק״</strong>!!!</p>'
            '<p>הנה הלינק:</p>'
            f'<p style="margin:22px 0">'
            f'<a href="{link}" style="background:#e63946;color:#fff;padding:13px 26px;'
            f'text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px">'
            f'🎙️ הרשמה לחודש {month_name}'
            '</a></p>'
            f'<p style="color:#888;font-size:13px">'
            f'לא עובד הכפתור? העתק לדפדפן:<br>'
            f'<a href="{link}" style="color:#e63946">{link}</a></p>'
            '<hr style="border:none;border-top:1px solid #ddd;margin:20px 0">'
            '<p>בגלל עומס הבקשות, המערכת מאפשרת רישום אחד כל חודש.</p>'
            '<p>Keep on Rockin\' !!!<br><strong>צוות רדיו זה רוק</strong></p>'
            '</div>'
        )

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"עריכת על הרוקר לחודש {month_name}"
        msg['From']    = SMTP_FROM_ADDR
        msg['To']      = subscriber['email']
        msg.attach(MIMEText(body_plain, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html,  'html',  'utf-8'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM_ADDR, [subscriber['email']], msg.as_bytes())
        print(f"[AlHaRoker] Monthly invite → {subscriber['email']}", flush=True)
    except Exception as e:
        print(f"[AlHaRoker] Monthly invite error for {subscriber['email']}: {e}", flush=True)


def _do_send_monthly_invites(next_year, next_month):
    """Load subscriber list and send monthly invites to everyone."""
    try:
        with open(AL_HAROKER_SUBSCRIBERS_FILE) as f:
            subs = json.load(f)
    except Exception:
        subs = []
    if not subs:
        print("[AlHaRoker] No subscribers to notify", flush=True)
        return
    print(f"[AlHaRoker] Sending monthly invites for {_HEB_MONTHS[next_month]} "
          f"{next_year} to {len(subs)} subscribers", flush=True)
    for sub in subs:
        _send_monthly_invite_email(sub, next_year, next_month)
        time.sleep(1)   # avoid SMTP rate limits


def _start_monthly_invite_sender():
    """Background daemon: 3 days before month end, email all subscribers for next month."""
    def _run():
        while True:
            try:
                now = datetime.now()
                # Calculate last day of current month
                if now.month == 12:
                    last_day = (datetime(now.year + 1, 1, 1) - timedelta(days=1)).date()
                else:
                    last_day = (datetime(now.year, now.month + 1, 1) - timedelta(days=1)).date()

                days_remaining = (last_day - now.date()).days  # 0 = last day of month

                if days_remaining == 2:   # trigger on 3rd-to-last day of month
                    if now.month == 12:
                        next_year, next_month = now.year + 1, 1
                    else:
                        next_year, next_month = now.year, now.month + 1

                    # Guard: only send once per calendar month
                    sent_key = f"{now.year}-{now.month:02d}"
                    try:
                        with open(AL_HAROKER_MONTHLY_SENT_FILE) as f:
                            sent_data = json.load(f)
                    except Exception:
                        sent_data = {}

                    if sent_data.get('last_sent_key') != sent_key:
                        print(f"[AlHaRoker] Monthly invite triggered (3 days to month end) "
                              f"for {next_year}/{next_month}", flush=True)
                        _do_send_monthly_invites(next_year, next_month)
                        try:
                            with open(AL_HAROKER_MONTHLY_SENT_FILE, 'w') as f:
                                json.dump({'last_sent_key': sent_key,
                                           'sent_at': now.isoformat(),
                                           'sent_for': f"{next_year}-{next_month:02d}"}, f)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[AlHaRoker] Monthly invite thread error: {e}", flush=True)

            # Sleep until ~10:00 AM tomorrow to re-check
            now2 = datetime.now()
            tomorrow_10 = (now2 + timedelta(days=1)).replace(
                hour=10, minute=0, second=0, microsecond=0)
            time.sleep(max((tomorrow_10 - now2).total_seconds(), 3600))

    threading.Thread(target=_run, daemon=True).start()


_start_monthly_invite_sender()


@app.route('/api/al-haroker-send-invites', methods=['POST'])
def api_al_haroker_send_invites():
    """Admin: manually trigger the monthly invite send for a given next month.
    Body (JSON, optional): {"year": 2026, "month": 6}"""
    data = request.get_json(force=True) or {}
    now  = datetime.now()
    if now.month == 12:
        def_year, def_month = now.year + 1, 1
    else:
        def_year, def_month = now.year, now.month + 1
    next_year  = int(data.get('year',  def_year))
    next_month = int(data.get('month', def_month))
    threading.Thread(target=_do_send_monthly_invites,
                     args=(next_year, next_month), daemon=True).start()
    return jsonify({'ok': True, 'sending_for': f"{next_year}/{next_month}",
                    'month_name': _HEB_MONTHS[next_month]})


if __name__ == '__main__':
    print("ZeRock Radio web interface starting on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
