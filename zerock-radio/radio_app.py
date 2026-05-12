#!/usr/bin/env python3
"""
ZeRock Radio — Web interface & show scheduler
Runs on port 5000. Communicates with Liquidsoap via telnet on port 1234.
"""

import os, glob, json, random, re, socket, threading, time, shutil, hashlib, secrets, calendar as _calendar, subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests as _requests
from datetime import datetime, timedelta, date as _date
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, Response

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
YOM_KIPPUR_FILE  = f"{RADIO_DIR}/yom_kippur_schedule.json"
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
    'Beat-oN מקומי':          14326,
    'Black Parade':            375,
    'ON AIR':                  10872,
    'On the Mend':             14312,
    'Oy Vavoy':                8064,
    'RockTrip':                14447,
    'Shabi On The Rocks':      563,
    'Shabi on the Rocks':      563,
    'Stage Dive':              12842,
    'The Breakdown':           8062,
    'Time Warp':               12266,
    'אני לא בפסקול':          461,
    'האחות':                   12987,
    'השאלטר':                  10875,
    'זה פרוג':                 2085,
    'זה רוק פורטה':            4382,
    'מצעד הרוק של ישראל':     389,
    'נגד כיוון הזיפים':       450,
    'סינגלס':                  374,
    'סן פטרוק':                389,
    'על הרוקר':                769,
    'פטרוק לילה':              388,
}
# WP shows taxonomy term IDs (from /wp-json/wp/v2/shows)
_WP_SHOW_IDS = {
    'Beat-oN מקומי':          317,
    'Black Parade':            49,
    'ON AIR':                  305,
    'On the Mend':             316,
    'Oy Vavoy':                71,
    'RockTrip':                318,
    'Shabi On The Rocks':      53,
    'Shabi on the Rocks':      53,
    'Stage Dive':              313,
    'The Breakdown':           253,
    'Time Warp':               308,
    'אני לא בפסקול':          43,
    'האחות':                   314,
    'השאלטר':                  306,
    'זה פרוג':                 149,
    'זה רוק פורטה':            45,
    'מצעד הרוק של ישראל':     58,
    'נגד כיוון הזיפים':       42,
    'סינגלס':                  48,
    'סן פטרוק':                44,
    'ערב של אלבומים':          60,
    'על הרוקר':                38,
    'פטרוק לילה':              50,
}

# Podbean API credentials (for fetching CDN media_url when WP post needs to be created)
PODBEAN_CLIENT_ID     = os.environ.get('PODBEAN_CLIENT_ID', '')
PODBEAN_CLIENT_SECRET = os.environ.get('PODBEAN_CLIENT_SECRET', '')

# Spotify Client Credentials — used to resolve poll songs to direct track URLs
SPOTIFY_CLIENT_ID     = os.environ.get('SPOTIFY_CLIENT_ID', '6b6d99c885f543f9b383bd5994720cc1')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '7099b2bcbb3e44e4a0cb8cb2dba7dbab')
# Spotify OAuth refresh token — needed for playlist modification (user scope)
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN', '')
# Weekly poll Spotify playlist IDs to update on renewal (owned by Roy Kuperman)
SPOTIFY_PALASH_PLAYLIST  = '5pzM2G3wfgBAoU4V7uwrai'
SPOTIFY_TOP20_PLAYLIST   = '5C6daDPpvGaaqNF2CkfCxf'

# ─── Al HaRoker self-service scheduling ───────────────────────────────────────
AL_HAROKER_BOOKINGS_FILE    = f"{RADIO_DIR}/al_haroker_bookings.json"
AL_HAROKER_SUBSCRIBERS_FILE = f"{RADIO_DIR}/al_haroker_subscribers.json"
ONE_TIME_LINKS_FILE         = f"{RADIO_DIR}/one_time_links.json"
POLLS_FILE                  = f"{RADIO_DIR}/polls.json"
POLL_VOTES_FILE             = f"{RADIO_DIR}/poll_votes.json"
POLL_CODES_FILE             = f"{RADIO_DIR}/poll_codes.json"
AL_HAROKER_MONTHLY_SENT_FILE= f"{RADIO_DIR}/al_haroker_monthly_sent.json"
UNSUBSCRIBE_TOKENS_FILE     = f"{RADIO_DIR}/unsubscribe_tokens.json"
UNSUBSCRIBED_EMAILS_FILE    = f"{RADIO_DIR}/unsubscribed_emails.json"
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
# Shows that must NEVER be uploaded to Podbean or WordPress, regardless of submitted mode.
# This is a hard override applied at every layer: upload ingestion, trigger, and WP publish.
NEVER_UPLOAD_SHOWS = {'erev_albumim'}
# Auth token for zerock uploader API (SHA-256 of the login password)
_UPLOADER_AUTH = hashlib.sha256(b'YudaKaka2026!').hexdigest()

app = Flask(__name__, template_folder=f"{RADIO_DIR}/templates")
app.config['TEMPLATES_AUTO_RELOAD'] = True   # pick up template changes without restart
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
    {'key': 'patrock_laila_eyal',   'name': 'פטרוק לילה',         'broadcaster': 'איל אורטל',    'day': 6,    'time': '21:00', 'upload_time': '22:00', 'rerun_days_offset': 6,    'rerun_time': '09:00','wp_show_id': ''},
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
    {'key': 'matzad_harok',         'name': 'מצעד הרוק של ישראל', 'broadcaster': '',              'day': 3,    'time': '13:00', 'upload_time': '15:00', 'rerun_days_offset': 1,    'rerun_time': '10:00','wp_show_id': '', 'no_wp': True},
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

def _upload_time_reached(show, show_cfg=None) -> bool:
    """Return True if the upload_time for this show has passed (safe to upload now).
    If no upload_time restriction, always returns True."""
    if show_cfg is None:
        show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')), None)
    if not show_cfg or not show_cfg.get('upload_time'):
        return True
    try:
        broadcast_dt = datetime.fromisoformat(show['scheduled_time'])
        upload_dt    = _calc_upload_dt(broadcast_dt, show_cfg)
        return datetime.now() >= upload_dt
    except Exception:
        return True   # on parse error, don't block

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
                                # Also override display title/artist so _np_cache
                                # shows the show name, not the file's ID3 tags.
                                title  = log_title
                                artist = log_artist
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


def _get_latest_podbean_episode_for_show(show_name: str) -> tuple:
    """Search Podbean for the most recent episode whose title contains show_name.
    Searches up to 300 episodes with NO recency filter — so old episodes are found too.
    Returns (media_url, episode_title) or (None, None) if not found."""
    token = _get_podbean_access_token()
    if not token:
        return None, None
    name_lower = show_name.strip().lower()
    try:
        for offset in range(0, 300, 20):
            resp = _requests.get(
                'https://api.podbean.com/v1/episodes',
                params={'access_token': token, 'limit': 20, 'offset': offset},
                timeout=15
            )
            episodes = resp.json().get('episodes', [])
            if not episodes:
                break
            for ep in episodes:
                title = ep.get('title', '')
                if name_lower in title.lower():
                    media_url = ep.get('media_url')
                    if media_url:
                        print(f"[Podbean] Found episode for '{show_name}': {title}", flush=True)
                        return media_url, title
    except Exception as e:
        print(f"[Podbean] episode lookup error for '{show_name}': {e}", flush=True)
    return None, None


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
        if show_cfg.get('no_wp'):
            print(f"[WP-Direct] Skipping WP post for '{show_cfg['name']}' (no_wp=True)", flush=True)
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
            wp_post_status = data.get('status', '')
            print(f"[WP-Direct] Created post {wp_id} → {link} (status={wp_post_status})", flush=True)
            # If the post was created as 'future', mark the schedule entry so
            # _check_wp_posts can flip it to 'publish' once air time passes.
            if wp_post_status == 'future':
                try:
                    with _schedule_lock:
                        sched = load_schedule()
                        for _s in sched:
                            if _s.get('id') == show.get('id'):
                                _s['wp_future_pending'] = True
                                break
                        save_schedule(sched)
                except Exception as _fe:
                    print(f"[WP-Direct] Could not set wp_future_pending: {_fe}", flush=True)
            # ── Post-creation verification (60s delay to let WP settle) ────────
            _v_wp_id   = wp_id
            _v_name    = show_name
            _v_bcast   = broadcast_dt
            _v_podbean = podbean_permalink or ''
            def _run_direct_verify():
                time.sleep(60)
                _verify_and_fix_wp_post(_v_wp_id, _v_name, _v_bcast, _v_podbean)
            threading.Thread(target=_run_direct_verify, daemon=True).start()
            return True, wp_id
        else:
            print(f"[WP-Direct] HTTP {resp.status_code}: {resp.text[:300]}", flush=True)
            return False, None
    except Exception as e:
        print(f"[WP-Direct] Error: {e}", flush=True)
        return False, None


def _verify_and_fix_wp_post(wp_post_id: int, show_name: str, broadcast_dt, podbean_url: str = '') -> bool:
    """Check a WP episode post for missing required fields and auto-fix what we can.
    Returns True if all fields OK (or fixed), False if anything remains broken."""
    import base64 as _b64_v
    if not WP_USERNAME or not WP_APP_PASS or not wp_post_id:
        return False
    # Normalise show_name: if it carries a broadcaster suffix ("ShowName — Broadcaster")
    # strip it so _WP_SHOW_IDS / _WP_FEATURED_IMAGES lookups work correctly.
    _canon_name = show_name
    if ' — ' in show_name or ' - ' in show_name:
        _sep = ' — ' if ' — ' in show_name else ' - '
        _base = show_name.split(_sep)[0].strip()
        if _base in _WP_SHOW_IDS or _base in _WP_FEATURED_IMAGES:
            _canon_name = _base
    show_name = _canon_name   # use canonical name for all lookups below
    # Skip WP verification for shows that should never have a WP post
    _scfg_v = next((s for s in SHOW_SCHEDULE if s['name'] == show_name), None)
    if _scfg_v and _scfg_v.get('no_wp'):
        print(f"[WP-Verify] Skipping verification for '{show_name}' (no_wp=True)", flush=True)
        return True
    try:
        _creds = _b64_v.b64encode(f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
        _hdrs  = {'Authorization': f'Basic {_creds}', 'Content-Type': 'application/json'}

        # ── 1. Fetch current post state ──────────────────────────────────────────
        resp = _requests.get(f"{WP_URL}/wp-json/wp/v2/episodes/{wp_post_id}",
                             headers=_hdrs, timeout=15)
        if resp.status_code != 200:
            print(f"[WP-Verify] Could not fetch post {wp_post_id}: HTTP {resp.status_code}", flush=True)
            return False
        post = resp.json()
        acf  = post.get('acf', {}) or {}

        fixes = {}   # accumulated PATCH payload
        issues = []  # human-readable list for logging

        # ── 2. status: must be 'publish' once broadcast time has passed ──────────
        if post.get('status') == 'future' and broadcast_dt and broadcast_dt <= datetime.now():
            fixes['status'] = 'publish'
            issues.append('status=future→publish')

        # ── 3. featured_media ────────────────────────────────────────────────────
        if not post.get('featured_media'):
            fi = _WP_FEATURED_IMAGES.get(show_name)
            if fi:
                fixes['featured_media'] = fi
                issues.append(f'featured_media={fi}')

        # ── 4. shows taxonomy ────────────────────────────────────────────────────
        if not post.get('shows'):
            sid = _WP_SHOW_IDS.get(show_name)
            if sid:
                fixes['shows'] = [sid]
                issues.append(f'shows=[{sid}]')

        # ── 5. ACF date ──────────────────────────────────────────────────────────
        if not acf.get('date') and broadcast_dt:
            if 'acf' not in fixes:
                fixes['acf'] = {}
            fixes['acf']['date'] = broadcast_dt.strftime('%Y%m%d')
            issues.append(f'date={broadcast_dt.strftime("%Y%m%d")}')

        # ── 6. podbean_link ──────────────────────────────────────────────────────
        if not acf.get('podbean_link'):
            media_url = None
            # Try resolving from the passed podbean permalink first
            if podbean_url and podbean_url.startswith('http'):
                media_url = _get_podbean_media_url(podbean_url) if 'podbean.com' in podbean_url else None
                if not media_url and podbean_url.startswith('https://mcdn.podbean.com'):
                    media_url = podbean_url   # already a CDN URL
            # Fallback: search Podbean API by show name
            if not media_url:
                media_url, _ep_title = _get_latest_podbean_episode_for_show(show_name)
            if media_url:
                if 'acf' not in fixes:
                    fixes['acf'] = {}
                fixes['acf']['podbean_link'] = media_url
                issues.append(f'podbean_link set')
            else:
                issues.append('podbean_link MISSING (Podbean lookup failed)')

        if not fixes:
            print(f"[WP-Verify] Post {wp_post_id} ({show_name}): all fields OK ✓", flush=True)
            return True

        # ── 7. Apply fixes in one PATCH ──────────────────────────────────────────
        patch = _requests.post(f"{WP_URL}/wp-json/wp/v2/episodes/{wp_post_id}",
                               json=fixes, headers=_hdrs, timeout=20)
        if patch.status_code in (200, 201):
            print(f"[WP-Verify] Post {wp_post_id} ({show_name}): fixed [{', '.join(issues)}] ✓", flush=True)
            return 'podbean_link MISSING' not in ' '.join(issues)
        else:
            print(f"[WP-Verify] Post {wp_post_id}: PATCH failed HTTP {patch.status_code}: {patch.text[:200]}", flush=True)
            return False
    except Exception as e:
        print(f"[WP-Verify] Post {wp_post_id} error: {e}", flush=True)
        return False


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
    # Always publish immediately at upload time — never create WP/Podbean posts as
    # 'future'. Using broadcast_dt in the past caused WP to create posts in 'future'
    # status (since broadcast_dt > now), which then clears the shows taxonomy on flip.
    publish_ts = str(int(datetime.now().timestamp()))
    # ── Convert WAV → MP3 before upload to avoid huge files timing out ──────────
    upload_path   = file_path
    upload_name   = show.get('original_name', 'show.mp3')
    _tmp_mp3      = None
    if file_path.lower().endswith('.wav'):
        _tmp_mp3 = file_path[:-4] + '_upload.mp3'
        try:
            print(f"[Upload] Converting WAV→MP3 for upload: {os.path.basename(file_path)}", flush=True)
            _conv = subprocess.run(
                ['ffmpeg', '-y', '-i', file_path, '-codec:a', 'libmp3lame',
                 '-qscale:a', '2', '-ar', '44100', '-ac', '2', _tmp_mp3],
                capture_output=True, timeout=600
            )
            if _conv.returncode == 0 and os.path.exists(_tmp_mp3):
                upload_path = _tmp_mp3
                upload_name = os.path.splitext(upload_name)[0] + '.mp3'
                print(f"[Upload] Converted to MP3: {os.path.getsize(_tmp_mp3)//1024//1024}MB", flush=True)
            else:
                print(f"[Upload] WAV→MP3 conversion failed, using original WAV", flush=True)
                _tmp_mp3 = None
        except Exception as _ce:
            print(f"[Upload] WAV→MP3 conversion error: {_ce} — using original WAV", flush=True)
            _tmp_mp3 = None
    try:
        with open(upload_path, 'rb') as f:
            files  = {'audioFile': (upload_name, f, 'audio/mpeg')}
            data   = {
                'showName':        show_cfg['name'],
                'broadcaster':     broadcaster,
                'date':            broadcast_dt.strftime('%Y-%m-%d'),
                'scheduleTime':    broadcast_dt.strftime('%H:%M'),
                'episodeNumber':   show.get('episode_num', ''),
                'playlist':        show.get('description', ''),
                'publishTimestamp': publish_ts,
                'wpShowId':        str(_WP_SHOW_IDS.get(show_cfg['name'], '')) or show_cfg.get('wp_show_id', ''),
                'wpBroadcasterId': '',
                'scheduleToSam':   '0',
                'samOnly':         '0',
                'skipWP':          '1' if (show.get('skip_wp') or show_cfg.get('no_wp')) else '0',
            }
            resp = _requests.post(
                UPLOADER_URL, files=files, data=data, timeout=600, stream=True,
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
    finally:
        # Clean up temp MP3 if we created one
        if _tmp_mp3 and os.path.exists(_tmp_mp3):
            try:
                os.remove(_tmp_mp3)
            except Exception:
                pass

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
                        # If the upload happened before the broadcast time, mark it so
                        # _check_wp_posts can flip it to 'publish' once air time passes.
                        # If uploaded >3h before air time, also immediately set the WP post
                        # to 'draft' so it doesn't appear on the public site prematurely.
                        try:
                            _bcast_dt = datetime.fromisoformat(s.get('scheduled_time', ''))
                            if _bcast_dt > datetime.now():
                                s['wp_future_pending'] = True
                            if _bcast_dt > datetime.now() + timedelta(hours=3):
                                s['wp_draft_pending'] = True
                                _draft_wp_id = wp_post_id
                                def _set_draft_status(_wid=_draft_wp_id):
                                    import base64 as _b64d
                                    _c = _b64d.b64encode(
                                        f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
                                    _h = {'Authorization': f'Basic {_c}',
                                          'Content-Type': 'application/json'}
                                    try:
                                        _r = _requests.post(
                                            f"{WP_URL}/wp-json/wp/v2/episodes/{_wid}",
                                            json={'status': 'draft'}, headers=_h, timeout=15)
                                        print(f"[Upload] Set post {_wid} to draft: "
                                              f"HTTP {_r.status_code}", flush=True)
                                    except Exception as _de:
                                        print(f"[Upload] Draft-set error {_wid}: {_de}",
                                              flush=True)
                                threading.Thread(target=_set_draft_status, daemon=True).start()
                        except Exception:
                            _bcast_dt = None
                        # ── Verify & auto-fix WP post fields (60s delay to let WP settle) ──
                        # Use canonical show name from SHOW_SCHEDULE (not schedule entry name
                        # which may include broadcaster suffix e.g. "פטרוק לילה — אלירן קטנוב")
                        _scfg_v = next((sc for sc in SHOW_SCHEDULE if sc['key'] == s.get('show_key')), None)
                        _verify_show_name = _scfg_v['name'] if _scfg_v else s.get('name', '')
                        _verify_podbean   = podbean_url or ''
                        _verify_bcast     = _bcast_dt
                        _verify_wp_id     = wp_post_id
                        def _run_verify():
                            time.sleep(60)
                            _verify_and_fix_wp_post(_verify_wp_id, _verify_show_name,
                                                    _verify_bcast, _verify_podbean)
                        threading.Thread(target=_run_verify, daemon=True).start()
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
    """Explicitly publish a WP episode post to 'publish' at air time, bypassing wp-cron.
    Calls the WP REST API directly — does NOT go through the uploader server.
    If no wp_post_id exists, falls back to creating a new post directly via REST API."""
    import base64 as _b64
    try:
        if wp_post_id:
            # Call WP REST API directly to flip status to 'publish'
            _creds = _b64.b64encode(f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
            _hdrs  = {'Authorization': f'Basic {_creds}', 'Content-Type': 'application/json'}
            resp = _requests.post(
                f"{WP_URL}/wp-json/wp/v2/episodes/{wp_post_id}",
                json={'status': 'publish'}, headers=_hdrs, timeout=30
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                print(f"[WP] Published post {wp_post_id} → {data.get('link','')} "
                      f"(status={data.get('status')})", flush=True)
                # Clear wp_future_pending flag if set
                with _schedule_lock:
                    sched = load_schedule()
                    for s in sched:
                        if s.get('id') == show_id:
                            s.pop('wp_future_pending', None)
                            break
                    save_schedule(sched)
            else:
                print(f"[WP] Publish returned {resp.status_code}: {resp.text[:200]}", flush=True)
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

def load_yom_kippur_schedule():
    """Return {'from': iso|None, 'until': iso|None}."""
    try:
        if os.path.exists(YOM_KIPPUR_FILE):
            with open(YOM_KIPPUR_FILE) as f:
                data = json.load(f) or {}
                return {'from': data.get('from'), 'until': data.get('until')}
    except Exception:
        pass
    return {'from': None, 'until': None}

def save_yom_kippur_schedule(data):
    with open(YOM_KIPPUR_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False)

def is_yom_kippur_window():
    s = load_yom_kippur_schedule()
    if not s.get('from') or not s.get('until'):
        return False
    try:
        now = datetime.now()
        return datetime.fromisoformat(s['from']) <= now <= datetime.fromisoformat(s['until'])
    except Exception:
        return False

_yom_kippur_lq_state = None   # last applied state (True=window active, both stopped)

def _sync_yom_kippur_to_streams():
    """When Yom Kippur window opens/closes, stop/start both streams.

    Behaviour per user spec:
      • At `from`  → stop  broadcast on local + external streams.
      • At `until` → start broadcast on local + external streams.
    Only fires on edge transitions to avoid stream-state thrashing.
    """
    global _yom_kippur_lq_state
    in_window = is_yom_kippur_window()
    if in_window == _yom_kippur_lq_state:
        return
    # First call after boot: just seed state, no transition (don't surprise the
    # user by toggling streams when nothing changed since last run).
    if _yom_kippur_lq_state is None:
        _yom_kippur_lq_state = in_window
        return
    try:
        if in_window:
            # Enter window — stop both streams
            lq_send(['var.set local_active = false', 'var.set ext_active = false'])
            _save_stream_states(False, False)
            print('[YomKippur] Window entered — both streams stopped', flush=True)
        else:
            # Exit window — start both streams
            lq_send(['var.set local_active = true', 'var.set ext_active = true'])
            _save_stream_states(True, True)
            print('[YomKippur] Window exited — both streams started', flush=True)
        _yom_kippur_lq_state = in_window
    except Exception as e:
        print(f'[YomKippur] Sync error: {e}', flush=True)

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
        # Search Podbean directly for the latest episode of this show.
        # No recency filter — searches up to 300 episodes so even older episodes are found.
        media_url, ep_title = _get_latest_podbean_episode_for_show(show_name)
        if not media_url:
            raise Exception(f"No Podbean episodes found for '{show_name}'")
        ep_title = ep_title or show_name

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


_last_wp_check        = 0.0   # epoch time of last WP post verification run
_last_board_sync      = 0.0   # epoch time of last periodic WP board sync

def _safe_dt(iso_str):
    """Parse an ISO datetime string, returning None on failure."""
    try:
        return datetime.fromisoformat(iso_str) if iso_str else None
    except Exception:
        return None

def _check_wp_posts(schedule):
    """Scan schedule for shows whose Podbean upload succeeded but WP post is missing,
    and for shows whose WP post was created with status='future' but air time has passed.
    Runs at most every 30 minutes (throttled by _last_wp_check).

    Candidates for missing WP: queue_to_broadcast, not a rerun, upload_done, wp_post_missing,
                                not already abandoned (upload_failed + 3 attempts).
    Candidates for future→publish: any entry with wp_future_pending=True and scheduled_time
                                   at least 10 minutes in the past."""
    global _last_wp_check
    import base64 as _b64
    now = time.time()
    if now - _last_wp_check < 1800:   # 30-minute throttle
        return
    _last_wp_check = now   # set upfront so we don't spam on errors

    changed = False

    # ── Pass 1: flip WP 'future' posts to 'publish' ──────────────────────────
    # Scan ALL recent queue_to_broadcast episodes that have a wp_post_id and
    # aired in the past 3 days — no flag needed.  Also catches episodes created
    # before wp_future_pending tracking was introduced.
    cutoff_past  = datetime.now() - timedelta(days=3)
    cutoff_early = datetime.now() - timedelta(minutes=10)   # must have aired
    future_candidates = [
        s for s in schedule
        if (s.get('wp_post_id')
            and s.get('upload_done')
            and s.get('mode') == 'queue_to_broadcast'
            and not s.get('is_rerun')
            and not s.get('auto_rerun')
            and not s.get('upload_failed'))
        and _safe_dt(s.get('scheduled_time')) is not None
        and cutoff_past <= _safe_dt(s.get('scheduled_time')) <= cutoff_early
    ]
    if future_candidates:
        print(f"[WP-Check] Verifying {len(future_candidates)} recent post(s) for future→publish…", flush=True)
        _creds = _b64.b64encode(f"{WP_USERNAME}:{WP_APP_PASS}".encode()).decode()
        _hdrs = {'Authorization': f'Basic {_creds}', 'Content-Type': 'application/json'}
        for show in future_candidates:
            wp_id = show['wp_post_id']
            try:
                resp = _requests.get(
                    f"{WP_URL}/wp-json/wp/v2/episodes/{wp_id}",
                    headers=_hdrs, timeout=15
                )
                if resp.status_code != 200:
                    print(f"[WP-Check] Could not fetch post {wp_id}: HTTP {resp.status_code}", flush=True)
                    continue
                post_data = resp.json()
                wp_status = post_data.get('status', '')
                # Always build a patch that re-asserts shows + featured_media
                # in addition to the status flip — some WP hooks clear taxonomy
                # on status transitions, so we send them together.
                _scfg_chk = next((s for s in SHOW_SCHEDULE if s.get('key') == show.get('show_key')), None)
                _tax_id   = _WP_SHOW_IDS.get(_scfg_chk['name']) if _scfg_chk else None
                _feat_id  = _WP_FEATURED_IMAGES.get(_scfg_chk['name']) if _scfg_chk else None
                patch_body = {}
                if wp_status == 'future':
                    patch_body['status'] = 'publish'
                # Re-assert shows if missing or wrong
                if _tax_id and post_data.get('shows') != [_tax_id]:
                    patch_body['shows'] = [_tax_id]
                # Re-assert featured_media if missing
                if _feat_id and not post_data.get('featured_media'):
                    patch_body['featured_media'] = _feat_id
                if patch_body:
                    upd = _requests.post(
                        f"{WP_URL}/wp-json/wp/v2/episodes/{wp_id}",
                        json=patch_body, headers=_hdrs, timeout=15
                    )
                    if upd.status_code in (200, 201):
                        print(f"[WP-Check] ✓ Post {wp_id} patched {list(patch_body.keys())} for '{show.get('name')}'", flush=True)
                        if 'status' in patch_body:
                            changed = True
                    else:
                        print(f"[WP-Check] ✗ Failed to patch post {wp_id}: HTTP {upd.status_code}", flush=True)
                elif wp_status == 'future':
                    print(f"[WP-Check] Post {wp_id} still future but no patch needed", flush=True)
                    continue
                elif wp_status not in ('publish', 'future'):
                    print(f"[WP-Check] Post {wp_id} status={wp_status!r} — skipping", flush=True)
                    continue

                # Clear wp_future_pending flag if present
                if show.get('wp_future_pending'):
                    with _schedule_lock:
                        sched = load_schedule()
                        for s in sched:
                            if s.get('id') == show['id']:
                                s.pop('wp_future_pending', None)
                                break
                        save_schedule(sched)
            except Exception as e:
                print(f"[WP-Check] Error checking post {wp_id}: {e}", flush=True)

    # ── Pass 2: create missing WP posts ──────────────────────────────────────
    candidates = [
        s for s in schedule
        if (s.get('wp_post_missing')
            and s.get('upload_done')
            and not s.get('is_rerun')
            and not s.get('upload_failed')
            and s.get('mode') == 'queue_to_broadcast')
    ]
    if not candidates:
        return changed
    print(f"[WP-Check] {len(candidates)} show(s) missing WP post — retrying…", flush=True)
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

    # ── Pass 3: verify ALL recent posts for missing taxonomy/media/date/podbean ──
    # This is the safety net — catches anything missed at upload time.
    # Runs on every show with a wp_post_id that aired within the last 3 days.
    verify_candidates = [
        s for s in schedule
        if (s.get('wp_post_id')
            and s.get('upload_done')
            and s.get('mode') == 'queue_to_broadcast'
            and not s.get('is_rerun')
            and not s.get('auto_rerun')
            and not s.get('upload_failed'))
        and _safe_dt(s.get('scheduled_time')) is not None
        and cutoff_past <= _safe_dt(s.get('scheduled_time')) <= datetime.now()
    ]
    # Deduplicate by wp_post_id (multiple schedule entries may share one post)
    seen_wp_ids = set()
    for show in verify_candidates:
        wp_id = show.get('wp_post_id')
        if wp_id in seen_wp_ids:
            continue
        seen_wp_ids.add(wp_id)
        show_cfg_v = next((sc for sc in SHOW_SCHEDULE if sc['key'] == show.get('show_key')), None)
        show_name_v = show_cfg_v['name'] if show_cfg_v else show.get('name', '')
        try:
            bcast_v = datetime.fromisoformat(show['scheduled_time'])
        except Exception:
            bcast_v = None
        _verify_and_fix_wp_post(wp_id, show_name_v, bcast_v, show.get('podbean_url', ''))

    return changed


def scheduler_loop():
    """Every 15s: trigger broadcasts, auto-schedule reruns, trigger Podbean/WP uploads."""
    _lq_was_running = False
    while True:
        try:
            with _schedule_lock:
                schedule = load_schedule()
            # Snapshot the on-disk state at load time. The scheduler's iteration
            # mutates `schedule` in-memory and saves it later — but background
            # threads (AutoRerun fetch, API endpoints) can write to schedule.json
            # in the meantime. At save time we re-read disk and apply ONLY the
            # field-level deltas we actually changed (vs. this snapshot), so
            # concurrent updates aren't clobbered. See merge-save below.
            _orig_by_id = {s['id']: dict(s) for s in schedule}
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

                            # Publish WP post at air time — don't rely on wp-cron.
                            # Skip for queue_only shows, shows with no_podbean flag,
                            # and any show in NEVER_UPLOAD_SHOWS (hard block).
                            _skey_wp  = show.get('show_key', '')
                            _scfg_wp  = next((s for s in SHOW_SCHEDULE if s['key'] == _skey_wp), None)
                            _skip_wp  = (_skey_wp in NEVER_UPLOAD_SHOWS
                                         or show.get('mode') == 'queue_only'
                                         or (_scfg_wp and _scfg_wp.get('no_podbean'))
                                         or (_scfg_wp and _scfg_wp.get('no_wp')))
                            if not show.get('is_rerun') and not show.get('wp_published') and not _skip_wp:
                                wp_id   = show.get('wp_post_id')
                                sname   = show.get('show_key', '')
                                # derive broadcast date for slug fallback
                                try:
                                    bdate = datetime.fromisoformat(show['scheduled_time']).strftime('%Y-%m-%d')
                                except Exception:
                                    bdate = None
                                show_cfg_wp = next((s for s in SHOW_SCHEDULE if s['key'] == sname), None)
                                wp_show_name = show_cfg_wp['name'] if show_cfg_wp else None
                                # Only publish/create WP post at air time if one already exists
                                # (flip future→publish) or the upload is already done.
                                # If upload is still pending (time-gated), the upload thread
                                # will create the WP post — creating one here causes duplicates.
                                _upload_pending = (show.get('mode') == 'queue_to_broadcast'
                                                   and not show.get('upload_done'))
                                if wp_id or not _upload_pending:
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
                            # Respect upload_time gate — don't upload before the configured time
                            if not _upload_time_reached(show):
                                _show_cfg_ut = next((s for s in SHOW_SCHEDULE if s['key'] == show.get('show_key')), None)
                                _ut = _show_cfg_ut.get('upload_time', '?') if _show_cfg_ut else '?'
                                print(f"[Scheduler] '{show['name']}' upload held — waiting until {_ut}", flush=True)
                            else:
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
                    # Merge-save: re-read latest disk state and apply only the
                    # field-level changes scheduler made (vs. _orig_by_id snapshot).
                    # This preserves concurrent updates from background threads
                    # (e.g. AutoRerun fetch saving placeholder→ready + appending the
                    # rerun entry) that landed between our load and this save.
                    disk_sched = load_schedule()
                    disk_by_id = {s['id']: s for s in disk_sched}
                    for s in schedule:
                        sid = s['id']
                        if sid not in disk_by_id:
                            # New entry that scheduler added in-memory (e.g. to_add
                            # auto-scheduled rerun) and not yet on disk → append.
                            if sid not in _orig_by_id:
                                disk_sched.append(s)
                            # else: was on disk at load, deleted by another thread —
                            # respect that deletion, don't resurrect.
                            continue
                        orig = _orig_by_id.get(sid, {})
                        d = disk_by_id[sid]
                        for k, v in s.items():
                            if orig.get(k) != v:
                                d[k] = v
                    save_schedule(disk_sched)

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
                            _needs_podbean = (show.get('show_key') not in NEVER_UPLOAD_SHOWS
                                              and not (_up_scfg and _up_scfg.get('no_podbean')))
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
        _sync_yom_kippur_to_streams()

        # ── Periodic WP board sync (every 30 min) ─────────────────────────────
        # Ensures the schedule board self-corrects even when event-based syncs
        # are missed (e.g. after a restart, or when shows age out of the 7-day
        # window without triggering any other sync event).
        global _last_board_sync
        _now_ts = time.time()
        if _now_ts - _last_board_sync >= 1800:   # 30-minute interval
            _last_board_sync = _now_ts
            threading.Thread(target=_sync_wp_board, daemon=True).start()

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


# ── Weekly poll auto-renew (every Thursday 15:00) ─────────────────────────────
def _weekly_poll_renew_loop():
    """Every Thursday at 15:00: snapshot old poll results → renew poll → update WP vote button."""
    _already_renewed_week = [None]   # track which ISO-week we last renewed

    while True:
        now  = datetime.now()
        # Next Thursday 15:00
        days_until_thu = (3 - now.weekday()) % 7
        next_thu = (now + timedelta(days=days_until_thu)).replace(
            hour=15, minute=0, second=0, microsecond=0)
        if next_thu <= now:
            next_thu += timedelta(weeks=1)
        sleep_secs = (next_thu - now).total_seconds()
        print(f"[WeeklyPoll] Next auto-renew: {next_thu.strftime('%A %Y-%m-%d %H:%M')} "
              f"(in {sleep_secs/3600:.1f}h)", flush=True)
        time.sleep(sleep_secs)

        iso_week = datetime.now().isocalendar()[1]
        if _already_renewed_week[0] == iso_week:
            print("[WeeklyPoll] Already renewed this week — skipping", flush=True)
            time.sleep(600)
            continue

        print("[WeeklyPoll] Thursday 15:00 — auto-renewing poll…", flush=True)
        try:
            r = _requests.post(
                'http://127.0.0.1:5000/api/polls/weekly-renew',
                timeout=30
            )
            print(f"[WeeklyPoll] Renewal result: {r.status_code} {r.text[:200]}", flush=True)
            if r.status_code == 200:
                _already_renewed_week[0] = iso_week
        except Exception as e:
            print(f"[WeeklyPoll] Renewal failed: {e}", flush=True)

        time.sleep(600)   # prevent double-fire within the same hour

threading.Thread(target=_weekly_poll_renew_loop, daemon=True).start()


def _poll_close_watcher():
    """Background thread: watches for polls whose closes_at has passed and
    - sets open=False in polls.json
    - updates WP snippet #55 to show 'voting closed' text
    Checks every 60 seconds.  Safe to restart — idempotent."""
    _already_closed = set()
    while True:
        try:
            now = datetime.now()
            polls = _load_polls()
            for poll in polls:
                pid = poll.get('id')
                if pid in _already_closed:
                    continue
                if not _poll_is_open(poll, now) and poll.get('open'):
                    # Poll just crossed its close time — flip flag
                    polls2 = _load_polls()
                    changed = False
                    for p2 in polls2:
                        if p2.get('id') == pid and p2.get('open'):
                            p2['open'] = False
                            changed = True
                    if changed:
                        _save_polls(polls2)
                        print(f'[PollWatcher] Closed poll {pid}', flush=True)
                    _already_closed.add(pid)
                    # Update WP snippet to show closed state
                    threading.Thread(target=_update_wp_vote_snippet_closed, args=(pid,), daemon=True).start()
        except Exception as e:
            print(f'[PollWatcher] Error: {e}', flush=True)
        time.sleep(60)


def _update_wp_vote_snippet_closed(poll_id):
    """Update WP snippet #55 to show 'voting closed' state."""
    if not WP_USERNAME or not WP_APP_PASS:
        return
    try:
        vote_url = f'{ZEROCK_PUBLIC_URL}/poll/{poll_id}'
        php_code = f"""add_action('wp_footer', function() {{
    echo '<script>
(function(){{
    function fixChartBtn() {{
        var btn = document.querySelector("a.chart-top-button");
        if (btn) {{
            btn.href = "{vote_url}";
            btn.textContent = "ההצבעה נסגרה — התוצאות ביום חמישי";
            btn.style.opacity = "0.6";
            btn.style.pointerEvents = "none";
        }}
    }}
    fixChartBtn();
    if (window.jQuery) {{ jQuery(document).on("ajaxComplete", fixChartBtn); }}
}})();
</script>';
}});"""
        import base64 as _b64c
        creds = _b64c.b64encode(f'{WP_USERNAME}:{WP_APP_PASS}'.encode()).decode()
        hdrs  = {'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'}
        r = _requests.patch(
            f'{WP_URL}/wp-json/code-snippets/v1/snippets/55',
            json={'code': php_code, 'scope': 'front-end'},
            auth=(WP_USERNAME, WP_APP_PASS), timeout=15
        )
        print(f'[PollWatcher] WP snippet close update: {r.status_code}', flush=True)
    except Exception as e:
        print(f'[PollWatcher] WP snippet update error: {e}', flush=True)


threading.Thread(target=_poll_close_watcher, daemon=True).start()


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
                        raw_label  = label or (os.path.splitext(os.path.basename(uri))[0] if uri else '')
                        # For show files, always pull the display name from the schedule
                        # (which has the correct broadcaster per episode), never from ID3 tags
                        # which may carry stale metadata from a previous episode's file.
                        if is_show:
                            try:
                                _now_for_seek = datetime.now()
                                _cutoff_seek  = _now_for_seek - timedelta(hours=6)
                                _best_show    = None
                                for _s in _schedule:
                                    _ta = _s.get('triggered_at')
                                    if not _ta:
                                        continue
                                    try:
                                        _ta_dt = datetime.fromisoformat(_ta)
                                    except Exception:
                                        continue
                                    if _ta_dt >= _cutoff_seek and _ta_dt <= _now_for_seek:
                                        if _best_show is None or _ta_dt > datetime.fromisoformat(_best_show.get('triggered_at','')):
                                            _best_show = _s
                                if _best_show:
                                    raw_label = _best_show.get('name') or _best_show.get('show_name') or raw_label
                            except Exception:
                                pass
                        on_air_info = {
                            'label':  raw_label,
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

    # Polls for matzad admin panel
    all_polls   = _load_polls()
    active_polls = sorted(
        [{**p, 'open': _poll_is_open(p, now)} for p in all_polls],
        key=lambda p: p.get('closes_at') or p.get('opens_at') or '',
        reverse=True
    )[:10]  # show last 10 polls

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
        active_polls=active_polls,
        zerock_public_url=ZEROCK_PUBLIC_URL,
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
        "yom_kippur_active":      is_yom_kippur_window(),
        "yom_kippur_schedule":    load_yom_kippur_schedule(),
    })

# ─── Read-only log access ─────────────────────────────────────────────────────
# Token-gated tail/grep endpoint over web/liquidsoap logs so we don't have to
# rely on SSH for after-the-fact investigation. Public via Caddy at
#   https://rocky.kupernet.com/logs/?token=...
_LOGS_TOKEN = os.environ.get('LOGS_TOKEN') or hashlib.sha256(b'YudaKaka2026!').hexdigest()[:24]
_LOG_FILES = {
    'web':                f"{RADIO_DIR}/logs/web.log",
    'liquidsoap':         f"{RADIO_DIR}/logs/liquidsoap.log",
    'liquidsoap-stderr':  f"{RADIO_DIR}/logs/liquidsoap-stderr.log",
    'liquidsoap-stdout':  f"{RADIO_DIR}/logs/liquidsoap-stdout.log",
    'now-playing':        f"{RADIO_DIR}/now_playing.txt",
}

@app.route('/logs/')
@app.route('/logs/<path:logfile>')
def admin_logs(logfile=None):
    """Tail/grep recent lines from a log file. Read-only.

    Auth: ?token=<LOGS_TOKEN>  (also accepts X-Logs-Token header)
    Params:
      n     — number of trailing lines (default 500, max 50000)
      grep  — Python regex; only lines matching are kept (applied before tail)
    Without a logfile name, returns a JSON listing of available logs.
    """
    token = request.args.get('token') or request.headers.get('X-Logs-Token', '')
    if token != _LOGS_TOKEN:
        return Response('Unauthorized\n', status=401, mimetype='text/plain')

    if not logfile:
        out = []
        for k, p in _LOG_FILES.items():
            try:
                st = os.stat(p)
                out.append({
                    'name':  k,
                    'path':  p,
                    'size':  st.st_size,
                    'mtime': datetime.fromtimestamp(st.st_mtime).isoformat(),
                })
            except FileNotFoundError:
                out.append({'name': k, 'path': p, 'missing': True})
        return jsonify(out)

    p = _LOG_FILES.get(logfile)
    if not p:
        return Response(f"unknown log: {logfile}\n", status=404, mimetype='text/plain')
    if not os.path.exists(p):
        return Response(f"log not present yet: {p}\n", status=404, mimetype='text/plain')

    try:
        n = max(1, min(int(request.args.get('n', 500)), 50000))
    except ValueError:
        return Response('n must be an integer\n', status=400, mimetype='text/plain')
    grep_pat = request.args.get('grep', '')
    rx = None
    if grep_pat:
        try:
            rx = re.compile(grep_pat)
        except re.error as e:
            return Response(f"bad regex: {e}\n", status=400, mimetype='text/plain')

    # Tail efficiently: read a bounded chunk from the end. If the user filtered
    # via grep, we need a wider window because matches may be sparse — so when
    # grep is set we read up to 8 MB from the tail; otherwise n*400 bytes.
    try:
        with open(p, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            window = 8 * 1024 * 1024 if rx else max(n * 400, 65536)
            f.seek(max(0, size - window))
            data = f.read().decode('utf-8', errors='replace')
        lines = data.splitlines()
        if rx is not None:
            lines = [l for l in lines if rx.search(l)]
        lines = lines[-n:]
        return Response('\n'.join(lines) + ('\n' if lines else ''),
                        mimetype='text/plain; charset=utf-8')
    except Exception as e:
        return Response(f"error reading log: {e}\n", status=500, mimetype='text/plain')


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

@app.route('/api/yom-kippur', methods=['GET'])
def api_yom_kippur_get():
    return jsonify({
        'schedule': load_yom_kippur_schedule(),
        'active':   is_yom_kippur_window(),
    })

@app.route('/api/yom-kippur', methods=['POST'])
def api_yom_kippur_post():
    data = request.get_json() or {}
    if data.get('clear'):
        save_yom_kippur_schedule({'from': None, 'until': None})
        _sync_yom_kippur_to_streams()
        return jsonify({'ok': True})

    from_iso, until_iso = data.get('from'), data.get('until')
    if not from_iso or not until_iso:
        return jsonify({'error': 'from and until are required'}), 400
    try:
        dt_from  = datetime.fromisoformat(from_iso)
        dt_until = datetime.fromisoformat(until_iso)
    except Exception:
        return jsonify({'error': 'Invalid datetime format'}), 400
    if dt_until <= dt_from:
        return jsonify({'error': 'until must be after from'}), 400

    save_yom_kippur_schedule({'from': dt_from.isoformat(), 'until': dt_until.isoformat()})
    _sync_yom_kippur_to_streams()
    return jsonify({'ok': True, 'active': is_yom_kippur_window()})

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
    # Re-enable the source. The icecast output (declared fallible=true in
    # rocky.liq) auto-reconnects to icecast.live as soon as the source is
    # available again, reclaiming the /zerock mount.
    resp = lq_send(["var.set ext_active = true"])
    _update_stream_state('ext_active', True)
    return jsonify({"success": True, "response": resp.strip()[:200]})

@app.route('/api/stream/external/stop', methods=['POST'])
def api_stream_external_stop():
    # Mark the source unavailable. With fallible=true on the output (in
    # rocky.liq), Liquidsoap disconnects from icecast.live and releases the
    # /zerock mount so another broadcaster can take over.
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
    'erev_albumim':         'בעריכת ',
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
    queue_only_entries = {}   # show_key → LIST of {wp_day, start_h, broadcaster}  for QUEUE_ONLY_BOARD_SHOWS
    # Track the earliest (soonest) datetime seen per (key, wp_day) to deduplicate
    # episodes that land on the same weekday in different weeks within the 14-day window.
    _queue_only_day_seen = {}   # (key, wp_day) → earliest datetime
    try:
        queue = load_schedule()
        now   = datetime.now()
        for entry in sorted(queue, key=lambda e: e.get('scheduled_time', '')):
            # Allow triggered queue-only shows that are CURRENTLY ON AIR
            # (triggered within the last show-duration hours) so the WP
            # "now playing" widget reflects what's actually broadcasting.
            _is_triggered = entry.get('triggered') or entry.get('is_rerun', False)
            if _is_triggered:
                _key_t = entry.get('show_key', '')
                _dur_t = _SHOW_DURATIONS_H.get(_key_t, 1)
                try:
                    _t_t = datetime.fromisoformat(entry.get('scheduled_time', ''))
                    _on_air = (_t_t <= now) and (now < _t_t + timedelta(hours=_dur_t))
                except Exception:
                    _on_air = False
                if not (_is_triggered and _on_air and _key_t in QUEUE_ONLY_BOARD_SHOWS):
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
                    # Queue-only shows (e.g. על הרוקר) only appear if the episode
                    # falls within the CURRENT board week (Sun–Sat).  The board
                    # transitions to the next week on Saturday evening at 18:00 —
                    # before that, next week's episodes are not yet shown.
                    #
                    # days_since_sun: 0=Sun, 1=Mon, …, 6=Sat  (Python Mon=0 → (wd+1)%7)
                    _days_since_sun = (now.weekday() + 1) % 7
                    _board_week_end = (
                        now + timedelta(days=(6 - _days_since_sun))
                    ).replace(hour=23, minute=59, second=59, microsecond=999999)
                    # Saturday evening (≥18:00): extend window to cover next week too
                    if now.weekday() == 5 and now.hour >= 18:
                        _board_week_end += timedelta(days=7)
                    if t > _board_week_end:
                        continue
                    _day_key = (key, ep_wp_day)
                    # Only keep the soonest episode per (show, weekday) — prevents
                    # two consecutive weeks' episodes from overlapping on the grid.
                    if _day_key in _queue_only_day_seen:
                        continue
                    _queue_only_day_seen[_day_key] = t
                    _bc_raw = (entry.get('broadcaster', '')
                               or (show_cfg_q.get('broadcaster', '') if show_cfg_q else ''))
                    _bc_prefix = _WP_BROADCASTER_PREFIX.get(key, '')
                    queue_only_entries.setdefault(key, []).append({
                        'wp_day':      ep_wp_day,
                        'start_h':     ep_start_h,
                        'broadcaster': (_bc_prefix + _bc_raw) if _bc_raw else '',
                    })
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
    # Each show_key can have multiple pending episodes within the 14-day window
    # (e.g. על הרוקר runs on many different weekdays), so we render each one.
    for show in SHOW_SCHEDULE:
        key = show['key']
        if show['day'] is not None and key not in QUEUE_ONLY_BOARD_SHOWS:
            continue
        infos = queue_only_entries.get(key) or []
        if not infos:
            continue
        dur  = _SHOW_DURATIONS_H.get(key, 1)
        slug = _WP_SLUGS.get(key, '')
        for info in infos:
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


def _build_wp_now_playing_json():
    """Build a JSON-serializable view of the weekly schedule for the WordPress
    "Now Broadcasting" homepage widget.

    The homepage widget historically read from a hardcoded weekly schedule in
    the WP theme PHP, which drifted from Rocky's source-of-truth (zifim moved,
    cancellations, queue overrides, etc). With this option populated, the
    widget can read a single source — the same `_build_wp_schedule_slots()`
    that drives the weekly schedule grid — and pick the slot covering "now"
    plus the one immediately after.

    Shape (deliberately small + JSON-flat for easy PHP consumption):
        {
          "slots":      [ {day, start_min, end_min, name, broadcaster,
                           slug, rerun}, ... ],
          "updated_at": "2026-04-29T09:30:00",
          "tz":         "Asia/Jerusalem"
        }

    `day`         : 0=Sun..6=Sat (matches PHP's date('w'))
    `start_min`/`end_min` : minutes since 00:00 (PHP-friendly integer math)
    """
    slots_by_day = _build_wp_schedule_slots()
    flat = []
    for day, items in slots_by_day.items():
        for s in items:
            flat.append({
                'day':         day,
                'start_min':   int(round(s['start_h'] * 60)),
                'end_min':     int(round(s['end_h']   * 60)),
                'name':        s.get('name', ''),
                'broadcaster': s.get('broadcaster', ''),
                'slug':        s.get('slug', ''),
                'rerun':       bool(s.get('rerun', False)),
            })
    return {
        'slots':      flat,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'tz':         'Asia/Jerusalem',
    }


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

    The `force` parameter is accepted for API compatibility but does not
    gate execution — board refreshes run on every trigger so the live
    WP schedule stays in sync with every upload, delete, and change.

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

            # ── 2b. Push now-playing JSON for the homepage "Now Broadcasting" widget ──
            # Same source-of-truth as the weekly grid (_build_wp_schedule_slots).
            # The widget PHP reads `zerock_now_playing_json`, finds the slot
            # covering current Israel-time, and renders the .hp-live-now block.
            # Without this, the homepage widget reads a hardcoded list in the
            # theme that drifts from reality (e.g. zifim 13:00–15:00 showing
            # mid-Wednesday morning).
            try:
                np_payload = json.dumps(_build_wp_now_playing_json(), ensure_ascii=False)
                r2b = _requests.post(
                    f"{WP_REST_BASE}/wc-admin/options",
                    json={'zerock_now_playing_json': np_payload},
                    auth=auth, headers=headers, timeout=15
                )
                results['now-playing-json'] = r2b.status_code
                if r2b.status_code == 200:
                    print("[WPSync] ✓ wc-admin/options (zerock_now_playing_json updated)", flush=True)
                else:
                    print(f"[WPSync] now-playing-json → {r2b.status_code}: {r2b.text[:100]}", flush=True)
            except Exception as e2b:
                results['now-playing-json'] = f'err:{e2b}'
                print(f"[WPSync] now-playing-json error: {e2b}", flush=True)

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
                    # Hide the injected board on every WP page except /schedule
                    # (page ID 254). WPCode ihaf_insert_footer injects globally,
                    # so we scope visibility here — on all other pages the wrapper
                    # is not rendered at all.
                    f'body:not(.page-id-{WP_SCHEDULE_PAGE_ID}) #zerock-board{{display:none!important}}'
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
                # Combined JS fix — single <script> block (WAF strips 2nd block).
                # 1. Spotify playlist link fix for /rock-chart/ page.
                # 2. Homepage now-playing fix: patches .hp-live-now / .hp-next-show
                #    broadcaster text using the public /zr/v1/np REST endpoint.
                _sp_top20  = SPOTIFY_TOP20_PLAYLIST
                _sp_palash = SPOTIFY_PALASH_PLAYLIST
                combined_fix = (
                    '\n<script id="zerock-fix">'
                    '(function(){'
                    # Spotify fix — rock-chart page only
                    'if(window.location.pathname.indexOf("/rock-chart/")!==-1){'
                    f'var t="{_sp_top20}",p="{_sp_palash}";'
                    'function sf(){document.querySelectorAll("a[href*=\'open.spotify.com/playlist/\']").forEach(function(a){'
                    'var h=a.href;'
                    'if(h.indexOf("1ifvWserGDqUQUH6Ows5oA")!==-1||h.indexOf(t)!==-1)a.href="https://open.spotify.com/playlist/"+t;'
                    'else if(h.indexOf("5NMCfgaWkLrFpusbgrMhU4")!==-1||h.indexOf(p)!==-1)a.href="https://open.spotify.com/playlist/"+p;'
                    '});}'
                    'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",sf);else sf();'
                    '}'
                    # Homepage fix — patches ACF-hardcoded broadcaster with live data
                    'var _hp=window.location.pathname;'
                    'if(_hp==="/"||_hp===""||_hp==="/index.php"){'
                    'function hf(){'
                    'fetch("/wp-json/zr/v1/np")'
                    '.then(function(r){return r.json();})'
                    '.then(function(html){'
                    'var d=document.createElement("div");d.innerHTML=html;'
                    'function p(s){var a=d.querySelector(s),b=document.querySelector(s);if(a&&b)b.textContent=a.textContent.trim();}'
                    'p(".hp-live-now .hp-show-name");p(".hp-live-now .hp-show-text");p(".hp-live-now .hp-show-time");'
                    'p(".hp-next-show .hp-show-name");p(".hp-next-show .hp-show-text");p(".hp-next-show .hp-show-time");'
                    '})'
                    '.catch(function(){});}'
                    'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",hf);else hf();'
                    '}'
                    '})();</script>'
                )
                footer_content = css + '\n' + html + combined_fix
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
            'no_wp':        s.get('no_wp', False),
            'next_broadcast': broadcast_dt.isoformat() if broadcast_dt else None,
            'next_upload':    upload_dt.isoformat()    if upload_dt    else None,
            'next_rerun':     rerun_dt.isoformat()     if rerun_dt     else None,
        })
    return jsonify(result)

@app.route('/api/schedule', methods=['POST'])
def api_add_show():
    show_key              = request.form.get('show_key', '').strip()
    manual_date              = request.form.get('manual_date', '').strip()          # YYYY-MM-DD, only for על הרוקר
    al_haroker_broadcaster   = request.form.get('al_haroker_broadcaster', '').strip()   # broadcaster for על הרוקר
    erev_albumim_broadcaster = request.form.get('erev_albumim_broadcaster', '').strip() # broadcaster for ערב של אלבומים
    mode                  = request.form.get('mode', 'queue_to_broadcast').strip()
    # על הרוקר always uploads to Podbean/WP — never allow queue_only for this show.
    if show_key == 'al_harocker':
        mode = 'queue_to_broadcast'
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
    # Hard block: NEVER_UPLOAD_SHOWS (e.g. ערב של אלבומים) must never go to Podbean or WP.
    if show_key in NEVER_UPLOAD_SHOWS:
        mode = 'queue_only'
        print(f"[Schedule] Hard-blocked upload for '{show_key}' — forced queue_only", flush=True)
    # Force queue_only for shows that have no Podbean/WP upload.
    # Exception: matzad_harok pre-recorded (single file) → upload to Podbean, skip WP.
    podbean_skip_wp = False
    if show_cfg and show_cfg.get('no_wp'):
        podbean_skip_wp = True   # Podbean yes, WP no
    elif show_cfg and show_cfg.get('no_podbean'):
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
    # If a non-rerun entry for the same show_key + broadcast time already exists,
    # remove it so the new upload takes over (new episode always wins).
    if show_cfg and broadcast_dt:
        existing = load_schedule()
        bcast_iso = broadcast_dt.isoformat()
        replaced = [e for e in existing
                    if e.get('show_key') == show_key
                    and e.get('scheduled_time') == bcast_iso
                    and not e.get('is_rerun')]
        if replaced:
            new_existing = [e for e in existing if e not in replaced]
            save_schedule(new_existing)
            print(f"[Schedule] Replaced existing entry: {show_key} @ {bcast_iso}", flush=True)

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
        'broadcaster':    (al_haroker_broadcaster   if show_key == 'al_harocker'   and al_haroker_broadcaster
                          else erev_albumim_broadcaster if show_key == 'erev_albumim' and erev_albumim_broadcaster
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
        'skip_wp':        podbean_skip_wp,
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
        # Respect upload_time gate — hold upload until configured time (e.g. מצעד הרוק at 15:00)
        _show_entry_for_gate = next((s for s in load_schedule() if s['id'] == show_id), {})
        if not _upload_time_reached(_show_entry_for_gate, show_cfg):
            _ut_str = show_cfg.get('upload_time', '?')
            print(f"[Schedule] '{name}' upload held — will fire at {_ut_str} (upload_time not yet reached)", flush=True)
        else:
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
    # Respect upload_time gate — hold until configured time (same logic as api_add_show)
    if mode == 'queue_to_broadcast' and not manual_schedule:
        _show_entry_for_gate = next((s for s in load_schedule() if s['id'] == show_id), {})
        if not _upload_time_reached(_show_entry_for_gate, show_cfg):
            _ut_str = show_cfg.get('upload_time', '?')
            print(f"[ScheduleURL] '{name}' upload held — will fire at {_ut_str} (upload_time not yet reached)", flush=True)
        else:
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

@app.route('/api/schedule/<show_id>/download')
def api_download_show(show_id):
    """Download show audio. Single-file → stream the file; multi-file (matzad / album)
    → stream a ZIP. Returns 404 if no resolvable files exist on disk."""
    schedule = load_schedule()
    show = next((s for s in schedule if s.get('id') == show_id), None)
    if not show:
        return jsonify({'error': 'show not found'}), 404

    # Collect candidate files in playback order
    files = []
    # 1) matzad-style: 20 מקום (slot #20 → #1) then 5 פל״ש interleaved? Use upload order for archive.
    if show.get('playlist_files'):
        files.extend([f for f in (show.get('playlist_files') or []) if f])
        files.extend([f for f in (show.get('palash_files')   or []) if f])
    # 2) album shows: nested list of lists
    elif show.get('albums'):
        for album in (show.get('albums') or []):
            for trk in (album or []):
                if trk: files.append(trk)
    # 3) generic flat list (fallback)
    elif show.get('files'):
        files.extend([f for f in (show.get('files') or []) if f])
    # 4) single file — prefer NAS copy (post-upload location), fall back to local
    else:
        for cand in (show.get('nas_path'), show.get('file_path')):
            if cand and os.path.exists(cand):
                files.append(cand)
                break

    # Filter to existing files; for multi-file shows, swap missing local paths for
    # their NAS counterparts when only the basename moved (best-effort fallback).
    resolved = []
    for f in files:
        if not f:
            continue
        if os.path.exists(f):
            resolved.append(f); continue
        # Try NAS_TEMP with same basename
        alt = os.path.join(NAS_TEMP, os.path.basename(f))
        if os.path.exists(alt):
            resolved.append(alt); continue
    files = resolved
    if not files:
        return jsonify({'error': 'no audio files found on disk'}), 404

    # Build a sensible base name for the download
    safe_show = "".join(c if c.isalnum() or c in ' _-' else '_'
                        for c in (show.get('name') or 'show')).strip().replace(' ', '_') or 'show'
    bcast = (show.get('scheduled_time') or '')[:10]   # YYYY-MM-DD
    base  = f"{safe_show}_{bcast}".rstrip('_')

    # Single file → direct stream
    if len(files) == 1:
        path = files[0]
        download_name = f"{base}{os.path.splitext(path)[1] or '.mp3'}"
        return send_file(path, as_attachment=True, download_name=download_name)

    # Multi-file → ZIP
    import io as _io, zipfile as _zip, re as _re
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, mode='w', compression=_zip.ZIP_STORED) as zf:
        used = set()
        for idx, path in enumerate(files, start=1):
            # Strip the show-id prefix for tidier names
            arc = os.path.basename(path)
            arc = _re.sub(r'^\d+_(?:pl|pa|a\d+_t)\d+_', '', arc)
            arc = f"{idx:02d}_{arc}"
            # Avoid name collisions
            if arc in used:
                root, ext = os.path.splitext(arc)
                arc = f"{root}_{idx}{ext}"
            used.add(arc)
            try:
                zf.write(path, arcname=arc)
            except Exception as ex:
                print(f"[Download] skip {path}: {ex}", flush=True)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"{base}.zip",
    )


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

@app.route('/api/seek', methods=['POST'])
def api_seek():
    """Seek to a position in the currently playing show/track.

    Body JSON: {"position": <float seconds>}

    Strategy: trim the current file from the requested position using ffmpeg
    (stream copy — near-instant), flush the shows queue, and push the trimmed
    file so playback resumes from the desired point.  Only works for show files
    (LOCAL_TEMP / NAS_TEMP); regular playlist tracks are skipped instead.
    """
    global _np_last_path, _np_track_start

    data     = request.get_json(silent=True) or {}
    position = float(data.get('position', 0))
    if position < 0:
        position = 0

    np = get_now_playing()
    full_path = np.get('full_path', '')
    duration  = np.get('duration', 0)

    if not full_path or not os.path.exists(full_path):
        return jsonify({'ok': False, 'error': 'No file playing'})

    is_show = full_path.startswith(LOCAL_TEMP) or NAS_TEMP in full_path
    if not is_show:
        # For regular playlist tracks we can't seek — just note the request
        return jsonify({'ok': False, 'error': 'Seek only supported for show/upload files'})

    if duration > 0:
        position = min(position, duration - 1)

    # Trim with ffmpeg (stream copy = no re-encode, runs in <1 sec)
    base, ext = os.path.splitext(os.path.basename(full_path))
    seek_path = os.path.join(NAS_TEMP, f'_seek_{int(time.time()*1000)}{ext or ".mp3"}')
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-ss', str(position), '-i', full_path,
             '-c', 'copy', seek_path],
            capture_output=True, timeout=15
        )
        if result.returncode != 0 or not os.path.exists(seek_path):
            return jsonify({'ok': False, 'error': 'ffmpeg trim failed'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

    # Flush current queue and skip current track, then push trimmed file
    lq_send_direct(['shows.flush_and_skip', f'shows.push {seek_path}'])

    # Reset NP tracking so updater immediately picks up the new track
    _np_last_path   = ''
    _np_track_start = None

    return jsonify({'ok': True, 'position': position, 'seek_path': os.path.basename(seek_path)})


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


_unsub_lock = threading.Lock()

def _load_unsubscribe_tokens():
    try:
        with open(UNSUBSCRIBE_TOKENS_FILE) as f:
            return json.load(f)   # {token: email}
    except Exception:
        return {}

def _save_unsubscribe_tokens(data):
    with open(UNSUBSCRIBE_TOKENS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_unsubscribed_emails():
    try:
        with open(UNSUBSCRIBED_EMAILS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_unsubscribed_emails(emails_set):
    with open(UNSUBSCRIBED_EMAILS_FILE, 'w') as f:
        json.dump(sorted(emails_set), f, ensure_ascii=False, indent=2)

def _get_unsubscribe_token(email):
    """Return a stable unsubscribe token for this email, creating one if needed."""
    email = email.strip().lower()
    with _unsub_lock:
        tokens = _load_unsubscribe_tokens()
        for tok, em in tokens.items():
            if em == email:
                return tok
        tok = secrets.token_urlsafe(24)
        tokens[tok] = email
        _save_unsubscribe_tokens(tokens)
        return tok

def _is_unsubscribed(email):
    """Return True if this email has opted out of marketing emails."""
    return email.strip().lower() in _load_unsubscribed_emails()

def _do_unsubscribe(email):
    """Mark email as unsubscribed and deactivate in subscriber lists (idempotent)."""
    email = email.strip().lower()
    with _unsub_lock:
        emails = _load_unsubscribed_emails()
        emails.add(email)
        _save_unsubscribed_emails(emails)
        # Deactivate in al_haroker_subscribers
        try:
            with open(AL_HAROKER_SUBSCRIBERS_FILE) as f:
                subs = json.load(f)
            changed = False
            for s in subs:
                if s.get('email', '').strip().lower() == email:
                    s['active'] = False
                    changed = True
            if changed:
                with open(AL_HAROKER_SUBSCRIBERS_FILE, 'w') as f:
                    json.dump(subs, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # Deactivate in general subscribers.json (poll invites)
        _subs_path = os.path.join(RADIO_DIR, 'subscribers.json')
        try:
            with open(_subs_path) as f:
                subs2 = json.load(f)
            changed2 = False
            for s in subs2:
                if s.get('email', '').strip().lower() == email:
                    s['active'] = False
                    changed2 = True
            if changed2:
                with open(_subs_path, 'w') as f:
                    json.dump(subs2, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


@app.route('/unsubscribe/<token>')
def unsubscribe_page(token):
    """One-click unsubscribe: marks the email as opted-out and shows confirmation."""
    with _unsub_lock:
        tokens = _load_unsubscribe_tokens()
    email = tokens.get(token)
    if not email:
        return ('<div dir="rtl" style="font-family:Arial,sans-serif;text-align:center;'
                'padding:60px">הקישור אינו תקין.</div>'), 404
    _do_unsubscribe(email)
    return f'''<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="utf-8">
  <title>ביטול הרשמה — ZeRock Radio</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #111; color: #eee;
            text-align: center; padding: 60px 20px; }}
    h1   {{ color: #e63946; }}
    a    {{ color: #e63946; }}
    .box {{ background: #1e1e1e; border-radius: 12px; max-width: 480px;
            margin: 0 auto; padding: 40px 32px; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>🤘 ZeRock Radio</h1>
    <h2>בוצע ביטול הרשמה</h2>
    <p>הכתובת <strong>{email}</strong> הוסרה מרשימת התפוצה שלנו.</p>
    <p>לא תקבל יותר הזמנות למצעד או לעל הרוקר.</p>
    <br>
    <a href="https://zerockradio.com">חזרה לאתר →</a>
  </div>
</body>
</html>''', 200


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


def _send_al_haroker_reminder_email(booking):
    """Send a 24-hour reminder email to an al-haroker broadcaster who has
    registered but not yet uploaded their show file."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[AlHaRoker] SMTP not configured — skipping reminder to {booking.get('email','')}", flush=True)
        return False
    email = booking.get('email', '')
    if not email:
        return False
    try:
        date_str   = booking['date']           # YYYY-MM-DD
        upload_url = f"{ZEROCK_PUBLIC_URL}/al-haroker-upload/{booking['token']}"

        body_text = (
            f"שלום {booking['broadcaster']},\n\n"
            f"רצינו להזכיר לך שנרשמת לעל הרוקר למחר,\n"
            f"הנה הקישור להעלאת התוכנית\n"
            f"{upload_url}\n\n"
            f"תודה,\n"
            f"צוות ״רדיו זה רוק״\n"
        )

        body_html = f"""<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;color:#222;line-height:1.6">
<p>שלום <strong>{booking['broadcaster']}</strong>,</p>
<p>רצינו להזכיר לך שנרשמת לעל הרוקר למחר,<br>
הנה הקישור להעלאת התוכנית:</p>
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
<p>תודה,<br><strong>צוות ״רדיו זה רוק״</strong></p>
</div>"""

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"תזכורת להעלות על הרוקר לתאריך {date_str}"
        msg['From']    = SMTP_FROM_ADDR
        msg['To']      = email
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM_ADDR, [email], msg.as_bytes())
        print(f"[AlHaRoker] Reminder email sent → {email} for {date_str}", flush=True)
        return True
    except Exception as e:
        print(f"[AlHaRoker] Reminder email error: {e}", flush=True)
        return False


def _al_haroker_reminder_loop():
    """Background thread: every 15 minutes, scan al-haroker bookings and send
    a 24-hour reminder to broadcasters who:
      - Are registered (booking exists)
      - Have NOT uploaded yet (booking['uploaded'] is False)
      - Broadcast is within the next 24 hours (0 < hours_until <= 24)
      - Have not already received a reminder (booking['reminder_sent_at'] unset)
    Only applies to al-haroker bookings; no other shows are touched."""
    # Give Flask a moment to start, then run immediately so a late-deploy
    # doesn't miss a booking that's already inside the 24h window.
    time.sleep(20)
    while True:
        try:
            now = datetime.now()
            with _bookings_lock:
                bookings = _load_al_haroker_bookings()
                changed  = False
                for b in bookings:
                    # Guards: only unreminded, un-uploaded bookings
                    if b.get('uploaded'):
                        continue
                    if b.get('reminder_sent_at'):
                        continue
                    if not b.get('email'):
                        continue
                    try:
                        broadcast_dt = datetime.strptime(b['date'], '%Y-%m-%d').replace(
                            hour=AL_HAROKER_BROADCAST_HOUR, minute=0, second=0, microsecond=0)
                    except Exception:
                        continue
                    hours_until = (broadcast_dt - now).total_seconds() / 3600.0
                    # Within the 24h window (exclude past-broadcast bookings)
                    if 0 < hours_until <= 24:
                        if _send_al_haroker_reminder_email(b):
                            b['reminder_sent_at'] = now.isoformat()
                            changed = True
                if changed:
                    _save_al_haroker_bookings(bookings)
        except Exception as e:
            print(f"[AlHaRoker] Reminder loop error: {e}", flush=True)
        time.sleep(900)   # 15 minutes

threading.Thread(target=_al_haroker_reminder_loop, daemon=True).start()


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


@app.route('/api/al-haroker-booking/<token>/status')
def api_al_haroker_booking_status(token):
    """Public: check whether an al-haroker booking has been uploaded (client error fallback)."""
    bookings = _load_al_haroker_bookings()
    booking  = next((b for b in bookings if b['token'] == token), None)
    if not booking:
        return jsonify({'exists': False, 'uploaded': False})
    return jsonify({'exists': True, 'uploaded': bool(booking.get('uploaded'))})


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


# ─── One-Time Upload Links (admin-issued, single-use) ─────────────────────────

_one_time_lock = threading.Lock()

def _load_one_time_links():
    try:
        with open(ONE_TIME_LINKS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_one_time_links(data):
    with open(ONE_TIME_LINKS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.route('/api/one-time-link', methods=['POST'])
def api_one_time_link_create():
    """Admin: generate a one-time upload link for a show."""
    data = request.get_json(silent=True) or request.form
    show_key = (data.get('show_key') or '').strip()
    if not show_key:
        return jsonify({'error': 'show_key required'}), 400

    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == show_key), None)
    if not show_cfg:
        return jsonify({'error': 'unknown show'}), 404

    import secrets as _secrets
    token = _secrets.token_urlsafe(24)

    entry = {
        'token':       token,
        'show_key':    show_key,
        'show_name':   show_cfg['name'],
        'broadcaster': show_cfg.get('broadcaster', ''),
        'created_at':  datetime.now().isoformat(),
    }
    with _one_time_lock:
        links = _load_one_time_links()
        links.append(entry)
        _save_one_time_links(links)

    url = f"{ZEROCK_PUBLIC_URL}/one-time-upload/{token}"
    print(f"[OneTimeLink] Generated for {show_cfg['name']} ({show_cfg.get('broadcaster','')}) → {token}", flush=True)
    return jsonify({'ok': True, 'token': token, 'url': url, 'entry': entry})


@app.route('/api/one-time-link', methods=['GET'])
def api_one_time_link_list():
    """Admin: list all active (unused) one-time links."""
    return jsonify(_load_one_time_links())


@app.route('/api/one-time-link/<token>/status')
def api_one_time_link_status(token):
    """Public: check whether a one-time token still exists (used by client error fallback)."""
    links = _load_one_time_links()
    return jsonify({'exists': any(l['token'] == token for l in links)})


@app.route('/api/one-time-link/<token>', methods=['DELETE'])
def api_one_time_link_delete(token):
    """Admin: revoke a one-time link."""
    with _one_time_lock:
        links = _load_one_time_links()
        new_links = [l for l in links if l['token'] != token]
        if len(new_links) == len(links):
            return jsonify({'error': 'not found'}), 404
        _save_one_time_links(new_links)
    return jsonify({'ok': True})


@app.route('/one-time-upload/<token>')
def one_time_upload_page(token):
    """Public: broadcaster's upload form."""
    links = _load_one_time_links()
    entry = next((l for l in links if l['token'] == token), None)
    if not entry:
        return render_template('one_time_upload.html', invalid=True, entry=None)
    return render_template('one_time_upload.html', invalid=False, entry=entry)


@app.route('/api/one-time-upload/<token>', methods=['POST'])
def api_one_time_upload(token):
    """Public: receive the file + chosen broadcast time, schedule it, then burn the token."""
    # Validate + claim token under lock
    with _one_time_lock:
        links = _load_one_time_links()
        entry = next((l for l in links if l['token'] == token), None)
        if not entry:
            return jsonify({'error': 'invalid_or_used_token'}), 404

    audio_file       = request.files.get('file')
    broadcast_time_s = (request.form.get('broadcast_time') or '').strip()
    episode_num      = (request.form.get('episode_num') or '').strip()
    description      = (request.form.get('description') or '').strip()

    if not audio_file or not audio_file.filename:
        return jsonify({'error': 'קובץ אודיו נדרש'}), 400
    if not broadcast_time_s:
        return jsonify({'error': 'יש לבחור זמן שידור'}), 400

    # Parse "YYYY-MM-DDTHH:MM" from <input type=datetime-local>
    try:
        broadcast_dt = datetime.fromisoformat(broadcast_time_s)
    except Exception:
        return jsonify({'error': 'זמן שידור לא תקין'}), 400

    if broadcast_dt < datetime.now() - timedelta(minutes=5):
        return jsonify({'error': 'זמן השידור חייב להיות בעתיד'}), 400

    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == entry['show_key']), None)
    if not show_cfg:
        return jsonify({'error': 'show config missing'}), 500

    # Save file locally
    show_id   = str(int(time.time() * 1000))
    safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                        for c in os.path.basename(audio_file.filename))
    filename   = f"{int(time.time())}_{safe_name}"
    local_path = os.path.join(LOCAL_TEMP, filename)
    nas_path   = os.path.join(NAS_TEMP, filename)
    audio_file.save(local_path)

    # Build schedule entry — broadcaster-chosen time, NO rerun, mode queue_to_broadcast
    show = {
        'id':              show_id,
        'name':            show_cfg['name'],
        'show_key':        show_cfg['key'],
        'broadcaster':     entry.get('broadcaster') or show_cfg.get('broadcaster', ''),
        'mode':            'queue_to_broadcast',
        'episode_num':     episode_num,
        'description':     description,
        'scheduled_time':  broadcast_dt.isoformat(),
        'upload_time':     broadcast_dt.isoformat(),  # upload to Podbean/WP at broadcast time
        'rerun_time':      None,  # disregards regular schedule — no rerun
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
        'one_time_link':   True,
        'added_at':        datetime.now().isoformat(),
    }

    with _schedule_lock:
        schedule = load_schedule()
        schedule.append(show)
        save_schedule(schedule)

    # BURN THE TOKEN — single-use, removed after upload
    with _one_time_lock:
        links = _load_one_time_links()
        new_links = [l for l in links if l['token'] != token]
        _save_one_time_links(new_links)

    # Move to NAS + upload to Podbean/WP
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

    print(f"[OneTimeLink] Used by {entry.get('broadcaster','?')} for {show_cfg['name']} → broadcast {broadcast_dt.isoformat()}", flush=True)
    return jsonify({'ok': True, 'broadcast_time': broadcast_dt.isoformat()})


# ─── Matzad voting polls ──────────────────────────────────────────────────────

_polls_lock = threading.Lock()
_votes_lock = threading.Lock()
_poll_codes_lock = threading.Lock()

def _load_poll_codes():
    try:
        with open(POLL_CODES_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_poll_codes(data):
    with open(POLL_CODES_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _send_poll_verification_email(to_email, code, poll_title):
    """Send a 6-digit email verification code to a poll voter."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[Poll] SMTP not configured — verification code for {to_email}: {code}", flush=True)
        return
    body_html = f"""<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;color:#222;line-height:1.6">
<p>שלום,</p>
<p>קיבלנו בקשת אימות להצבעה ב-<strong>{poll_title}</strong>.</p>
<p>קוד האימות שלך:</p>
<p style="font-size:2.4em;font-weight:bold;letter-spacing:10px;color:#e63946;
          text-align:center;padding:22px 0;background:#f9f9f9;border-radius:8px;
          margin:18px 0">{code}</p>
<p style="color:#888;font-size:13px">הקוד תקף ל-10 דקות.<br>
אם לא ביקשת קוד זה, התעלם מהודעה זו.</p>
<hr style="border:none;border-top:1px solid #ddd;margin:24px 0">
<p><strong>צוות ZeRock Radio</strong> 🤘</p>
</div>"""
    body_text = f"קוד האימות שלך להצבעה ב-{poll_title}: {code}\n(תקף ל-10 דקות)\n\nZeRock Radio"
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"ZeRock Radio — קוד אימות {code}"
    msg['From']    = SMTP_FROM_ADDR
    msg['To']      = to_email
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.ehlo(); s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_FROM_ADDR, [to_email], msg.as_bytes())
    print(f"[Poll] Verification code sent → {to_email}", flush=True)

def _load_polls():
    try:
        with open(POLLS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_polls(data):
    with open(POLLS_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_poll_votes():
    try:
        with open(POLL_VOTES_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def _save_poll_votes(data):
    with open(POLL_VOTES_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_tiebreak_order(poll):
    """Return a stable {song_id: float} dict for tiebreaking within a poll.

    Values are generated once per poll and persisted in polls.json under
    poll['tiebreak_order'].  Any song not yet in the stored dict gets a
    freshly generated random float; the result is written to disk inside
    _polls_lock so concurrent requests stay consistent.

    Ties are therefore broken the same way on every request — the order
    only changes if new songs are added to the poll (new IDs get new floats).
    """
    import random as _tb
    stored  = poll.get('tiebreak_order') or {}
    missing = [s['id'] for s in poll.get('songs', []) if s['id'] not in stored]
    if not missing:
        return stored

    # Need to generate and persist new entries — use _polls_lock to avoid races
    with _polls_lock:
        all_polls = _load_polls()
        p = next((x for x in all_polls if x['id'] == poll['id']), None)
        if p is None:
            # Poll not on disk (race / brand-new) — generate without saving
            result = dict(stored)
            for sid in missing:
                result[sid] = _tb.random()
            return result
        stored2 = p.get('tiebreak_order') or {}
        changed = False
        for s in p.get('songs', []):
            if s['id'] not in stored2:
                stored2[s['id']] = _tb.random()
                changed = True
        if changed:
            p['tiebreak_order'] = stored2
            _save_polls(all_polls)
        # Keep the caller's in-memory poll dict in sync (important when the
        # caller will later call _save_polls on its own polls list copy)
        poll['tiebreak_order'] = stored2
        return stored2


# ── Spotify Client Credentials token cache ────────────────────────────────────
_spotify_token_cache = {'token': None, 'expires_at': 0}
_spotify_lock        = threading.Lock()

def _spotify_get_token():
    """Fetch (or return cached) Spotify Client Credentials token."""
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    now = time.time()
    with _spotify_lock:
        if _spotify_token_cache['token'] and _spotify_token_cache['expires_at'] > now + 30:
            return _spotify_token_cache['token']
        try:
            import base64, urllib.request, urllib.parse, urllib.error
            creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
            data  = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
            req   = urllib.request.Request(
                'https://accounts.spotify.com/api/token',
                data=data,
                headers={
                    'Authorization': f'Basic {creds}',
                    'Content-Type':  'application/x-www-form-urlencoded',
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read())
            tok = payload.get('access_token')
            ttl = int(payload.get('expires_in', 3600))
            _spotify_token_cache['token']      = tok
            _spotify_token_cache['expires_at'] = now + ttl
            return tok
        except Exception as e:
            print(f"[Spotify] token error: {e}", flush=True)
            return None


def _spotify_search_track(query):
    """Search Spotify for a track. Returns the open.spotify.com URL of the top match, or None."""
    tok = _spotify_get_token()
    if not tok or not query:
        return None
    try:
        import urllib.request, urllib.parse, urllib.error
        params = urllib.parse.urlencode({'q': query, 'type': 'track', 'limit': 1, 'market': 'IL'})
        req = urllib.request.Request(
            f'https://api.spotify.com/v1/search?{params}',
            headers={'Authorization': f'Bearer {tok}'},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read())
        items = (payload.get('tracks') or {}).get('items') or []
        if not items:
            return None
        return items[0].get('external_urls', {}).get('spotify')
    except Exception as e:
        print(f"[Spotify] search error for '{query}': {e}", flush=True)
        return None


# ── Spotify OAuth (user token) ────────────────────────────────────────────────
_spotify_user_token_cache = {'token': None, 'expires_at': 0}

def _spotify_get_user_token():
    """Get a user-scoped Spotify access token using the stored refresh token.
    Returns None if SPOTIFY_REFRESH_TOKEN is not configured."""
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET or not SPOTIFY_REFRESH_TOKEN:
        return None
    import base64, urllib.request, urllib.parse
    now = time.time()
    cached = _spotify_user_token_cache
    if cached['token'] and cached['expires_at'] > now + 30:
        return cached['token']
    try:
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        data  = urllib.parse.urlencode({
            'grant_type':    'refresh_token',
            'refresh_token': SPOTIFY_REFRESH_TOKEN,
        }).encode()
        req = urllib.request.Request(
            'https://accounts.spotify.com/api/token',
            data=data,
            headers={
                'Authorization': f'Basic {creds}',
                'Content-Type':  'application/x-www-form-urlencoded',
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read())
        tok = payload.get('access_token')
        ttl = int(payload.get('expires_in', 3600))
        cached['token']      = tok
        cached['expires_at'] = now + ttl
        return tok
    except Exception as e:
        print(f"[Spotify] user-token refresh error: {e}", flush=True)
        return None


def _spotify_replace_playlist(playlist_id, uris):
    """Replace all tracks in a Spotify playlist with the given track URIs.
    Uses PUT /playlists/{id}/items (replaces entire playlist).
    NOTE: /tracks was deprecated Feb 2026 — must use /items.
    uris: list of 'spotify:track:...' strings."""
    import urllib.request, urllib.parse
    tok = _spotify_get_user_token()
    if not tok:
        print(f"[Spotify] No user token — cannot update playlist {playlist_id}", flush=True)
        return False
    try:
        # Spotify PUT replaces up to 100 items per call
        body = json.dumps({'uris': uris[:100]}).encode()
        req  = urllib.request.Request(
            f'https://api.spotify.com/v1/playlists/{playlist_id}/items',
            data=body,
            method='PUT',
            headers={
                'Authorization': f'Bearer {tok}',
                'Content-Type':  'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            status = r.status
        print(f"[Spotify] Playlist {playlist_id} updated → {len(uris)} tracks (HTTP {status})", flush=True)
        return True
    except Exception as e:
        print(f"[Spotify] replace_playlist error for {playlist_id}: {e}", flush=True)
        return False


def _spotify_track_uri_from_url(url):
    """Convert a spotify.com track URL to a spotify:track: URI."""
    if not url:
        return None
    import re as _re
    m = _re.search(r'track/([A-Za-z0-9]+)', url)
    return f'spotify:track:{m.group(1)}' if m else None


def _spotify_update_playlist_description(playlist_id, description):
    """Update the description of a Spotify playlist."""
    import urllib.request
    tok = _spotify_get_user_token()
    if not tok:
        print(f"[Spotify] No user token — cannot update description for {playlist_id}", flush=True)
        return False
    try:
        body = json.dumps({'description': description}).encode()
        req  = urllib.request.Request(
            f'https://api.spotify.com/v1/playlists/{playlist_id}',
            data=body,
            method='PUT',
            headers={
                'Authorization': f'Bearer {tok}',
                'Content-Type':  'application/json',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            status = r.status
        print(f"[Spotify] Playlist {playlist_id} description updated (HTTP {status})", flush=True)
        return True
    except Exception as e:
        print(f"[Spotify] update_description error for {playlist_id}: {e}", flush=True)
        return False


def _spotify_update_wp_links(top20_id, palash_id):
    """Update the Spotify playlist IDs in WP options and in the ihaf_insert_footer JS block."""
    import re as _re3
    if not WP_USERNAME or not WP_APP_PASS:
        return
    try:
        auth    = (WP_USERNAME, WP_APP_PASS)
        headers = {'Content-Type': 'application/json'}
        base    = f"{WP_URL}/wp-json"

        # 1. Update the WP options for the playlist IDs
        _requests.post(f"{base}/wc-admin/options",
            json={'zerock_spotify_top20': top20_id, 'zerock_spotify_palash': palash_id},
            auth=auth, headers=headers, timeout=15)

        # 2. Update the JS block inside ihaf_insert_footer
        r = _requests.get(f"{base}/wc-admin/options?options=ihaf_insert_footer",
            auth=auth, timeout=15)
        current = (r.json().get('ihaf_insert_footer') or '')
        new_js = f"""
<!-- zerock-spotify-fix -->
<script>
(function(){{
  if (window.location.pathname.indexOf("/rock-chart/") === -1) return;
  var top20  = "{top20_id}";
  var palash = "{palash_id}";
  function fix() {{
    document.querySelectorAll("a[href*=\\"open.spotify.com/playlist/\\"]").forEach(function(a) {{
      var h = a.href;
      if (h.indexOf("1ifvWserGDqUQUH6Ows5oA") !== -1 || h.indexOf(top20) !== -1)
        a.href = "https://open.spotify.com/playlist/" + top20;
      else if (h.indexOf("5NMCfgaWkLrFpusbgrMhU4") !== -1 || h.indexOf(palash) !== -1)
        a.href = "https://open.spotify.com/playlist/" + palash;
    }});
  }}
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fix);
  else fix();
}})();
</script>
<!-- /zerock-spotify-fix -->"""
        updated = _re3.sub(
            r'<!-- zerock-spotify-fix -->.*?<!-- /zerock-spotify-fix -->',
            new_js.strip(), current, flags=_re3.DOTALL
        )
        if '<!-- zerock-spotify-fix -->' not in updated:
            updated = current.rstrip() + '\n' + new_js
        _requests.post(f"{base}/wc-admin/options",
            json={'ihaf_insert_footer': updated},
            auth=auth, headers=headers, timeout=15)
        print(f"[Spotify] WP rock-chart links updated → top20={top20_id} palash={palash_id}", flush=True)
    except Exception as e:
        print(f"[Spotify] WP link update error: {e}", flush=True)


# ── Palash artist welcome emails (sent on Thursday 15:00 with each new poll) ──

def _find_release_email_for_palash(label):
    """Search new_releases.json for the sender email matching a Palash song label.
    Label format: 'Artist - Song Title'.
    Matches against id3_artist / id3_title tags first, then subject / from_name.
    Returns (email, from_name) or (None, None)."""
    try:
        releases = _nr_load()
    except Exception:
        return None, None
    parts     = label.split(' - ', 1)
    artist_q  = parts[0].strip().lower()  if parts             else ''
    title_q   = parts[1].strip().lower()  if len(parts) > 1   else ''

    for r in releases:
        email = (r.get('from_email') or '').strip()
        if not email or '@' not in email:
            continue
        # Check id3 tags on each attached file (most reliable)
        for f in r.get('files', []) or []:
            id3_art = (f.get('id3_artist') or '').lower()
            id3_ttl = (f.get('id3_title')  or '').lower()
            if artist_q and (artist_q in id3_art or id3_art in artist_q):
                if not title_q or (title_q in id3_ttl or id3_ttl in title_q):
                    return email, (r.get('from_name') or '')
        # Fallback: artist name appears in the email subject line
        subj = (r.get('subject') or '').lower()
        if artist_q and artist_q in subj:
            return email, (r.get('from_name') or '')
    return None, None


def _send_palash_welcome_emails(palash_songs):
    """Send a welcome / chart-entry notification email to each Palash artist.
    Called in a background thread immediately after the Thursday 15:00 poll renewal."""
    if not SMTP_USER or not SMTP_PASS:
        print("[PalashEmail] SMTP not configured — skipping", flush=True)
        return
    sent      = 0
    not_found = []
    seen_emails = set()   # deduplicate — one email per address regardless of how many songs match
    for song in palash_songs:
        label = song.get('label', '')
        email, _ = _find_release_email_for_palash(label)
        if not email:
            print(f"[PalashEmail] No email found for: {label}", flush=True)
            not_found.append(label)
            continue
        if email.lower() in seen_emails:
            print(f"[PalashEmail] Skipping duplicate address {email} for: {label}", flush=True)
            continue
        seen_emails.add(email.lower())
        try:
            body_html = (
                '<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;'
                'color:#222;line-height:1.8">'
                '<p>שלום</p>'
                '<p>ברוכים הבאים לפינה לשיפוטכם במצעד הרוק של ישראל!<br>'
                'מוזמנים לשתף לעוקבים שלכם קישור להצבעה ← '
                '<a href="https://linktr.ee/israelirockchart">'
                'https://linktr.ee/israelirockchart</a></p>'
                '<p>שימו לב ניתן להצביע עד יום שלישי בשעה 19:00</p>'
                '<p>ובנוסף יעלה פוסט עליכם במהלך השבוע</p>'
                '<hr style="border:none;border-top:1px solid #ddd;margin:20px 0">'
                '<p><strong>צוות ZeRock Radio</strong> 🤘</p>'
                '</div>'
            )
            body_text = (
                'שלום\n\n'
                'ברוכים הבאים לפינה לשיפוטכם במצעד הרוק של ישראל!\n'
                'מוזמנים לשתף לעוקבים שלכם קישור להצבעה --> '
                'https://linktr.ee/israelirockchart\n'
                'שימו לב ניתן להצביע עד יום שלישי בשעה 19:00\n'
                'ובנוסף יעלה פוסט עליכם במהלך השבוע\n\n'
                'צוות ZeRock Radio 🤘'
            )
            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'כניסה למצעד הרוק של ישראל'
            msg['From']    = SMTP_FROM_ADDR
            msg['To']      = email
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html',  'utf-8'))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo(); s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM_ADDR, [email], msg.as_bytes())
            print(f"[PalashEmail] ✓ Sent to {email} ({label})", flush=True)
            sent += 1
        except Exception as e:
            print(f"[PalashEmail] Error → {email} ({label}): {e}", flush=True)
    print(f"[PalashEmail] Done — sent: {sent}, not found: {not_found}", flush=True)


# ── Weekly poll voter invite email ────────────────────────────────────────────

def _send_weekly_vote_invites(old_poll, new_poll_id):
    """Collect real email addresses from old poll votes and send Hebrew invite."""
    if not SMTP_USER or not SMTP_PASS:
        print("[WeeklyRenew] SMTP not configured — skipping invite emails", flush=True)
        return
    votes     = _load_poll_votes()
    old_id    = old_poll['id']
    vote_url  = f"{ZEROCK_PUBLIC_URL}/poll/{new_poll_id}"
    # Collect real (non-synthetic) emails for the old poll
    fake_domains = {'forms-import.zerockradio.com', 'admin.zerockradio.com'}
    emails_seen  = set()
    recipients   = []
    for v in votes:
        if v.get('poll_id') != old_id:
            continue
        email = (v.get('email') or '').strip().lower()
        if not email or '@' not in email:
            continue
        domain = email.split('@')[-1]
        if domain in fake_domains:
            continue
        if email in emails_seen:
            continue
        emails_seen.add(email)
        recipients.append(email)

    # Also include all active subscribers from subscribers.json
    _subs_path = os.path.join(RADIO_DIR, 'subscribers.json')
    if os.path.exists(_subs_path):
        try:
            _subs = json.load(open(_subs_path))
            for _s in _subs:
                _em = (_s.get('email') or '').strip().lower()
                if _em and '@' in _em and _s.get('active', True) and _em not in emails_seen:
                    emails_seen.add(_em)
                    recipients.append(_em)
        except Exception as _e:
            print(f"[WeeklyRenew] subscribers.json load error: {_e}", flush=True)

    # Filter out unsubscribed emails
    recipients = [e for e in recipients if not _is_unsubscribed(e)]

    if not recipients:
        print("[WeeklyRenew] No real email addresses to invite", flush=True)
        return

    body_html_base = f"""<div dir="rtl" style="font-family:Arial,sans-serif;font-size:16px;color:#222;line-height:1.8">
<p>היי 👋</p>
<p>זה אנחנו, מצוות המצעד של <strong>רדיו זה רוק</strong>.</p>
<p>מוזמנים להצביע שוב למצעד הרוק של ישראל — ההצבעה השבועית החדשה פתוחה!</p>
<p style="text-align:center;margin:24px 0">
  <a href="{vote_url}" style="background:#e63946;color:#fff;padding:14px 32px;border-radius:8px;
     text-decoration:none;font-size:18px;font-weight:bold">להצביע עכשיו 🤘</a>
</p>
<hr style="border:none;border-top:1px solid #ddd;margin:24px 0">
<p style="color:#888;font-size:13px">קיבלת מייל זה כי הצבעת בעבר במצעד הרוק של רדיו זה רוק.</p>
<p><strong>צוות ZeRock Radio</strong> 🎸</p>"""
    body_text_base = (f"היי!\nמוזמנים להצביע שוב למצעד הרוק של ישראל.\n\n"
                      f"הנה הלינק: {vote_url}\n\nZeRock Radio 🤘")

    sent = 0
    errors = 0
    for email in recipients:
        try:
            unsub_url  = f"{ZEROCK_PUBLIC_URL}/unsubscribe/{_get_unsubscribe_token(email)}"
            unsub_html = (f'<p style="text-align:center;margin-top:20px">'
                          f'<a href="{unsub_url}" style="color:#bbb;font-size:12px;text-decoration:none">'
                          f'הסר אותי מרשימת התפוצה</a></p>\n</div>')
            body_html  = body_html_base + unsub_html
            body_text  = body_text_base + f"\n\nלביטול הרשמה: {unsub_url}"
            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'מוזמנים להצביע שוב — מצעד הרוק של רדיו זה רוק 🤘'
            msg['From']    = SMTP_FROM_ADDR
            msg['To']      = email
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
                s.ehlo(); s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM_ADDR, [email], msg.as_bytes())
            sent += 1
        except Exception as e:
            print(f"[WeeklyRenew] invite email error → {email}: {e}", flush=True)
            errors += 1
    print(f"[WeeklyRenew] Invite emails sent: {sent}, errors: {errors}", flush=True)


def _calc_default_voting_window(now=None):
    """Compute the default voting window covering or following 'now'.
    Window: Thursday 15:00 → next Tuesday 19:00 (5 days 4 hours).
    If 'now' is inside an active window, return that window; otherwise the next one."""
    if now is None:
        now = datetime.now()
    # Python weekday: Mon=0 … Thu=3 … Sun=6
    days_since_thu = (now.weekday() - 3) % 7
    last_thu_1500 = (now - timedelta(days=days_since_thu)).replace(
        hour=15, minute=0, second=0, microsecond=0)
    if last_thu_1500 > now:
        last_thu_1500 -= timedelta(days=7)
    closes = last_thu_1500 + timedelta(days=5, hours=4)  # → following Tue 19:00
    if closes >= now:
        return last_thu_1500, closes
    next_thu = last_thu_1500 + timedelta(days=7)
    return next_thu, next_thu + timedelta(days=5, hours=4)


def _poll_is_open(poll, now=None):
    """Effective open state: schedule-based, with optional manual close fallback."""
    if now is None:
        now = datetime.now()
    o = poll.get('opens_at')
    c = poll.get('closes_at')
    if not o or not c:
        # Legacy polls without a schedule fall back to the manual `open` flag.
        return bool(poll.get('open', True))
    try:
        opens_dt  = datetime.fromisoformat(o)
        closes_dt = datetime.fromisoformat(c)
        # Strip timezone so comparison works against naive datetime.now()
        if opens_dt.tzinfo  is not None: opens_dt  = opens_dt.replace(tzinfo=None)
        if closes_dt.tzinfo is not None: closes_dt = closes_dt.replace(tzinfo=None)
        return opens_dt <= now <= closes_dt
    except Exception:
        return bool(poll.get('open', True))


def _label_from_filename(path, prefix_pat):
    """Derive a clean display label from an uploaded matzad/palash file path.
    Strips '<show_id>_pl##_' or '<show_id>_pa##_' prefix, then extension,
    then converts underscores to spaces."""
    import re
    base = os.path.splitext(os.path.basename(path or ''))[0]
    base = re.sub(prefix_pat, '', base)
    return base.replace('_', ' ').strip() or 'Untitled'


@app.route('/api/matzad-episodes')
def api_matzad_episodes():
    """Admin: list scheduled matzad episodes that have playlist + palash files."""
    schedule = load_schedule()
    out = []
    for s in schedule:
        if s.get('show_key') != 'matzad_harok':
            continue
        pl = s.get('playlist_files') or []
        pa = s.get('palash_files') or []
        if not pl and not pa:
            continue
        out.append({
            'id':             s['id'],
            'scheduled_time': s.get('scheduled_time'),
            'episode_num':    s.get('episode_num') or '',
            'pl_count':       len(pl),
            'pa_count':       len(pa),
        })
    out.sort(key=lambda x: x['scheduled_time'] or '', reverse=True)
    return jsonify(out)


@app.route('/api/matzad-episode/<show_id>/songs')
def api_matzad_episode_songs(show_id):
    """Admin: return the song list for a matzad episode (auto-labeled from filenames)."""
    schedule = load_schedule()
    show = next((s for s in schedule if s.get('id') == show_id and s.get('show_key') == 'matzad_harok'), None)
    if not show:
        return jsonify({'error': 'episode not found'}), 404

    pl_files = show.get('playlist_files') or []
    pl_slots = show.get('playlist_slots') or []
    pa_files = show.get('palash_files')   or []

    songs = []
    # Pair each playlist file with its slot, sort 20→1 for display
    pairs = sorted(
        [(pl_slots[i] if i < len(pl_slots) else (i + 1), f) for i, f in enumerate(pl_files)],
        key=lambda x: x[0], reverse=True
    )
    for slot, f in pairs:
        songs.append({
            'id':    f'm{slot}',
            'group': 'matzad',
            'slot':  slot,
            'label': _label_from_filename(f, r'^\d+_pl\d+_'),
        })
    for i, f in enumerate(pa_files, start=1):
        songs.append({
            'id':    f'p{i}',
            'group': 'palash',
            'slot':  i,
            'label': _label_from_filename(f, r'^\d+_pa\d+_'),
        })
    return jsonify({'show_id': show_id, 'songs': songs})


@app.route('/api/polls', methods=['POST'])
def api_poll_create():
    """Admin: create a poll from a matzad episode's songs."""
    data = request.get_json(silent=True) or {}
    show_id = (data.get('matzad_show_id') or '').strip()
    title   = (data.get('title') or '').strip()
    songs_in = data.get('songs') or []  # list of {id, group, slot, label}

    if not show_id:
        return jsonify({'error': 'matzad_show_id required'}), 400
    if not songs_in:
        return jsonify({'error': 'songs required'}), 400

    # Validate each song has needed fields and dedupe ids
    seen_ids = set()
    songs = []
    for s in songs_in:
        sid   = (s.get('id') or '').strip()
        label = (s.get('label') or '').strip()
        group = s.get('group')
        if not sid or sid in seen_ids:
            return jsonify({'error': 'invalid or duplicate song id'}), 400
        if group not in ('matzad', 'palash'):
            return jsonify({'error': f'invalid group for {sid}'}), 400
        if not label:
            return jsonify({'error': f'empty label for {sid}'}), 400
        seen_ids.add(sid)
        songs.append({
            'id':          sid,
            'group':       group,
            'slot':        s.get('slot'),
            'label':       label,
            'spotify_url': (s.get('spotify_url') or '').strip() or None,  # admin override (rare)
            'youtube_url': (s.get('youtube_url') or '').strip() or None,
        })

    # Resolve missing spotify_url via Client Credentials search (best-effort, parallel)
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        to_resolve = [(i, s) for i, s in enumerate(songs) if not s.get('spotify_url')]
        if to_resolve:
            from concurrent.futures import ThreadPoolExecutor
            def _resolve_one(pair):
                idx, s = pair
                return idx, _spotify_search_track(s['label'])
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for idx, url in ex.map(_resolve_one, to_resolve, timeout=30):
                        if url:
                            songs[idx]['spotify_url'] = url
                resolved = sum(1 for s in songs if s.get('spotify_url'))
                print(f"[Poll] Spotify lookup: {resolved}/{len(songs)} resolved", flush=True)
            except Exception as e:
                print(f"[Poll] Spotify resolver error: {e}", flush=True)

    import secrets as _secrets
    poll_id = _secrets.token_urlsafe(12)

    if not title:
        # default title: episode date if possible
        schedule = load_schedule()
        ep = next((x for x in schedule if x.get('id') == show_id), None)
        if ep and ep.get('scheduled_time'):
            try:
                dt = datetime.fromisoformat(ep['scheduled_time'])
                title = f"מצעד הרוק של ישראל — {dt.strftime('%d/%m/%Y')}"
            except Exception:
                title = 'מצעד הרוק של ישראל'
        else:
            title = 'מצעד הרוק של ישראל'

    # Voting window: default Thu 15:00 → next Tue 19:00, admin can override.
    opens_default, closes_default = _calc_default_voting_window()
    opens_in  = (data.get('opens_at')  or '').strip()
    closes_in = (data.get('closes_at') or '').strip()
    try:
        opens_at = datetime.fromisoformat(opens_in)  if opens_in  else opens_default
    except Exception:
        opens_at = opens_default
    try:
        closes_at = datetime.fromisoformat(closes_in) if closes_in else closes_default
    except Exception:
        closes_at = closes_default
    if closes_at <= opens_at:
        return jsonify({'error': 'closes_at must be after opens_at'}), 400

    poll = {
        'id':             poll_id,
        'title':          title,
        'matzad_show_id': show_id,
        'songs':          songs,
        'max_votes':      5,
        'open':           True,                       # legacy flag, kept for compat
        'opens_at':       opens_at.isoformat(),
        'closes_at':      closes_at.isoformat(),
        'created_at':     datetime.now().isoformat(),
        'closed_at':      None,
    }
    with _polls_lock:
        polls = _load_polls()
        polls.append(poll)
        _save_polls(polls)

    url = f"{ZEROCK_PUBLIC_URL}/poll/{poll_id}"
    print(f"[Poll] Created '{title}' → {poll_id} ({len(songs)} songs)", flush=True)
    return jsonify({'ok': True, 'poll': poll, 'url': url})


@app.route('/api/polls', methods=['GET'])
def api_poll_list():
    """Admin: list all polls with vote counts and effective open state."""
    polls = _load_polls()
    votes = _load_poll_votes()
    now   = datetime.now()
    counts = {}
    for v in votes:
        counts[v['poll_id']] = counts.get(v['poll_id'], 0) + 1
    return jsonify([{
        **p,
        'open':       _poll_is_open(p, now),   # derived from schedule
        'vote_count': counts.get(p['id'], 0),
        'url':        f"{ZEROCK_PUBLIC_URL}/poll/{p['id']}",
    } for p in polls])


@app.route('/api/polls/history')
def api_polls_history():
    """Admin: all past polls with final ranked results (newest first)."""
    polls      = _load_polls()
    all_votes  = _load_poll_votes()
    now        = datetime.now()

    def _sort_key(p):
        for k in ('closes_at', 'created_at', 'opens_at'):
            v = p.get(k)
            if v:
                try: return datetime.fromisoformat(v)
                except Exception: pass
        return datetime.min

    result = []
    for poll in sorted(polls, key=_sort_key, reverse=True):
        is_current = _poll_is_open(poll, now)

        # Use existing snapshot if available, else compute from votes
        snap = poll.get('public_snapshot')
        if snap:
            ranked = snap.get('results', [])
            total  = snap.get('total_voters', 0)
            taken_at = snap.get('taken_at', '')
        else:
            votes  = [v for v in all_votes if v['poll_id'] == poll['id']]
            tally  = {s['id']: 0 for s in poll.get('songs', [])}
            for v in votes:
                for sid in (v.get('song_ids') or []):
                    if sid in tally:
                        tally[sid] += 1
            _tb_order = _get_tiebreak_order(poll)
            ranked = sorted(
                [{**s, 'votes': tally[s['id']], '_r': _tb_order.get(s['id'], 0.5)} for s in poll.get('songs', [])],
                key=lambda x: (-x['votes'], x['_r'])
            )
            # Add movement
            prev_pos   = poll.get('prev_positions') or {}
            song_weeks = poll.get('song_weeks') or {}
            for rank, song in enumerate(ranked, 1):
                sid = song['id']
                pp  = prev_pos.get(sid)
                if pp is None or pp in ('new',):
                    song['movement'] = 'new'
                elif pp == 'palash':
                    song['movement'] = 'palash'
                else:
                    delta = int(pp) - rank
                    song['movement'] = f'+{delta}' if delta > 0 else (str(delta) if delta < 0 else '0')
                song['weeks'] = song_weeks.get(sid)
            total    = len(votes)
            taken_at = poll.get('closes_at', '')

        result.append({
            'id':         poll['id'],
            'title':      poll.get('title', ''),
            'closes_at':  poll.get('closes_at', ''),
            'opens_at':   poll.get('opens_at', ''),
            'is_current': is_current,
            'total_voters': total,
            'taken_at':   taken_at,
            'results':    ranked,
        })

    return jsonify(result)


@app.route('/api/polls/<poll_id>/results')
def api_poll_results(poll_id):
    """Admin: tally per song."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'not found'}), 404
    votes = [v for v in _load_poll_votes() if v['poll_id'] == poll_id]
    tally = {s['id']: 0 for s in poll['songs']}
    for v in votes:
        for sid in (v.get('song_ids') or []):
            if sid in tally:
                tally[sid] += 1
    results = sorted([
        {**s, 'votes': tally[s['id']]} for s in poll['songs']
    ], key=lambda x: (-x['votes'], -((x.get('slot') or 0) if x['group']=='matzad' else 0)))
    return jsonify({
        'poll':         poll,
        'total_voters': len(votes),
        'results':      results,
    })


@app.route('/api/polls/<poll_id>/close', methods=['POST'])
def api_poll_close(poll_id):
    """Admin: close voting now (sets closes_at to now)."""
    now = datetime.now()
    with _polls_lock:
        polls = _load_polls()
        poll  = next((p for p in polls if p['id'] == poll_id), None)
        if not poll:
            return jsonify({'error': 'not found'}), 404
        poll['open']       = False
        poll['closes_at']  = now.isoformat()
        poll['closed_at']  = now.isoformat()
        _save_polls(polls)
    return jsonify({'ok': True})


@app.route('/api/polls/<poll_id>/reopen', methods=['POST'])
def api_poll_reopen(poll_id):
    """Admin: extend voting to the next Thu→Wed window (or current week if active)."""
    now = datetime.now()
    opens_default, closes_default = _calc_default_voting_window(now)
    with _polls_lock:
        polls = _load_polls()
        poll  = next((p for p in polls if p['id'] == poll_id), None)
        if not poll:
            return jsonify({'error': 'not found'}), 404
        # If we're inside a default window, extend to its end; else next window
        poll['open']       = True
        poll['opens_at']   = opens_default.isoformat()
        poll['closes_at']  = closes_default.isoformat()
        poll['closed_at']  = None
        _save_polls(polls)
    return jsonify({'ok': True, 'opens_at': poll['opens_at'], 'closes_at': poll['closes_at']})


@app.route('/api/polls/<poll_id>/schedule', methods=['POST'])
def api_poll_set_schedule(poll_id):
    """Admin: update opens_at / closes_at directly."""
    data = request.get_json(silent=True) or {}
    opens_in  = (data.get('opens_at')  or '').strip()
    closes_in = (data.get('closes_at') or '').strip()
    if not opens_in or not closes_in:
        return jsonify({'error': 'opens_at and closes_at required'}), 400
    try:
        opens_at  = datetime.fromisoformat(opens_in)
        closes_at = datetime.fromisoformat(closes_in)
    except Exception:
        return jsonify({'error': 'invalid datetime'}), 400
    if closes_at <= opens_at:
        return jsonify({'error': 'closes_at must be after opens_at'}), 400
    with _polls_lock:
        polls = _load_polls()
        poll  = next((p for p in polls if p['id'] == poll_id), None)
        if not poll:
            return jsonify({'error': 'not found'}), 404
        poll['opens_at']  = opens_at.isoformat()
        poll['closes_at'] = closes_at.isoformat()
        poll['closed_at'] = None
        _save_polls(polls)
    return jsonify({'ok': True})


@app.route('/api/voting-window-default')
def api_voting_window_default():
    """Admin helper: return the default Thu→Wed window from now."""
    o, c = _calc_default_voting_window()
    return jsonify({'opens_at': o.isoformat(), 'closes_at': c.isoformat()})


@app.route('/api/polls/<poll_id>', methods=['DELETE'])
def api_poll_delete(poll_id):
    """Admin: delete a poll and its votes."""
    with _polls_lock:
        polls = _load_polls()
        new_polls = [p for p in polls if p['id'] != poll_id]
        if len(new_polls) == len(polls):
            return jsonify({'error': 'not found'}), 404
        _save_polls(new_polls)
    with _votes_lock:
        votes = _load_poll_votes()
        _save_poll_votes([v for v in votes if v['poll_id'] != poll_id])
    return jsonify({'ok': True})


@app.route('/poll/<poll_id>/reset-cookie')
def poll_reset_cookie(poll_id):
    """Clear the 'already voted' browser cookie so the user can vote again."""
    from flask import redirect
    resp = redirect(f'/poll/{poll_id}')
    resp.delete_cookie(f'voted_{poll_id}')
    return resp


@app.route('/poll/<poll_id>')
def poll_vote_page(poll_id):
    """Public: voting form."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return render_template('poll_vote.html', invalid=True, poll=None, already_voted=False)
    # Augment with effective open state for template
    now = datetime.now()
    poll = {**poll, 'open': _poll_is_open(poll, now)}
    already = request.cookies.get(f'voted_{poll_id}') == '1'
    import random as _random
    vote_songs = list(poll.get('songs') or [])
    _random.shuffle(vote_songs)
    return render_template('poll_vote.html', invalid=False, poll=poll,
                           already_voted=already, vote_songs=vote_songs)


_RESULTS_PASSWORD   = 'YudaKaka2026!'
_RESULTS_AUTH_TOKEN = __import__('hashlib').sha256(
    ('zerock_results:' + _RESULTS_PASSWORD).encode()).hexdigest()[:32]


@app.route('/poll/<poll_id>/results', methods=['GET', 'POST'])
def poll_results_page(poll_id):
    """Public: live results page for a poll (password protected)."""
    # ── Auth gate ─────────────────────────────────────────────────────────────
    if request.method == 'POST':
        pw = (request.form.get('password') or '').strip()
        if pw == _RESULTS_PASSWORD:
            from flask import redirect as _redirect
            resp = _redirect(f'/poll/{poll_id}/results')
            resp.set_cookie('results_auth', _RESULTS_AUTH_TOKEN,
                            max_age=30 * 86400, samesite='Lax', httponly=True)
            return resp
        return render_template('poll_results.html', invalid=False, poll=None,
                               auth_required=True, auth_error=True)
    if request.cookies.get('results_auth') != _RESULTS_AUTH_TOKEN:
        return render_template('poll_results.html', invalid=False, poll=None,
                               auth_required=True, auth_error=False)
    # ── Authenticated — proceed ───────────────────────────────────────────────
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return render_template('poll_results.html', invalid=True, poll=None)
    now  = datetime.now()
    poll = {**poll, 'open': _poll_is_open(poll, now)}
    votes = [v for v in _load_poll_votes() if v['poll_id'] == poll_id]
    tally = {s['id']: 0 for s in poll['songs']}
    for v in votes:
        for sid in (v.get('song_ids') or []):
            if sid in tally:
                tally[sid] += 1
    # Sort ALL songs by votes descending — stable tiebreak for equal votes
    _tb_order = _get_tiebreak_order(poll)
    results = sorted([
        {**s, 'votes': tally[s['id']], '_r': _tb_order.get(s['id'], 0.5)} for s in poll['songs']
    ], key=lambda x: (-x['votes'], x['_r']))
    # Compute movement vs previous chart if prev_positions is provided
    prev_pos   = poll.get('prev_positions') or {}
    song_weeks = poll.get('song_weeks') or {}
    for curr_rank, song in enumerate(results, start=1):
        sid = song['id']
        pp  = prev_pos.get(sid)
        if pp is None or pp == 'new':
            song['movement'] = 'new'
        elif pp == 'palash':
            song['movement'] = 'palash'
        else:
            delta = int(pp) - curr_rank
            if delta > 0:
                song['movement'] = f'+{delta}'
            elif delta < 0:
                song['movement'] = str(delta)
            else:
                song['movement'] = '0'
        song['weeks'] = song_weeks.get(sid)
    # Compute special badges
    badge_aliya_id  = None  # העלייה הגבוהה — biggest rise
    badge_yerida_id = None  # הירידה הגבוהה — biggest drop
    badge_vatik_id  = None  # השיר הותיק   — most weeks on chart
    max_rise = 0; max_drop = 0; max_weeks = 0
    for song in results[:20]:
        mv    = song.get('movement', '')
        weeks = song.get('weeks') or 0
        if mv and mv not in ('new', 'palash', '0'):
            if mv.startswith('+'):
                rise = int(mv[1:])
                if rise > max_rise:
                    max_rise = rise; badge_aliya_id = song['id']
            elif mv.startswith('-'):
                drop = int(mv[1:])
                if drop > max_drop:
                    max_drop = drop; badge_yerida_id = song['id']
        if weeks > max_weeks:
            max_weeks = weeks; badge_vatik_id = song['id']
    max_votes_any  = max((r['votes'] for r in results), default=0)
    any_votes      = max_votes_any > 0
    vote_url       = f"{ZEROCK_PUBLIC_URL}/poll/{poll_id}"
    return render_template('poll_results.html',
        invalid=False,
        poll=poll,
        results=results,
        total_voters=len(votes),
        total_songs=len(poll['songs']),
        max_votes_any=max_votes_any,
        any_votes=any_votes,
        vote_url=vote_url,
        next_palash=poll.get('next_palash') or [],
        next_palash_comments=poll.get('next_palash_comments') or [],
        badge_aliya_id=badge_aliya_id,
        badge_yerida_id=badge_yerida_id,
        badge_vatik_id=badge_vatik_id,
    )


@app.route('/api/polls/<poll_id>/next-palash/<int:np_index>/comment', methods=['POST'])
def api_next_palash_comment(poll_id, np_index):
    """Authenticated: save a free-text comment for a next-week palash song by position."""
    if request.cookies.get('results_auth') != _RESULTS_AUTH_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401
    data    = request.get_json(force=True) or {}
    comment = data.get('comment', '').strip()
    polls   = _load_polls()
    poll    = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'poll not found'}), 404
    next_palash = poll.get('next_palash') or []
    if np_index < 0 or np_index >= len(next_palash):
        return jsonify({'error': 'index out of range'}), 400
    comments = poll.setdefault('next_palash_comments', [''] * len(next_palash))
    # Ensure list is long enough
    while len(comments) <= np_index:
        comments.append('')
    comments[np_index] = comment
    _save_polls(polls)
    print(f"[Poll] next_palash[{np_index}] comment saved in {poll_id}: {comment!r}", flush=True)
    return jsonify({'ok': True, 'comment': comment})


def _public_results_window():
    """Return True if we're currently in the Thursday 15:00 → Wednesday 19:00 window
    (Israel server time) when public chart results should be visible."""
    now = datetime.now()
    wd  = now.weekday()   # 0=Mon … 3=Thu … 6=Sun
    h   = now.hour
    if wd == 3:   return h >= 15          # Thursday 15:00+
    if wd == 2:   return h < 19           # Wednesday before 19:00
    return wd in (4, 5, 6, 0, 1)          # Fri / Sat / Sun / Mon / Tue


def _take_public_snapshot(poll_id=None):
    """Freeze the current vote tally for a poll into public_snapshot.
    If poll_id is given, snapshots that poll; otherwise snapshots the most
    recently closed poll (or most recent poll if all open).
    Called every Thursday at 15:00 when the matzad upload completes."""
    polls = _load_polls()
    if not polls:
        print('[PubSnapshot] No polls found', flush=True)
        return
    if poll_id:
        poll = next((p for p in polls if p['id'] == poll_id), None)
        if not poll:
            print(f'[PubSnapshot] Poll {poll_id} not found', flush=True)
            return
    else:
        # Find most recent CLOSED poll; fall back to newest poll overall
        def _dt(p):
            for k in ('closed_at', 'closes_at', 'created_at', 'opens_at'):
                v = p.get(k)
                if v:
                    try:
                        dt = datetime.fromisoformat(v)
                        return dt.replace(tzinfo=None)  # normalise for comparison
                    except Exception:
                        pass
            return datetime.min
        closed = [p for p in polls if not p.get('open', True)]
        pool   = closed if closed else polls
        poll   = max(pool, key=_dt)

    votes = [v for v in _load_poll_votes() if v['poll_id'] == poll['id']]
    tally = {s['id']: 0 for s in poll['songs']}
    for v in votes:
        for sid in (v.get('song_ids') or []):
            if sid in tally:
                tally[sid] += 1

    # Sort with stable tiebreak (same as team results page)
    _tb_order = _get_tiebreak_order(poll)
    results_raw = sorted(
        [{**s, 'votes': tally[s['id']], '_r': _tb_order.get(s['id'], 0.5)} for s in poll['songs']],
        key=lambda x: (-x['votes'], x['_r'])
    )

    # Annotate with movement + weeks
    prev_pos   = poll.get('prev_positions') or {}
    song_weeks = poll.get('song_weeks') or {}
    results_out = []
    badge_aliya_id  = None
    badge_yerida_id = None
    badge_vatik_id  = None
    max_rise = 0; max_drop = 0; max_weeks = 0

    for curr_rank, song in enumerate(results_raw, start=1):
        sid = song['id']
        pp  = prev_pos.get(sid)
        if pp is None or pp == 'new':
            movement = 'new'
        elif pp == 'palash':
            movement = 'palash'
        else:
            delta = int(pp) - curr_rank
            movement = f'+{delta}' if delta > 0 else (str(delta) if delta < 0 else '0')
        weeks = song_weeks.get(sid)
        entry = {
            'id':          sid,
            'group':       song.get('group', 'matzad'),
            'slot':        song.get('slot'),
            'label':       song.get('label', ''),
            'spotify_url': song.get('spotify_url'),
            'youtube_url': song.get('youtube_url'),
            'votes':       song['votes'],
            'movement':    movement,
            'weeks':       weeks,
        }
        # Compute badges (matzad-only songs in top 20)
        if song.get('group') != 'palash' and curr_rank <= 20:
            if movement not in ('new', 'palash', '0') and movement:
                if movement.startswith('+'):
                    rise = int(movement[1:])
                    if rise > max_rise:
                        max_rise = rise; badge_aliya_id = sid
                elif movement.startswith('-'):
                    drop = abs(int(movement))
                    if drop > max_drop:
                        max_drop = drop; badge_yerida_id = sid
        wk = weeks or 0
        if wk > max_weeks:
            max_weeks = wk; badge_vatik_id = sid
        results_out.append(entry)

    snapshot = {
        'taken_at':        datetime.now().isoformat(),
        'poll_id':         poll['id'],
        'poll_title':      poll.get('title', ''),
        'total_voters':    len(votes),
        'results':         results_out,
        'badge_aliya_id':  badge_aliya_id,
        'badge_yerida_id': badge_yerida_id,
        'badge_vatik_id':  badge_vatik_id,
    }
    poll['public_snapshot'] = snapshot
    _save_polls(polls)
    print(f"[PubSnapshot] ✓ Snapshot taken for poll '{poll['id']}' "
          f"({len(votes)} voters, {len(results_out)} songs)", flush=True)
    return snapshot


@app.route('/poll/<poll_id>/public-results')
def poll_public_results_page(poll_id):
    """Public (no password): last chart results — visible Thursday 15:00 → Wednesday 19:00."""
    in_window = _public_results_window()

    polls    = _load_polls()
    poll     = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return render_template('poll_public_results.html',
                               invalid=True, in_window=in_window, snapshot=None)

    snapshot = poll.get('public_snapshot')
    return render_template('poll_public_results.html',
                           invalid=False,
                           in_window=in_window,
                           snapshot=snapshot,
                           poll=poll)


@app.route('/api/polls/<poll_id>/take-snapshot', methods=['POST'])
def api_poll_take_snapshot(poll_id):
    """Admin: manually trigger a public-results snapshot for a specific poll."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'poll not found'}), 404
    snap = _take_public_snapshot(poll_id=poll_id)
    return jsonify({'ok': True, 'snapshot': snap})


@app.route('/api/polls/<poll_id>/next-palash', methods=['PUT'])
def api_poll_set_next_palash(poll_id):
    """Admin: set the 5 next-week הפינה לשיפוטכם songs."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'poll not found'}), 404
    data  = request.get_json(silent=True) or {}
    songs = data.get('songs') or []
    # Keep up to 5, strip whitespace, drop empty strings
    songs = [s.strip() for s in songs if isinstance(s, str) and s.strip()][:5]
    poll['next_palash'] = songs
    _save_polls(polls)
    return jsonify({'ok': True, 'next_palash': songs})


@app.route('/api/polls/weekly-renew', methods=['POST'])
def api_polls_weekly_renew():
    """Admin: close current poll and open next week's poll (top-20 + next_palash)."""
    import secrets as _sec2
    from datetime import timezone, timedelta as _td2

    polls = _load_polls()
    if not polls:
        return jsonify({'error': 'no polls found'}), 404

    # Find most recent poll by closes_at
    def _parse_dt2(s):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    old_poll = max(polls, key=lambda p: _parse_dt2(p.get('closes_at', '')))

    # Compute vote tally for old poll
    votes = _load_poll_votes()
    tally = {s['id']: 0 for s in old_poll['songs']}
    for v in votes:
        if v.get('poll_id') == old_poll['id']:
            for sid in (v.get('song_ids') or v.get('choices') or []):
                if sid in tally:
                    tally[sid] += 1

    # Sort songs by votes descending with stable tiebreak (persisted per-poll)
    _tb_order = _get_tiebreak_order(old_poll)
    ranked = sorted(old_poll['songs'],
                    key=lambda s: (-tally[s['id']], _tb_order.get(s['id'], 0.5)))

    # Top 20 become new matzad
    new_matzad = ranked[:20]

    # Palash songs for next week
    next_palash_labels = [
        s.strip() for s in (old_poll.get('next_palash') or [])
        if isinstance(s, str) and s.strip()
    ][:5]

    # Old song_weeks and prev_positions references
    old_song_weeks = old_poll.get('song_weeks') or {}

    # Build rank map: old_id -> rank_in_current_week (1-based int for all songs)
    old_rank_map = {}
    for i, s in enumerate(ranked[:20]):
        old_rank_map[s['id']] = i + 1

    # Build new songs, prev_positions, song_weeks
    new_songs = []
    new_prev_positions = {}
    new_song_weeks = {}

    for i, s in enumerate(new_matzad):
        new_id = f's{i + 1:02d}'
        new_songs.append({
            'id':          new_id,
            'group':       'matzad',
            'slot':        i + 1,
            'label':       s['label'],
            'spotify_url': s.get('spotify_url'),
            'youtube_url': s.get('youtube_url'),
        })
        prev_pos = old_rank_map.get(s['id'])
        if prev_pos is not None:
            new_prev_positions[new_id] = prev_pos
        # If not in map: brand-new song (prev absent = 'new' on results page)
        new_song_weeks[new_id] = (old_song_weeks.get(s['id']) or 0) + 1

    for i, label in enumerate(next_palash_labels):
        new_id = f's{21 + i:02d}'
        new_songs.append({
            'id':          new_id,
            'group':       'palash',
            'slot':        i + 1,
            'label':       label,
            'spotify_url': None,
            'youtube_url': None,
        })
        # Palash entries are new to the chart — no prev_position entry
        new_song_weeks[new_id] = 1

    # Next Tuesday 19:00 Israel time (UTC+3)
    israel_tz = timezone(_td2(hours=3))
    now_israel = datetime.now(israel_tz)
    days_until_tue = (1 - now_israel.weekday()) % 7
    if days_until_tue == 0:
        days_until_tue = 7  # Already Tuesday → next Tuesday
    next_tue = (now_israel + _td2(days=days_until_tue)).replace(
        hour=19, minute=0, second=0, microsecond=0)
    closes_at_str = next_tue.strftime('%Y-%m-%dT%H:%M:%S+03:00')
    opens_at_str  = now_israel.strftime('%Y-%m-%dT%H:%M:%S+03:00')

    # Close old poll
    old_poll['open']      = False
    old_poll['closed_at'] = opens_at_str

    # Create new poll — increment the chart number in the title and update the date
    import re as _re
    old_title   = old_poll.get('title', 'מצעד הרוק הישראלי השבועי של רדיו זה רוק 306')
    old_num_m   = _re.search(r'(\d{3,})', old_title)
    new_num     = (int(old_num_m.group(1)) + 1) if old_num_m else ''
    # Date = next Thursday (the broadcast date for this new poll)
    next_thursday = now_israel + _td2(days=7)
    new_date    = next_thursday.strftime('%d/%m/%Y')
    # Replace old number and date in title
    new_title   = _re.sub(r'\d{3,}', str(new_num), old_title, count=1)
    new_title   = _re.sub(r'\d{2}/\d{2}/\d{4}', new_date, new_title)
    if not _re.search(r'\d{2}/\d{2}/\d{4}', old_title):
        new_title = new_title.rstrip() + f' — {new_date}'

    new_poll_id = _sec2.token_urlsafe(12)
    new_poll = {
        'id':             new_poll_id,
        'title':          new_title,
        'matzad_show_id': None,
        'songs':          new_songs,
        'max_votes':      old_poll.get('max_votes', 5),
        'open':           True,
        'opens_at':       opens_at_str,
        'closes_at':      closes_at_str,
        'created_at':     opens_at_str,
        'closed_at':      None,
        'prev_positions': new_prev_positions,
        'song_weeks':     new_song_weeks,
        'staff':          old_poll.get('staff', ''),
        'next_palash':    [],
    }

    polls.append(new_poll)
    _save_polls(polls)

    # ── Snapshot: freeze old poll results for public results page ────────────
    threading.Thread(target=_take_public_snapshot, daemon=True).start()

    # ── Update WP vote button to new poll URL ────────────────────────────────
    def _update_wp_vote_snippet(pid):
        try:
            vote_url  = f"{ZEROCK_PUBLIC_URL}/poll/{pid}"
            php_code  = (
                "add_action('wp_footer', function() {\n"
                "    if (!is_page('rock-chart')) return;\n"
                "    echo '<script>\n"
                "(function(){\n"
                '    var btn = document.querySelector("a.chart-top-button");\n'
                f'    if (btn) {{ btn.href = "{vote_url}"; }}\n'
                "})();\n"
                "</script>';\n"
                "});"
            )
            auth_wp  = (WP_USERNAME, WP_APP_PASS)
            hdrs     = {'Content-Type': 'application/json'}
            r1 = _requests.patch(
                f"{WP_REST_BASE}/code-snippets/v1/snippets/55",
                json={'code': php_code, 'scope': 'front-end'},
                auth=auth_wp, headers=hdrs, timeout=15
            )
            _requests.post(
                f"{WP_REST_BASE}/code-snippets/v1/snippets/55/activate",
                auth=auth_wp, headers=hdrs, timeout=15
            )
            print(f"[WeeklyRenew] WP vote snippet updated → {vote_url} ({r1.status_code})", flush=True)
        except Exception as e:
            print(f"[WeeklyRenew] WP snippet update failed: {e}", flush=True)
    threading.Thread(target=_update_wp_vote_snippet, args=(new_poll_id,), daemon=True).start()

    # ── Send invite emails to previous voters ────────────────────────────────
    _old_poll_snapshot = dict(old_poll)  # capture before anything mutates it
    threading.Thread(
        target=_send_weekly_vote_invites,
        args=(_old_poll_snapshot, new_poll_id),
        daemon=True,
    ).start()

    # ── Update Spotify playlists ──────────────────────────────────────────────
    import re as _re2
    _chart_num_m = _re2.search(r'(\d{3,})', new_title)
    _chart_num   = _chart_num_m.group(1) if _chart_num_m else ''
    _chart_date  = now_israel.strftime('%d/%m/%Y')

    def _update_spotify_playlists(matzad_songs, palash_songs, chart_num, chart_date):
        """Replace Palash playlist and Top-20 playlist tracks and descriptions on Spotify."""
        try:
            description = f'מצעד הרוק של ישראל מספר {chart_num} מעודכן לתאריך {chart_date}'

            # Resolve Spotify URLs for songs that don't already have them
            def _ensure_uris(songs):
                uris = []
                for s in songs:
                    url = s.get('spotify_url')
                    if not url and SPOTIFY_CLIENT_ID:
                        url = _spotify_search_track(s['label'])
                    uri = _spotify_track_uri_from_url(url)
                    if uri:
                        uris.append(uri)
                return uris

            palash_uris = _ensure_uris(palash_songs)
            top20_uris  = _ensure_uris(matzad_songs)

            if palash_uris:
                _spotify_replace_playlist(SPOTIFY_PALASH_PLAYLIST, palash_uris)
            else:
                print("[WeeklyRenew] No Palash Spotify URIs — skipping palash playlist update", flush=True)
            _spotify_update_playlist_description(SPOTIFY_PALASH_PLAYLIST, description)

            if top20_uris:
                _spotify_replace_playlist(SPOTIFY_TOP20_PLAYLIST, top20_uris)
            else:
                print("[WeeklyRenew] No Top-20 Spotify URIs — skipping top-20 playlist update", flush=True)
            _spotify_update_playlist_description(SPOTIFY_TOP20_PLAYLIST, description)

            # Update WP rock-chart page Spotify links
            _spotify_update_wp_links(SPOTIFY_TOP20_PLAYLIST, SPOTIFY_PALASH_PLAYLIST)

        except Exception as e:
            print(f"[WeeklyRenew] Spotify playlist update error: {e}", flush=True)

    threading.Thread(
        target=_update_spotify_playlists,
        args=(new_songs[:20], new_songs[20:], _chart_num, _chart_date),
        daemon=True,
    ).start()

    # Send welcome emails to Palash artists
    threading.Thread(
        target=_send_palash_welcome_emails,
        args=(new_songs[20:],),
        daemon=True,
    ).start()

    return jsonify({
        'ok':           True,
        'old_poll_id':  old_poll['id'],
        'new_poll_id':  new_poll_id,
        'new_closes_at': closes_at_str,
        'matzad_songs': [s['label'] for s in new_songs[:20]],
        'palash_songs': [s['label'] for s in new_songs[20:]],
    })


# ── Spotify OAuth one-time setup ──────────────────────────────────────────────

@app.route('/api/spotify/auth')
def api_spotify_auth():
    """Redirect to Spotify authorization page (one-time admin setup)."""
    if not SPOTIFY_CLIENT_ID:
        return 'SPOTIFY_CLIENT_ID not configured', 500
    import urllib.parse
    params = urllib.parse.urlencode({
        'client_id':     SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri':  f"{ZEROCK_PUBLIC_URL}/api/spotify/callback",
        'scope':         'playlist-modify-public playlist-modify-private',
        'show_dialog':   'true',
    })
    return redirect(f'https://accounts.spotify.com/authorize?{params}')


@app.route('/api/spotify/callback')
def api_spotify_callback():
    """Receive Spotify OAuth callback and display the refresh token."""
    import base64, urllib.request, urllib.parse
    code  = request.args.get('code')
    error = request.args.get('error')
    if error or not code:
        return f'Spotify auth error: {error}', 400
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return 'Spotify credentials not configured', 500
    try:
        creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        data  = urllib.parse.urlencode({
            'grant_type':   'authorization_code',
            'code':          code,
            'redirect_uri':  f"{ZEROCK_PUBLIC_URL}/api/spotify/callback",
        }).encode()
        req = urllib.request.Request(
            'https://accounts.spotify.com/api/token',
            data=data,
            headers={
                'Authorization': f'Basic {creds}',
                'Content-Type':  'application/x-www-form-urlencoded',
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            payload = json.loads(r.read())
        refresh_token = payload.get('refresh_token', '(none returned)')
        return (
            f"<pre style='font-size:18px;padding:24px'>"
            f"✅ Spotify OAuth success!\n\n"
            f"Add this to your start script:\n\n"
            f"export SPOTIFY_REFRESH_TOKEN={refresh_token}\n\n"
            f"Then restart the radio service.\n</pre>"
        )
    except Exception as e:
        return f'Spotify token exchange failed: {e}', 500


@app.route('/api/poll/<poll_id>/send-code', methods=['POST'])
def api_poll_send_code(poll_id):
    """Public: send 6-digit email verification code."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'invalid poll'}), 404
    if not _poll_is_open(poll, datetime.now()):
        return jsonify({'error': 'ההצבעה סגורה'}), 403

    data  = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'כתובת מייל לא תקינה'}), 400

    # Reject if already voted with this email
    votes = _load_poll_votes()
    if any(v['poll_id'] == poll_id and v.get('email', '').lower() == email for v in votes):
        return jsonify({'error': 'כבר הצבעת בסקר זה עם כתובת מייל זו'}), 409

    now = datetime.now()
    import random as _rand, secrets as _sec
    with _poll_codes_lock:
        codes = _load_poll_codes()
        # Rate-limit: max 3 send-code requests per email per poll per hour
        recent = [c for c in codes
                  if c['poll_id'] == poll_id and c['email'] == email
                  and datetime.fromisoformat(c['created_at']) > now - timedelta(hours=1)]
        if len(recent) >= 3:
            return jsonify({'error': 'יותר מדי בקשות. נסה שוב בעוד שעה'}), 429
        # Invalidate previous unverified codes for this email+poll
        codes = [c for c in codes
                 if not (c['poll_id'] == poll_id and c['email'] == email and not c.get('verified'))]
        code = f"{_rand.randint(0, 999999):06d}"
        codes.append({
            'poll_id':    poll_id,
            'email':      email,
            'code':       code,
            'created_at': now.isoformat(),
            'expires_at': (now + timedelta(minutes=10)).isoformat(),
            'verified':   False,
            'token':      None,
        })
        _save_poll_codes(codes)

    try:
        threading.Thread(target=_send_poll_verification_email,
                         args=(email, code, poll['title']), daemon=True).start()
    except Exception as e:
        print(f"[Poll] Email thread error: {e}", flush=True)
        return jsonify({'error': 'שגיאה בשליחת המייל. נסה שוב'}), 500
    return jsonify({'ok': True})


@app.route('/api/poll/<poll_id>/verify-code', methods=['POST'])
def api_poll_verify_code(poll_id):
    """Public: verify 6-digit code; return a short-lived verify_token."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'invalid poll'}), 404

    data  = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code  = str(data.get('code') or '').strip()
    if not email or not code:
        return jsonify({'error': 'מייל וקוד נדרשים'}), 400

    now = datetime.now()
    import secrets as _sec
    with _poll_codes_lock:
        codes = _load_poll_codes()
        entry = next((c for c in codes
                      if c['poll_id'] == poll_id and c['email'] == email
                      and not c.get('verified') and not c.get('vote_submitted')), None)
        if not entry:
            return jsonify({'error': 'לא נמצא קוד. שלח קוד חדש'}), 400
        if datetime.fromisoformat(entry['expires_at']) < now:
            return jsonify({'error': 'הקוד פג תוקף. שלח קוד חדש'}), 400
        if entry['code'] != code:
            return jsonify({'error': 'קוד שגוי'}), 400
        token = _sec.token_urlsafe(24)
        entry['verified']    = True
        entry['token']       = token
        entry['verified_at'] = now.isoformat()
        _save_poll_codes(codes)

    return jsonify({'ok': True, 'verify_token': token})


@app.route('/api/poll/<poll_id>/vote', methods=['POST'])
def api_poll_vote(poll_id):
    """Public: submit a ballot of exactly 5 song_ids (requires email verification)."""
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return jsonify({'error': 'invalid poll'}), 404
    now = datetime.now()
    if not _poll_is_open(poll, now):
        # Distinguish "not yet" vs "already closed" for clearer error text
        try:
            opens = datetime.fromisoformat(poll.get('opens_at') or '')
            if now < opens:
                return jsonify({'error': 'ההצבעה עדיין לא נפתחה'}), 403
        except Exception:
            pass
        return jsonify({'error': 'ההצבעה סגורה'}), 403

    data         = request.get_json(silent=True) or {}
    song_ids     = data.get('song_ids') or []
    email        = (data.get('email') or '').strip().lower()
    verify_token = (data.get('verify_token') or '').strip()
    voter_name   = (data.get('name') or '').strip()[:120]

    # ── Email verification required ───────────────────────────────────────────
    if not email or not verify_token:
        return jsonify({'error': 'אימות מייל נדרש לפני הצבעה'}), 403

    now2 = datetime.now()
    with _poll_codes_lock:
        codes = _load_poll_codes()
        code_entry = next((c for c in codes
                           if c['poll_id'] == poll_id
                           and c['email'] == email
                           and c.get('verified')
                           and c.get('token') == verify_token
                           and not c.get('vote_submitted')), None)
        if not code_entry:
            return jsonify({'error': 'אימות לא תקין. אמת מחדש'}), 403
        # Token expires 1 hour after verification
        if datetime.fromisoformat(code_entry['verified_at']) < now2 - timedelta(hours=1):
            return jsonify({'error': 'פג תוקף האימות. אמת מחדש'}), 403
        # Claim token immediately to prevent double submission
        code_entry['vote_submitted'] = True
        _save_poll_codes(codes)

    # ── Validate song selection ───────────────────────────────────────────────
    if not isinstance(song_ids, list):
        return jsonify({'error': 'song_ids must be a list'}), 400
    n_required = poll.get('max_votes', 5)
    if len(song_ids) != n_required:
        return jsonify({'error': f'יש לבחור בדיוק {n_required} שירים'}), 400
    if len(set(song_ids)) != len(song_ids):
        return jsonify({'error': 'בחירות כפולות'}), 400
    valid_ids = {s['id'] for s in poll['songs']}
    if any(sid not in valid_ids for sid in song_ids):
        return jsonify({'error': 'מזהה שיר לא תקין'}), 400

    # ── Dedup: email (primary) + cookie + IP (secondary) ─────────────────────
    ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
          or request.remote_addr or '')
    with _votes_lock:
        votes = _load_poll_votes()
        if any(v['poll_id'] == poll_id and v.get('email', '').lower() == email for v in votes):
            return jsonify({'error': 'כבר הצבעת בסקר זה'}), 409
        votes.append({
            'poll_id':  poll_id,
            'email':    email,
            'name':     voter_name,
            'song_ids': song_ids,
            'ip':       ip,
            'ua':       (request.headers.get('User-Agent') or '')[:200],
            'voted_at': datetime.now().isoformat(),
        })
        _save_poll_votes(votes)

    resp = jsonify({'ok': True})
    resp.set_cookie(f'voted_{poll_id}', '1', max_age=90*86400, samesite='Lax')
    print(f"[Poll] Vote received for {poll_id} from {email} / {ip}", flush=True)
    return resp


# ─── Auto-generate next chart from poll votes ────────────────────────────────

def _compute_next_chart(poll_id):
    """From poll votes, build the next matzad chart (top-20 ranking + badge highlights).

    Rules (per user spec):
      • New entry (was in last week's פל״ש, now in 1–20)         → 'knisa_new'
      • Highest-placed new entry                                  → 'knisa'
      • Biggest decline (delta < 0) among matzad-origin songs     → 'yerida'
      • Biggest improvement (delta > 0) among matzad-origin songs → 'aliya'
        (delta = old_slot − new_slot; positive = moved up toward #1)

    Returns (result_dict, error_str_or_None).
    """
    polls = _load_polls()
    poll  = next((p for p in polls if p['id'] == poll_id), None)
    if not poll:
        return None, 'poll not found'

    schedule = load_schedule()
    src_id   = poll.get('matzad_show_id')
    src_show = next((s for s in schedule if s.get('id') == src_id), None)
    if not src_show:
        return None, 'source matzad episode no longer exists'

    # Tally votes
    all_votes = [v for v in _load_poll_votes() if v['poll_id'] == poll_id]
    tally = {s['id']: 0 for s in poll['songs']}
    for v in all_votes:
        for sid in (v.get('song_ids') or []):
            if sid in tally:
                tally[sid] += 1

    # Map song id → file path from the source episode (so we can reuse audio)
    file_map = {}
    src_pl_files = src_show.get('playlist_files') or []
    src_pl_slots = src_show.get('playlist_slots') or []
    for i, f in enumerate(src_pl_files):
        slot = src_pl_slots[i] if i < len(src_pl_slots) else (i + 1)
        file_map[f'm{slot}'] = f
    src_pa_files = src_show.get('palash_files') or []
    for i, f in enumerate(src_pa_files):
        file_map[f'p{i+1}'] = f

    # Sort by votes desc, with deterministic tiebreaker:
    #   1. more votes wins
    #   2. matzad-origin ranks above palash-origin (established song wins tie)
    #   3. lower original slot wins (slot #1 above slot #20; palash 1 above palash 5)
    def sort_key(s):
        return (
            -tally[s['id']],
            0 if s.get('group') == 'matzad' else 1,
            s.get('slot') or 99,
        )
    sorted_songs = sorted(poll['songs'], key=sort_key)
    top_20 = sorted_songs[:20]

    # Build per-position entries
    ranking = []
    for new_pos, s in enumerate(top_20, start=1):
        entry = {
            'new_slot':    new_pos,                              # 1 = top
            'song_id':     s['id'],
            'label':       s['label'],
            'votes':       tally[s['id']],
            'old_group':   s.get('group'),
            'old_slot':    s.get('slot') if s.get('group') == 'matzad' else None,
            'palash_idx':  s.get('slot') if s.get('group') == 'palash' else None,
            'delta':       None,
            'badges':      [],
            'source_file': file_map.get(s['id']),
            'spotify_url': s.get('spotify_url'),
        }
        if s.get('group') == 'matzad' and s.get('slot'):
            entry['delta'] = s['slot'] - new_pos
        ranking.append(entry)

    # Highlight computations
    new_entries = [e for e in ranking if e['old_group'] == 'palash']
    knisa_new_ids = [e['song_id'] for e in new_entries]
    knisa_id  = min(new_entries, key=lambda e: e['new_slot'])['song_id'] if new_entries else None

    matzad_origin = [e for e in ranking if e['old_group'] == 'matzad' and e['delta'] is not None]
    aliya_id  = None
    yerida_id = None
    if matzad_origin:
        best_up   = max(matzad_origin, key=lambda e: e['delta'])
        worst_dn  = min(matzad_origin, key=lambda e: e['delta'])
        if best_up['delta']  > 0: aliya_id  = best_up['song_id']
        if worst_dn['delta'] < 0: yerida_id = worst_dn['song_id']

    # Apply badges to ranking (knisa & knisa_new can both apply to same song)
    for e in ranking:
        if e['song_id'] in knisa_new_ids: e['badges'].append('knisa_new')
        if e['song_id'] == knisa_id:      e['badges'].append('knisa')
        if e['song_id'] == aliya_id:      e['badges'].append('aliya')
        if e['song_id'] == yerida_id:     e['badges'].append('yerida')

    return {
        'poll_id':         poll_id,
        'poll_title':      poll.get('title'),
        'source_show_id':  src_id,
        'total_voters':    len(all_votes),
        'ranking':         ranking,
        'highlights': {
            'knisa_new': knisa_new_ids,
            'knisa':     knisa_id,
            'aliya':     aliya_id,
            'yerida':    yerida_id,
        },
    }, None


@app.route('/api/poll/<poll_id>/next-chart')
def api_poll_next_chart(poll_id):
    """Admin: preview the auto-computed next chart from a poll's votes."""
    result, err = _compute_next_chart(poll_id)
    if err:
        return jsonify({'error': err}), 404
    return jsonify(result)


@app.route('/api/matzad-chart/create-from-poll', methods=['POST'])
def api_matzad_chart_create_from_poll():
    """Admin: create a new matzad schedule entry from a poll's results.

    Multipart form:
      poll_id      — required
      manual_date  — optional YYYY-MM-DD; otherwise next Thursday
      palash_0..palash_4 — 5 NEW פל״ש song uploads (required)
    """
    poll_id     = (request.form.get('poll_id')     or '').strip()
    manual_date = (request.form.get('manual_date') or '').strip()
    if not poll_id:
        return jsonify({'error': 'poll_id required'}), 400

    chart, err = _compute_next_chart(poll_id)
    if err:
        return jsonify({'error': err}), 404
    if len(chart['ranking']) < 20:
        return jsonify({'error': f"chart has only {len(chart['ranking'])} songs (need 20)"}), 400

    # Verify all top-20 entries have a resolvable source file (sanity check)
    missing = [e['song_id'] for e in chart['ranking'] if not e['source_file']]
    if missing:
        return jsonify({'error': f'source files missing for: {", ".join(missing)}'}), 400

    # Collect 5 new פל״ש uploads
    palash_raw = []
    for i in range(5):
        pf = request.files.get(f'palash_{i}')
        if not pf or not pf.filename:
            return jsonify({'error': f'palash_{i} file is required'}), 400
        palash_raw.append(pf)

    # Resolve broadcast date — matzad_harok config says Thursday 13:00
    show_cfg = next((s for s in SHOW_SCHEDULE if s['key'] == 'matzad_harok'), None)
    if not show_cfg:
        return jsonify({'error': 'matzad show config not found'}), 500
    if manual_date:
        try:
            broadcast_dt = datetime.strptime(manual_date, '%Y-%m-%d')
            h, m = map(int, show_cfg['time'].split(':'))
            broadcast_dt = broadcast_dt.replace(hour=h, minute=m)
        except Exception:
            return jsonify({'error': 'invalid manual_date (YYYY-MM-DD)'}), 400
    else:
        broadcast_dt = _next_broadcast_dt(show_cfg)
        if not broadcast_dt:
            return jsonify({'error': 'could not determine next broadcast date'}), 500

    # Duplicate guard (same as api_add_show)
    bcast_iso = broadcast_dt.isoformat()
    existing = load_schedule()
    dup = next((e for e in existing
                if e.get('show_key') == 'matzad_harok'
                and e.get('scheduled_time') == bcast_iso
                and not e.get('is_rerun')), None)
    if dup:
        return jsonify({'error': f'a matzad episode is already scheduled for {bcast_iso}'}), 409

    upload_dt = _calc_upload_dt(broadcast_dt, show_cfg)
    rerun_dt  = _calc_rerun_dt(broadcast_dt, show_cfg)
    name      = _show_label(show_cfg)
    show_id   = str(int(time.time() * 1000))

    # Copy each top-20 source file to a new path with the new show_id prefix.
    # This decouples the new chart from the source episode's lifecycle.
    import shutil as _shutil
    playlist_paths  = []
    playlist_slots  = []   # 1-based slot numbers in the new chart
    playlist_badges = []   # per-index list of badge keys (index 0 = slot #1)
    for e in chart['ranking']:
        slot_idx = e['new_slot'] - 1                      # 0-based: slot #1 → idx 0
        src_path = e['source_file']
        safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                            for c in os.path.basename(src_path))
        # Strip any old show_id prefix to keep names tidy
        import re as _re
        safe_name = _re.sub(r'^\d+_p[la]\d+_', '', safe_name)
        fname = f"{show_id}_pl{slot_idx:02d}_{safe_name}"
        new_path = os.path.join(LOCAL_TEMP, fname)
        try:
            _shutil.copy2(src_path, new_path)
        except Exception as ex:
            return jsonify({'error': f'failed to copy source file for slot #{e["new_slot"]}: {ex}'}), 500
        playlist_paths.append(new_path)
        playlist_slots.append(e['new_slot'])
        playlist_badges.append(list(e['badges']))   # copy

    # playlist_badges is currently in new_slot order (slot #1 first).
    # Existing format expects index 0 = slot #1, which matches — good.

    # Save the 5 new פל״ש files
    palash_paths = []
    for idx, pf in enumerate(palash_raw):
        safe_name = "".join(c if c.isalnum() or c in ' _-.' else '_'
                            for c in os.path.basename(pf.filename))
        fname = f"{show_id}_pa{idx:02d}_{safe_name}"
        lpath = os.path.join(LOCAL_TEMP, fname)
        pf.save(lpath)
        palash_paths.append(lpath)

    all_tracks = playlist_paths + palash_paths
    show = {
        'id':              show_id,
        'name':            name,
        'show_key':        'matzad_harok',
        'broadcaster':     show_cfg.get('broadcaster', ''),
        'mode':            'queue_only',
        'episode_num':     '',
        'description':     f'Auto-generated from poll: {chart.get("poll_title") or poll_id}',
        'scheduled_time':  broadcast_dt.isoformat(),
        'upload_time':     upload_dt.isoformat() if upload_dt else None,
        'rerun_time':      rerun_dt.isoformat()  if rerun_dt  else None,
        'file_path':       all_tracks[0],
        'nas_path':        all_tracks[0],
        'nas_ready':       True,
        'albums':          None,
        'playlist_files':  playlist_paths,
        'playlist_slots':  playlist_slots,
        'playlist_badges': playlist_badges,
        'palash_files':    palash_paths,
        'files':           all_tracks,
        'original_name':   f'20 מקום + 5 פל״ש (auto from poll {poll_id})',
        'triggered':       False,
        'rerun_scheduled': False,
        'upload_done':     False,
        'is_rerun':        False,
        'added_at':        datetime.now().isoformat(),
        'auto_from_poll':  poll_id,
    }
    with _schedule_lock:
        sched = load_schedule()
        sched.append(show)
        rerun = _make_rerun_entry(show)
        if rerun:
            sched.append(rerun)
            show['rerun_scheduled'] = True
        save_schedule(sched)

    threading.Thread(target=_sync_wp_board, daemon=True).start()
    print(f"[NextChart] Auto-created matzad episode {show_id} from poll {poll_id} for {bcast_iso}", flush=True)
    return jsonify({'ok': True, 'show': show, 'chart': chart})


# ─── Al HaRoker — monthly invite emails ───────────────────────────────────────

def _send_monthly_invite_email(subscriber, next_year, next_month):
    """Send a single monthly invite email to one subscriber."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[AlHaRoker] SMTP not configured — skipping invite to {subscriber['email']}", flush=True)
        return
    try:
        month_name = _HEB_MONTHS[next_month]
        link       = f"{ZEROCK_PUBLIC_URL}/al-haroker-schedule/{next_year}/{next_month}"
        unsub_url  = f"{ZEROCK_PUBLIC_URL}/unsubscribe/{_get_unsubscribe_token(subscriber['email'])}"

        body_plain = (
            f"היי רוקרים ורוקריות,\n\n"
            f"מוזמנים להרשם לעריכת על הרוקר ב\u05f4רדיו זה רוק\u05f4!!!\n"
            f"הנה הלינק:\n{link}\n\n"
            f"בגלל עומס הבקשות, המערכת מאפשרת רישום אחד כל חודש.\n\n"
            f"Keep on Rockin' !!!\n"
            f"צוות רדיו זה רוק\n\n"
            f"לביטול הרשמה: {unsub_url}"
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
            f'<p style="text-align:center;margin-top:20px">'
            f'<a href="{unsub_url}" style="color:#bbb;font-size:12px;text-decoration:none">'
            'הסר אותי מרשימת התפוצה</a></p>'
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
    # Filter out inactive and unsubscribed
    subs = [s for s in subs if s.get('active', True) and not _is_unsubscribed(s.get('email', ''))]
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


# =============================================================================
# NEW HEBREW RELEASES — broadcasters' inbox of incoming song demos
# -----------------------------------------------------------------------------
# Artists send their new tracks to rockzerock@gmail.com. This module:
#   1. Polls the inbox via IMAP every 5 minutes (no Seen-flag mutation, so it
#      doesn't disturb the team's actual inbox state).
#   2. Pulls audio attachments from any email seen in the last 100 days.
#   3. Saves them to NAS at /mnt/nas/Music/NewReleases/ — converts non-MP3
#      sources to MP3 via ffmpeg so broadcasters always download a uniform
#      format.
#   4. Indexes everything in new_releases.json with sender / subject / ID3
#      metadata, deduped by IMAP UID across restarts.
#   5. Renders /new-heb-releases — RTL Hebrew page with HTML5 audio player +
#      download buttons.
#   6. Cleans up entries (and their files) older than 90 days, hourly.
# =============================================================================
import imaplib
import email as _email_mod
from email.header import decode_header as _email_decode_header
from email.utils import parsedate_to_datetime as _email_parsedate

NEW_RELEASES_DIR  = "/mnt/nas/Music/NewReleases"
NEW_RELEASES_JSON = f"{RADIO_DIR}/new_releases.json"
NEW_RELEASES_TTL_DAYS = 90
NEW_RELEASES_LOOKBACK_DAYS = 100  # SINCE search window (TTL + small buffer)
NEW_RELEASES_POLL_SEC = 300       # 5 min
NEW_RELEASES_MAX_FILE_BYTES = 60 * 1024 * 1024   # 60 MB per attachment

_IMAP_HOST = "imap.gmail.com"
_IMAP_USER = os.environ.get('ZEROCK_SMTP_USER', '')
_IMAP_PASS = os.environ.get('ZEROCK_SMTP_PASS', '')

_AUDIO_MIMETYPES = {
    'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/wave',
    'audio/flac', 'audio/x-flac', 'audio/aac', 'audio/m4a', 'audio/x-m4a',
    'audio/mp4', 'audio/ogg', 'audio/x-aiff', 'audio/aiff',
}
_AUDIO_EXTENSIONS = {
    '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.aiff',
    '.aif', '.wma', '.opus',
}
# Non-audio attachment types we want to keep as downloadable "about the song" docs
_DOC_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.rtf', '.txt',
    '.jpg', '.jpeg', '.png', '.gif', '.webp',
}
NEW_RELEASES_DOCS_DIR = "/mnt/nas/Music/NewReleases/docs"
NEW_RELEASES_MAX_DOC_BYTES = 20 * 1024 * 1024   # 20 MB per doc attachment

_releases_lock = threading.Lock()
# Separate lock guarding the whole poll cycle. Without this, the background
# poller and an admin-triggered /poll-now can both load_releases() at once,
# both see the same UID as missing, and both download+save it — duplicate
# files and duplicate entries.
_nr_poll_lock  = threading.Lock()


def _nr_load():
    """Load new_releases.json — list of release dicts. Empty list on missing/corrupt."""
    try:
        with open(NEW_RELEASES_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _nr_save(data):
    """Atomic write to new_releases.json."""
    tmp = NEW_RELEASES_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, NEW_RELEASES_JSON)


def _nr_decode_header(raw):
    """Decode an RFC 2047 MIME-encoded header (handles Hebrew Subject/From)."""
    if not raw:
        return ''
    parts = _email_decode_header(raw)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or 'utf-8', errors='replace'))
            except (LookupError, UnicodeDecodeError):
                out.append(txt.decode('utf-8', errors='replace'))
        else:
            out.append(txt)
    return ''.join(out).strip()


def _nr_safe_filename(name):
    """Filesystem-safe filename — keep ASCII alnum, Hebrew, basic punctuation."""
    out = []
    for c in (name or ''):
        if c.isalnum() or c in ' -_.()[]':
            out.append(c)
        elif '֐' <= c <= '׿':  # Hebrew block
            out.append(c)
        else:
            out.append('_')
    cleaned = ''.join(out).strip().strip('.')
    return (cleaned[:120] or 'untitled')


def _nr_ffprobe(path):
    """Best-effort ID3 + duration extraction. Returns dict — empty on failure."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', path],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout or '{}')
        fmt = data.get('format', {}) or {}
        # ffprobe lowercases tag keys for some containers, preserves for others.
        tags = {k.lower(): v for k, v in (fmt.get('tags') or {}).items()}
        return {
            'duration_sec': float(fmt.get('duration', 0) or 0),
            'artist':       tags.get('artist', '') or tags.get('album_artist', '') or '',
            'title':        tags.get('title', '') or '',
            'album':        tags.get('album', '') or '',
        }
    except Exception:
        return {'duration_sec': 0, 'artist': '', 'title': '', 'album': ''}


def _nr_convert_to_mp3(src_path, dst_path):
    """Convert any audio file to 192k stereo MP3. Returns True on success."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', src_path,
             '-vn', '-c:a', 'libmp3lame', '-b:a', '192k', '-ac', '2',
             dst_path],
            capture_output=True, timeout=600
        )
        return (result.returncode == 0
                and os.path.exists(dst_path)
                and os.path.getsize(dst_path) > 1024)
    except Exception:
        return False


def _nr_is_audio_part(part):
    """Heuristic — MIME type or filename extension looks like audio."""
    ct = (part.get_content_type() or '').lower()
    if ct in _AUDIO_MIMETYPES:
        return True
    fn = part.get_filename() or ''
    if fn:
        return os.path.splitext(fn)[1].lower() in _AUDIO_EXTENSIONS
    return False


def _nr_is_doc_part(part):
    """True if this part is a non-audio attachment worth keeping (PDF, image, doc…)."""
    disp = part.get('Content-Disposition', '').lower()
    # Must look like an attachment (explicit or has a name= parameter)
    fn = _nr_decode_header(part.get_filename() or '')
    if not fn:
        return False
    ext = os.path.splitext(fn)[1].lower()
    if ext in _AUDIO_EXTENSIONS:
        return False  # already handled as audio
    if ext not in _DOC_EXTENSIONS:
        return False
    # Skip inline images that are likely signature logos (small, cid: referenced)
    if part.get_content_maintype() == 'image' and 'attachment' not in disp:
        return False
    return True


def _nr_extract_body(msg):
    """Extract plain-text body from an email message (best-effort, ≤2000 chars).

    Prefers text/plain parts. Falls back to a tag-stripped text/html part.
    Skips parts that are attachments (Content-Disposition: attachment).
    Returns '' if nothing useful is found.
    """
    import re as _re
    candidates = []
    for part in msg.walk():
        ct = part.get_content_type()
        disp = part.get('Content-Disposition', '')
        if 'attachment' in disp.lower():
            continue
        if ct == 'text/plain':
            try:
                raw = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                text = raw.decode(charset, errors='replace')
                candidates.insert(0, ('plain', text))  # prefer plain
            except Exception:
                pass
        elif ct == 'text/html' and not any(c[0] == 'plain' for c in candidates):
            try:
                raw = part.get_payload(decode=True) or b''
                charset = part.get_content_charset() or 'utf-8'
                html = raw.decode(charset, errors='replace')
                # Strip tags, collapse whitespace
                text = _re.sub(r'<[^>]+>', ' ', html)
                text = _re.sub(r'[ \t]+', ' ', text)
                candidates.append(('html', text))
            except Exception:
                pass

    for _, text in candidates:
        # Collapse blank lines (≥2 consecutive newlines → single blank line)
        text = _re.sub(r'\n{3,}', '\n\n', text).strip()
        # Remove quoted-reply boilerplate (lines starting with >)
        lines = [l for l in text.splitlines() if not l.strip().startswith('>')]
        text = '\n'.join(lines).strip()
        text = _re.sub(r'\n{3,}', '\n\n', text)
        if text:
            return text[:2000]  # cap at 2000 chars
    return ''


def _nr_process_message(M, msg_uid):
    """Pull one IMAP message. Save audio attachments. Return release dict or None."""
    typ, msg_data = M.uid('FETCH', msg_uid, '(BODY.PEEK[])')
    if typ != 'OK' or not msg_data or not msg_data[0]:
        return None
    raw = msg_data[0][1]
    msg = _email_mod.message_from_bytes(raw)

    subject   = _nr_decode_header(msg.get('Subject', ''))
    from_raw  = msg.get('From', '')
    name, addr = _email_mod.utils.parseaddr(from_raw)
    from_name  = _nr_decode_header(name)
    from_email = (addr or '').strip()
    try:
        rcv_dt = _email_parsedate(msg.get('Date', ''))
        # Strip tzinfo so we compare consistently with our naive datetimes.
        if rcv_dt and rcv_dt.tzinfo is not None:
            rcv_dt = rcv_dt.replace(tzinfo=None)
        received_at = (rcv_dt or datetime.now()).isoformat()
    except Exception:
        received_at = datetime.now().isoformat()

    body_text = _nr_extract_body(msg)

    audio_parts = [p for p in msg.walk()
                   if p.get_content_maintype() != 'multipart' and _nr_is_audio_part(p)]
    if not audio_parts:
        return None

    os.makedirs(NEW_RELEASES_DIR, exist_ok=True)
    saved = []
    for i, part in enumerate(audio_parts):
        orig_name = _nr_decode_header(part.get_filename() or f'song_{i+1}.mp3')
        safe_orig = _nr_safe_filename(orig_name)
        ext = (os.path.splitext(safe_orig)[1] or '.bin').lower()
        ts = int(time.time() * 1000) + i

        try:
            payload = part.get_payload(decode=True)
        except Exception as e:
            print(f"[NewReleases] payload decode error for {orig_name}: {e}", flush=True)
            continue
        if not payload:
            continue
        if len(payload) > NEW_RELEASES_MAX_FILE_BYTES:
            print(f"[NewReleases] skip {orig_name} ({len(payload)} bytes > cap)", flush=True)
            continue

        # Always save the original first
        raw_path = os.path.join(NEW_RELEASES_DIR, f'{ts}_{safe_orig}')
        try:
            with open(raw_path, 'wb') as f:
                f.write(payload)
        except Exception as e:
            print(f"[NewReleases] write error for {orig_name}: {e}", flush=True)
            continue

        # If non-MP3, transcode. Keep mp3_path pointing at whichever is the MP3.
        if ext == '.mp3':
            mp3_path = raw_path
        else:
            stem = os.path.splitext(safe_orig)[0]
            mp3_path = os.path.join(NEW_RELEASES_DIR, f'{ts}_{stem}.mp3')
            if not _nr_convert_to_mp3(raw_path, mp3_path):
                # Conversion failed — fall back to serving the original.
                mp3_path = raw_path

        meta = _nr_ffprobe(mp3_path)
        try:
            size = os.path.getsize(mp3_path)
        except OSError:
            size = 0

        saved.append({
            'filename':     orig_name,
            'safe_name':    safe_orig,
            'path':         mp3_path,
            'orig_path':    raw_path if raw_path != mp3_path else '',
            'size':         size,
            'duration_sec': meta['duration_sec'],
            'id3_artist':   meta['artist'],
            'id3_title':    meta['title'],
            'id3_album':    meta['album'],
        })

    if not saved:
        return None

    # Save non-audio attachments (PDFs, images, docs describing the song)
    doc_parts = [p for p in msg.walk()
                 if p.get_content_maintype() != 'multipart' and _nr_is_doc_part(p)]
    saved_docs = []
    if doc_parts:
        os.makedirs(NEW_RELEASES_DOCS_DIR, exist_ok=True)
    for part in doc_parts:
        orig_name = _nr_decode_header(part.get_filename() or 'attachment')
        safe_name = _nr_safe_filename(orig_name)
        ts = int(time.time() * 1000)
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload or len(payload) > NEW_RELEASES_MAX_DOC_BYTES:
            continue
        doc_path = os.path.join(NEW_RELEASES_DOCS_DIR, f'{ts}_{safe_name}')
        try:
            with open(doc_path, 'wb') as fh:
                fh.write(payload)
        except Exception as e:
            print(f"[NewReleases] doc write error {orig_name}: {e}", flush=True)
            continue
        saved_docs.append({
            'filename':     orig_name,
            'safe_name':    safe_name,
            'path':         doc_path,
            'size':         len(payload),
            'content_type': part.get_content_type() or 'application/octet-stream',
        })

    return {
        'id':          f"{msg_uid.decode()}_{int(time.time() * 1000)}",
        'imap_uid':    msg_uid.decode(),
        'from_name':   from_name,
        'from_email':  from_email,
        'subject':     subject,
        'body_text':   body_text,
        'received_at': received_at,
        'fetched_at':  datetime.now().isoformat(),
        'files':       saved,
        'docs':        saved_docs,
    }


def _nr_poll_once():
    """One IMAP poll cycle. Returns (new_release_count, error_str_or_None).

    Idempotent across restarts: dedupes by IMAP UID using new_releases.json.
    Doesn't mutate Seen flags so the human inbox state is preserved.

    Wrapped in _nr_poll_lock so concurrent calls (background loop +
    /api/new-releases/poll-now) don't double-process the same UIDs.
    """
    if not _nr_poll_lock.acquire(blocking=False):
        return 0, 'poll already in progress'
    try:
        return _nr_poll_once_locked()
    finally:
        _nr_poll_lock.release()


def _nr_poll_once_locked():
    if not _IMAP_USER or not _IMAP_PASS:
        return 0, 'IMAP creds (ZEROCK_SMTP_USER/PASS) not set'
    try:
        M = imaplib.IMAP4_SSL(_IMAP_HOST)
        M.login(_IMAP_USER, _IMAP_PASS)
        M.select('INBOX', readonly=False)
    except Exception as e:
        return 0, f'IMAP login error: {e}'

    try:
        since_str = (datetime.now() - timedelta(days=NEW_RELEASES_LOOKBACK_DAYS))\
                        .strftime('%d-%b-%Y')
        typ, data = M.uid('SEARCH', None, f'(SINCE "{since_str}")')
        if typ != 'OK':
            return 0, 'IMAP search failed'
        uids = (data[0] or b'').split()
        if not uids:
            return 0, None

        with _releases_lock:
            rels = _nr_load()
            seen_uids = {r.get('imap_uid') for r in rels}

        new_count = 0
        for uid in uids:
            if uid.decode() in seen_uids:
                continue
            try:
                entry = _nr_process_message(M, uid)
            except Exception as e:
                print(f"[NewReleases] process error uid={uid}: {e}", flush=True)
                continue
            if not entry:
                # Mark this UID as processed too (so we don't keep parsing the
                # same headerless or audio-less message every poll), with a
                # placeholder so it dedupes — but no files, so cleanup ignores.
                with _releases_lock:
                    rels = _nr_load()
                    if not any(r.get('imap_uid') == uid.decode() for r in rels):
                        rels.append({
                            'id':          f"{uid.decode()}_skip",
                            'imap_uid':    uid.decode(),
                            'from_name':   '',
                            'from_email':  '',
                            'subject':     '',
                            'received_at': datetime.now().isoformat(),
                            'fetched_at':  datetime.now().isoformat(),
                            'files':       [],
                            'no_audio':    True,
                        })
                        _nr_save(rels)
                continue
            with _releases_lock:
                rels = _nr_load()
                # Defensive re-check: if another path slipped this UID in
                # while we were downloading/transcoding, drop the files we
                # just saved and skip — the existing entry wins.
                if any(r.get('imap_uid') == entry['imap_uid'] for r in rels):
                    for f in entry.get('files', []):
                        for path_key in ('path', 'orig_path'):
                            p = f.get(path_key, '')
                            if p and os.path.exists(p):
                                try: os.remove(p)
                                except Exception: pass
                    for d in entry.get('docs', []):
                        p = d.get('path', '')
                        if p and os.path.exists(p):
                            try: os.remove(p)
                            except Exception: pass
                    continue
                # Content-based dedup: skip if same sender already has a file
                # with the same name and size (sender clicked send twice).
                new_keys = {
                    (entry.get('from_email','').lower(), f.get('filename','').lower(), f.get('size',0))
                    for f in entry.get('files', [])
                }
                existing_keys = {
                    (r.get('from_email','').lower(), f.get('filename','').lower(), f.get('size',0))
                    for r in rels if r.get('files')
                    for f in r['files']
                }
                if new_keys & existing_keys:
                    print(f"[NewReleases] skip content-dup uid={entry['imap_uid']} "
                          f"from={entry['from_email']!r}", flush=True)
                    for f in entry.get('files', []):
                        for path_key in ('path', 'orig_path'):
                            p = f.get(path_key, '')
                            if p and os.path.exists(p):
                                try: os.remove(p)
                                except Exception: pass
                    for d in entry.get('docs', []):
                        p = d.get('path', '')
                        if p and os.path.exists(p):
                            try: os.remove(p)
                            except Exception: pass
                    # Still mark the UID as seen so we don't re-check it
                    rels.append({
                        'id': f"{entry['imap_uid']}_skip",
                        'imap_uid': entry['imap_uid'],
                        'from_name': '', 'from_email': '', 'subject': '',
                        'received_at': datetime.now().isoformat(),
                        'fetched_at':  datetime.now().isoformat(),
                        'files': [], 'no_audio': True,
                    })
                    _nr_save(rels)
                    continue
                rels.append(entry)
                _nr_save(rels)
            new_count += 1
            print(f"[NewReleases] +{len(entry['files'])} file(s) from "
                  f"{entry['from_email']!r}: {entry['subject'][:80]!r}", flush=True)
    finally:
        try:
            M.logout()
        except Exception:
            pass

    return new_count, None


def _nr_loop():
    """Background poller thread."""
    print("[NewReleases] poller started", flush=True)
    # Stagger startup so we don't all hit Gmail in the first second after restart
    time.sleep(20)
    while True:
        try:
            n, err = _nr_poll_once()
            if err:
                print(f"[NewReleases] poll: {err}", flush=True)
            elif n:
                print(f"[NewReleases] poll: +{n} new release(s)", flush=True)
        except Exception as e:
            print(f"[NewReleases] loop error: {e}", flush=True)
        time.sleep(NEW_RELEASES_POLL_SEC)


def _nr_cleanup_loop():
    """Hourly cleanup: drop entries (and their files) older than TTL days."""
    while True:
        time.sleep(3600)
        cutoff = datetime.now() - timedelta(days=NEW_RELEASES_TTL_DAYS)
        with _releases_lock:
            rels = _nr_load()
        kept, removed = [], 0
        for r in rels:
            try:
                rcv = datetime.fromisoformat((r.get('received_at') or '').replace('Z', ''))
                if rcv.tzinfo is not None:
                    rcv = rcv.replace(tzinfo=None)
            except Exception:
                rcv = datetime.now()
            if rcv < cutoff:
                # Delete audio files (both mp3 and original)
                for f in r.get('files', []) or []:
                    for path_key in ('path', 'orig_path'):
                        p = f.get(path_key, '')
                        if p and os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                # Delete non-audio doc attachments
                for d in r.get('docs', []) or []:
                    p = d.get('path', '')
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                removed += 1
            else:
                kept.append(r)
        if removed:
            with _releases_lock:
                _nr_save(kept)
            print(f"[NewReleases] cleanup: removed {removed} entries (>{NEW_RELEASES_TTL_DAYS}d old)",
                  flush=True)


threading.Thread(target=_nr_loop,         daemon=True).start()
threading.Thread(target=_nr_cleanup_loop, daemon=True).start()


@app.route('/new-heb-releases')
def new_heb_releases_page():
    """Hebrew RTL page listing new releases sent to rockzerock@gmail.com."""
    return render_template('new_releases.html')


@app.route('/api/new-releases')
def api_new_releases():
    """JSON list of releases (newest first), absolute paths stripped from output."""
    rels = _nr_load()
    rels = [r for r in rels if r.get('files') and not r.get('no_audio')]
    rels.sort(key=lambda r: r.get('received_at', ''), reverse=True)
    out = []
    for r in rels:
        files = []
        for i, f in enumerate(r.get('files', []) or []):
            files.append({
                'idx':          i,
                'filename':     f.get('filename', ''),
                'size':         f.get('size', 0),
                'duration':     f.get('duration_sec', 0),
                'artist':       f.get('id3_artist', ''),
                'title':        f.get('id3_title', ''),
                'album':        f.get('id3_album', ''),
                'play_url':     f"/api/new-releases/{r['id']}/play/{i}",
                'download_url': f"/api/new-releases/{r['id']}/download/{i}",
            })
        docs = []
        for j, d in enumerate(r.get('docs', []) or []):
            docs.append({
                'idx':          j,
                'filename':     d.get('filename', ''),
                'size':         d.get('size', 0),
                'content_type': d.get('content_type', ''),
                'doc_url':      f"/api/new-releases/{r['id']}/doc/{j}",
            })
        out.append({
            'id':          r['id'],
            'from_name':   r.get('from_name', ''),
            'from_email':  r.get('from_email', ''),
            'subject':     r.get('subject', ''),
            'body_text':   r.get('body_text', ''),
            'received_at': r.get('received_at', ''),
            'files':       files,
            'docs':        docs,
        })
    return jsonify(out)


def _nr_get_file(rel_id, idx):
    rels = _nr_load()
    r = next((x for x in rels if x.get('id') == rel_id), None)
    if not r:
        return None, None
    files = r.get('files', []) or []
    if idx < 0 or idx >= len(files):
        return r, None
    return r, files[idx]


@app.route('/api/new-releases/<rel_id>/play/<int:idx>')
def api_new_releases_play(rel_id, idx):
    """Stream an audio file inline for browser <audio> playback."""
    _, f = _nr_get_file(rel_id, idx)
    if not f or not os.path.exists(f.get('path', '')):
        return ('Not found', 404)
    return send_file(f['path'], mimetype='audio/mpeg', conditional=True)


@app.route('/api/new-releases/<rel_id>/download/<int:idx>')
def api_new_releases_download(rel_id, idx):
    """Force-download as MP3 with a friendly artist-title.mp3 name."""
    _, f = _nr_get_file(rel_id, idx)
    if not f or not os.path.exists(f.get('path', '')):
        return ('Not found', 404)
    artist = (f.get('id3_artist') or '').strip()
    title  = (f.get('id3_title')  or '').strip()
    if artist and title:
        download_name = f"{artist} - {title}.mp3"
    else:
        base = os.path.splitext(f.get('filename', 'song'))[0]
        download_name = f"{base or 'song'}.mp3"
    download_name = _nr_safe_filename(download_name)
    return send_file(f['path'], as_attachment=True,
                     download_name=download_name, mimetype='audio/mpeg')


@app.route('/api/new-releases/<rel_id>/doc/<int:idx>')
def api_new_releases_doc(rel_id, idx):
    """Force-download a non-audio attachment (PDF, image, doc…)."""
    rels = _nr_load()
    r = next((x for x in rels if x.get('id') == rel_id), None)
    if not r:
        return ('Not found', 404)
    docs = r.get('docs', []) or []
    if idx < 0 or idx >= len(docs):
        return ('Not found', 404)
    d = docs[idx]
    path = d.get('path', '')
    if not path or not os.path.exists(path):
        return ('Not found', 404)
    return send_file(path, as_attachment=True,
                     download_name=d.get('filename', 'attachment'),
                     mimetype=d.get('content_type', 'application/octet-stream'))


@app.route('/api/new-releases/poll-now', methods=['POST'])
def api_new_releases_poll_now():
    """Admin trigger — force an immediate IMAP poll instead of waiting 5 min."""
    n, err = _nr_poll_once()
    return jsonify({'ok': err is None, 'new': n, 'error': err})


if __name__ == '__main__':
    print("ZeRock Radio web interface starting on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
