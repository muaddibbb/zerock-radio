#!/usr/bin/env python3
"""Per-song ending/opening analysis for Rocky's smart transitions.

For every music file this measures (once, cached) how the song ENDS and how it BEGINS, from
the decoded audio itself (not from duration or metadata):

  ending  : fade | decay | abrupt | unknown     + how long the audible level takes to fall
            (seconds before the audible end at which the level last exceeded body-6/-12/-18/-24 dB),
            the time of the last strong attack, technical silence at the file end, confidence
  opening : hit | full | fade_in | quiet_intro | single_note | unknown   + leading technical silence,
            time until the intro is audibly meaningful (within 12 dB of the song's body level),
            confidence

The numbers are written into the Liquidsoap playlists as annotate tags (see rocky_loudness.liq_line).
Rocky's crossfade function (rocky.liq) then decides, for every actual PAIR of songs, when the next
song starts and how long the overlap lasts. This module only describes single songs.

What it does NOT do (no vocal/ML engine is installed on the server): it cannot tell singing from
instruments. Instead every opening that is not clearly a quiet / fading / single-note introduction is
treated as "may contain vocals or a full-band hit" and is never overlapped with audible music.

Standard library + ffmpeg only.
    python3 rocky_transitions.py analyze <file>          # print the analysis of one song
    python3 rocky_transitions.py scan                    # measure all songs in english.m3u / hebrew.m3u
    python3 rocky_transitions.py stats                   # class counts
"""
import audioop
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

RADIO_DIR    = os.environ.get('ROCKY_RADIO_DIR', '/home/roy/zerock-radio')
PLAYLIST_DIR = f"{RADIO_DIR}/playlists"
CACHE_FILE   = f"{RADIO_DIR}/rocky_transitions.json"
OVERRIDES_FILE = f"{RADIO_DIR}/rocky_transitions_overrides.json"
ANALYSIS_VERSION = 1

SR       = 11025          # analysis sample rate (mono)
HOP      = 0.01           # envelope resolution (s)
WIN      = 0.02           # envelope window (s)
EDGE_SECS = 30.0          # how much of each end of the file is analysed
SIL_ABS_DB = -60.0        # below this = technical silence
AUDIBLE_ABS_DB = -55.0    # floor of "audible"
MAX_WINDOW = 12.0         # seconds of the outgoing song the crossfade may use (rocky.liq cross duration)

_cache_lock = threading.Lock()


# ── decoding / envelope ──────────────────────────────────────────────────────
def _decode(path, tail):
    """Mono 16-bit PCM of the first or last EDGE_SECS of the file (b'' on failure)."""
    cmd = ['nice', '-n', '15', 'ffmpeg', '-nostdin', '-v', 'error']
    if tail:
        cmd += ['-sseof', f'-{EDGE_SECS + 1:.0f}']
    cmd += ['-i', path]
    if not tail:
        cmd += ['-t', f'{EDGE_SECS:.0f}']
    cmd += ['-vn', '-ac', '1', '-ar', str(SR), '-f', 's16le', '-']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=240)
        return r.stdout if r.returncode == 0 else b''
    except Exception:
        return b''


def _envelope(pcm):
    """dBFS RMS every HOP seconds (window WIN)."""
    step = int(SR * HOP) * 2
    win = int(SR * WIN) * 2
    out = []
    for i in range(0, max(0, len(pcm) - win + 1), step):
        r = audioop.rms(pcm[i:i + win], 2)
        out.append(20.0 * math.log10(r / 32768.0) if r > 0 else -120.0)
    return out


def _smooth(env, n):
    """Moving average of the linear power over n frames, back in dB (keeps length)."""
    p = [10 ** (e / 10.0) for e in env]
    out, acc = [], 0.0
    for i, v in enumerate(p):
        acc += v
        if i >= n:
            acc -= p[i - n]
        out.append(10.0 * math.log10(max(acc / min(i + 1, n), 1e-12)))
    return out


def _pct(vals, q):
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * q))] if v else -120.0


def _onsets(env, floor_db):
    """Frame indexes where the level jumps up sharply (attacks), >=120 ms apart."""
    out, last = [], -100
    for i in range(8, len(env)):
        if env[i] < floor_db:
            continue
        base = min(env[i - 8:i - 2])
        if env[i] - base >= 7.0 and env[i] >= env[i - 1] and i - last >= 12:
            out.append(i)
            last = i
    return out


def _mono_frac(env_s, i0, i1, sign):
    """Share of 100 ms steps in [i0,i1] that move in the expected direction (sign=-1 falling, +1 rising)."""
    pts = env_s[i0:i1 + 1:10]
    if len(pts) < 3:
        return 0.0
    good = sum(1 for a, b in zip(pts, pts[1:]) if (b - a) * sign >= -1.0)
    return good / (len(pts) - 1)


# ── ending ───────────────────────────────────────────────────────────────────
def analyze_end(pcm):
    env = _envelope(pcm)
    n = len(env)
    if n < 300:
        return {'oe': 'unknown', 'oe_conf': 0.0, 'oe_sil': 0.0}
    last_any = max((i for i, e in enumerate(env) if e > SIL_ABS_DB), default=-1)
    if last_any < 0:
        return {'oe': 'unknown', 'oe_conf': 0.0, 'oe_sil': 0.0}
    sm = _smooth(env, 25)                      # 250 ms power average
    body = _pct([e for e in sm[:last_any + 1] if e > -50], 0.85)
    aud_floor = max(AUDIBLE_ABS_DB, body - 45.0)
    last_aud = max((i for i in range(last_any + 1) if sm[i] > aud_floor), default=last_any)
    sil = (n - 1 - last_aud) * HOP             # file end -> last audible frame (technical silence / dust)

    def t_before(k):
        """seconds before the audible end at which the level last exceeded body-k dB"""
        for i in range(last_aud, -1, -1):
            if sm[i] >= body - k:
                return (last_aud - i) * HOP
        return EDGE_SECS

    t = {k: t_before(k) for k in (3, 6, 12, 18, 24, 30)}
    span = t[6]
    ons = _onsets(env[:last_aud + 1], body - 30)
    last_on = max((i for i in ons if env[i] >= body - 12), default=None)
    t_att = (last_aud - last_on) * HOP if last_on is not None else EDGE_SECS
    # onset density while the level is falling (after the first 0.4 s)
    lo = max(0, last_aud - int(span / HOP) + 40)
    dens = sum(1 for i in ons if lo <= i <= last_aud - 30) / max(0.5, (last_aud - 30 - lo) * HOP) if span > 0.8 else 0.0
    mono = _mono_frac(sm, max(0, last_aud - int((t[30] if t[30] < EDGE_SECS else span + 4) / HOP)), last_aud, -1)

    # a gap of >=1.5 s of silence inside the last 30 s followed by more music -> hidden track / intentional silence
    gap = False
    run = 0
    for i in range(last_aud):
        run = run + 1 if env[i] <= SIL_ABS_DB else 0
        if run >= 150 and i < last_aud - 100:
            gap = True
    kind, conf = 'unknown', 0.3
    if span <= 0.35 and env[max(0, last_aud - 10)] >= body - 12:
        kind, conf = 'abrupt', 0.9
    elif span > 0.35:
        fall = t[30] - t[6] if t[30] < EDGE_SECS else None
        if dens >= 1.2 and mono >= 0.6 and span >= 1.5:
            kind, conf = 'fade', 0.55 + 0.35 * min(1.0, mono)
        elif dens < 1.0 and span >= 0.8 and mono >= 0.6:
            # sparse tail that decays: a ring-out of a final note / chord (or a fade of sparse music)
            kind = 'decay' if span < 6.0 else 'fade'
            conf = 0.5 + 0.35 * min(1.0, mono)
        elif span >= 3.0 and mono >= 0.5:
            kind, conf = 'fade', 0.5
        else:
            kind, conf = 'unknown', 0.35
    if gap:
        conf = min(conf, 0.3)
    if sil > 8.0:                               # more than 8 s of "dust" after the music: suspicious
        conf = min(conf, 0.4)
    return {'oe': kind, 'oe_conf': round(conf, 2), 'oe_sil': round(sil, 2),
            'oe_t6': round(t[6], 2), 'oe_t12': round(t[12], 2), 'oe_t18': round(t[18], 2),
            'oe_t24': round(t[24], 2), 'oe_att': round(min(t_att, EDGE_SECS), 2),
            'oe_dens': round(dens, 2), 'oe_body': round(body, 1), 'oe_gap': gap}


# ── opening ──────────────────────────────────────────────────────────────────
def analyze_start(pcm):
    env = _envelope(pcm)
    n = len(env)
    if n < 300:
        return {'ie': 'unknown', 'ie_conf': 0.0, 'ie_sil': 0.0}
    first_any = next((i for i, e in enumerate(env) if e > SIL_ABS_DB), None)
    if first_any is None:
        return {'ie': 'unknown', 'ie_conf': 0.0, 'ie_sil': 0.0}
    sm = _smooth(env, 25)
    body = _pct([e for e in sm[first_any:] if e > -50], 0.85)
    aud_floor = max(AUDIBLE_ABS_DB, body - 45.0)
    first_aud = next((i for i in range(first_any, n) if env[i] > aud_floor), first_any)
    sil = first_aud * HOP                       # leading technical silence

    def t_after(k):
        """seconds after the first audible frame at which the level first reaches body-k dB"""
        for i in range(first_aud, n):
            if sm[i] >= body - k:
                return (i - first_aud) * HOP
        return EDGE_SECS

    t = {k: t_after(k) for k in (3, 6, 12, 18, 24, 30)}
    ons = _onsets(env, body - 30)
    ons_in = [i for i in ons if i >= first_aud - 3]
    first_on = ons_in[0] if ons_in else None
    lvl_early = env[min(n - 1, first_aud + 15)]            # 150 ms after the start
    lvl_1s = sm[min(n - 1, first_aud + 100)]
    n_on_3s = sum(1 for i in ons_in if i <= first_aud + 300)
    mono = _mono_frac(sm, first_aud, min(n - 1, first_aud + int(max(t[6], 1.0) / HOP)), +1)

    kind, conf = 'unknown', 0.35
    if lvl_early >= body - 9 and first_on is not None and (first_on - first_aud) <= 20:
        kind, conf = 'hit', 0.9                           # full-level entrance with an immediate attack
    elif lvl_early >= body - 9:
        kind, conf = 'full', 0.7                          # already at body level, no clean attack (mid-phrase / loud start)
    elif lvl_early <= body - 24 and t[6] - t[24] >= 1.0 and mono >= 0.7 and n_on_3s <= 2:
        kind, conf = 'fade_in', 0.6 + 0.3 * mono
    elif n_on_3s <= 1 and first_on is not None and lvl_1s <= body - 15 and t[12] >= 2.0:
        kind, conf = 'single_note', 0.6
    elif lvl_1s <= body - 12 and t[12] >= 2.0:
        kind, conf = 'quiet_intro', 0.65
    elif lvl_early >= body - 16:
        kind, conf = 'full', 0.55
    if sil > 8.0:
        conf = min(conf, 0.4)
    return {'ie': kind, 'ie_conf': round(conf, 2), 'ie_sil': round(sil, 2),
            'ie_t6': round(t[6], 2), 'ie_t12': round(t[12], 2), 'ie_t18': round(t[18], 2),
            'ie_body': round(body, 1)}


def analyze_file(path):
    tail = _decode(path, True)
    head = _decode(path, False)
    if not tail or not head:
        return None
    a = {}
    a.update(analyze_end(tail))
    a.update(analyze_start(head))
    return a


# ── cache + overrides ────────────────────────────────────────────────────────
def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    fd, tmp = tempfile.mkstemp(prefix='.rocky_trans.', dir=os.path.dirname(CACHE_FILE) or '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_overrides():
    try:
        with open(OVERRIDES_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_overrides(ov):
    fd, tmp = tempfile.mkstemp(prefix='.rocky_trans_ov.', dir=os.path.dirname(OVERRIDES_FILE) or '.')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(ov, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o664)
    os.replace(tmp, OVERRIDES_FILE)


def _stat_key(path):
    try:
        st = os.stat(path)
        return st.st_size, int(st.st_mtime)
    except OSError:
        return None


def entry_valid(e, path):
    k = _stat_key(path)
    return bool(e and k and e.get('size') == k[0] and e.get('mtime') == k[1] and e.get('v') == ANALYSIS_VERSION)


# Override fields (all optional, per song; see /transitions page):
#   oe, ie                      force the ending / opening class
#   out_cue                     "the song is over" this many seconds BEFORE its audible end (cut a long tail)
#   in_cue                      first audible sound is this many seconds after the file start (skip a long intro silence)
#   out_curve, in_curve         none | lin | sin | exp | log   (gain curve forced on this song's end / start)
#   out_fade, in_fade           seconds for those curves
#   no_overlap                  true = never overlap this song with its neighbour
OVERRIDE_KEYS = ('oe', 'ie', 'out_cue', 'in_cue', 'out_curve', 'in_curve', 'out_fade', 'in_fade', 'no_overlap')


def effective(path, cache=None, overrides=None):
    """Analysis of a song with manual overrides applied, or None when not analysed."""
    cache = load_cache() if cache is None else cache
    e = cache.get(path)
    if not entry_valid(e, path) or e.get('failed'):
        return None
    out = dict(e)
    ov = (load_overrides() if overrides is None else overrides).get(path) or {}
    if ov.get('oe'):
        out['oe'], out['oe_conf'] = ov['oe'], 1.0
    if ov.get('ie'):
        out['ie'], out['ie_conf'] = ov['ie'], 1.0
    if ov.get('out_cue') is not None:
        out['oe_sil'] = round(float(out.get('oe_sil', 0)) + float(ov['out_cue']), 2)
    if ov.get('in_cue') is not None:
        out['ie_sil'] = round(float(ov['in_cue']), 2)
    for k in ('out_curve', 'in_curve', 'out_fade', 'in_fade', 'no_overlap'):
        if k in ov and ov[k] not in (None, ''):
            out[k] = ov[k]
    return out


def cross_window(a):
    """Seconds of the outgoing song the crossfade may use. Short for abrupt endings so that the
    "now playing" label (switches when the crossfade starts) does not run ahead of the audio."""
    kind = a.get('oe')
    if kind == 'abrupt':
        w = 3.0
    elif kind == 'decay':
        w = min(10.0, max(4.0, float(a.get('oe_att', 4)) + 1.0))
    elif kind == 'fade':
        w = min(MAX_WINDOW, max(5.0, float(a.get('oe_t18', 4)) + 6.0))
    else:
        w = 4.0
    return round(w + float(a.get('oe_sil', 0)), 1)


def tags_for(path, cache=None, overrides=None):
    """Annotate tag string fragment (without gain) for a song, e.g. 'tr_v="1",tr_oe="fade",...'; '' if none."""
    a = effective(path, cache, overrides)
    if not a:
        return ''
    keys = ['oe', 'oe_conf', 'oe_sil', 'oe_t6', 'oe_t12', 'oe_t18', 'oe_t24', 'oe_att',
            'ie', 'ie_conf', 'ie_sil', 'ie_t12', 'out_curve', 'in_curve', 'out_fade', 'in_fade', 'no_overlap']
    parts = ['tr_v="1"', f'liq_cross_duration="{cross_window(a)}"']
    for k in keys:
        if k in a and a[k] is not None and a[k] != '':
            v = a[k]
            if isinstance(v, bool):
                v = 'true' if v else 'false'
            parts.append(f'tr_{k}="{v}"')
    return ','.join(parts)


# ── scanning ─────────────────────────────────────────────────────────────────
def all_tracks():
    seen, out = set(), []
    for name in ('english.m3u', 'hebrew.m3u'):
        p = f"{PLAYLIST_DIR}/{name}"
        if not os.path.exists(p):
            continue
        with open(p, 'r', encoding='utf-8') as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith('#') and l not in seen:
                    seen.add(l)
                    out.append(l)
    return out


def scan(paths, max_new=None, workers=2, log=print, save_every=25):
    with _cache_lock:
        cache = load_cache()
    todo = []
    for p in paths:
        k = _stat_key(p)
        if k is None or entry_valid(cache.get(p), p):
            continue
        todo.append((p, k))
    if max_new is not None:
        todo = todo[:max_new]
    done = processed = 0
    t0 = time.time()

    def one(item):
        return item[0], item[1], analyze_file(item[0])

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for p, k, a in ex.map(one, todo):
            processed += 1
            e = {'size': k[0], 'mtime': k[1], 'v': ANALYSIS_VERSION, 'at': int(time.time())}
            if a:
                e.update(a)
                done += 1
            else:
                e['failed'] = True
            cache[p] = e
            if processed % save_every == 0:
                with _cache_lock:
                    save_cache(cache)
                log(f"[transitions] {processed}/{len(todo)} analysed ({time.time() - t0:.0f}s)")
    with _cache_lock:
        save_cache(cache)
    log(f"[transitions] scan finished: {done} analysed of {len(todo)} pending in {time.time() - t0:.0f}s")
    return done


def stats():
    from collections import Counter
    cache = load_cache()
    tr = all_tracks()
    ok = [cache[t] for t in tr if t in cache and not cache[t].get('failed') and entry_valid(cache[t], t)]
    print(f"songs: {len(tr)}  analysed: {len(ok)}  failed: {sum(1 for t in tr if cache.get(t, {}).get('failed'))}")
    print("endings :", dict(Counter(e['oe'] for e in ok)))
    print("openings:", dict(Counter(e['ie'] for e in ok)))
    low = sum(1 for e in ok if min(e.get('oe_conf', 0), e.get('ie_conf', 0)) < 0.55)
    print(f"low-confidence (<0.55) on either side: {low}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    if cmd == 'analyze':
        for p in sys.argv[2:]:
            print(p)
            print(json.dumps(analyze_file(p), ensure_ascii=False, indent=1))
    elif cmd == 'scan':
        scan(all_tracks())
    else:
        stats()
