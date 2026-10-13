#!/usr/bin/env python3
"""Per-song loudness measurement for Rocky's volume normalization.

Measures each music file ONCE (EBU R128 integrated loudness via ffmpeg), keeps the
result in a cache, and writes a Liquidsoap-ready playlist in which every line carries
the gain needed to bring that song to ROCKY_TARGET_LUFS:

    annotate:liq_amplify="-3.20 dB":/mnt/nas/Music/.../song.mp3

Liquidsoap's amplify() applies that metadata gain, so nothing is analysed while a
song is being played (analysing at play time stalled on large FLAC files).

Standard library only. Usable as a module (imported by radio_app.py) or as a script:
    python3 rocky_loudness.py scan     # measure every song in english.m3u + hebrew.m3u
                                       # then (re)write english_liq.m3u / hebrew_liq.m3u
    python3 rocky_loudness.py write    # only rewrite the *_liq.m3u from the cache
    python3 rocky_loudness.py stats    # coverage + spread of the cache
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

RADIO_DIR     = os.environ.get('ROCKY_RADIO_DIR', '/home/roy/zerock-radio')
PLAYLIST_DIR  = f"{RADIO_DIR}/playlists"
CACHE_FILE    = f"{RADIO_DIR}/rocky_loudness.json"

# Station level for Rocky MUSIC (shows sit around -11.5 LUFS). Change this one number and
# rewrite the playlists (python3 rocky_loudness.py write) to make Rocky louder / quieter.
TARGET_LUFS   = -13.0
GAIN_MIN_DB   = -15.0      # never attenuate more than this
GAIN_MAX_DB   = 12.0       # never boost more than this
PEAK_CEILING_DB = 1.0      # a boost may not push a song's peak above this (dBFS)
MEASURE_SECS  = 600        # measure at most the first 10 minutes of a file

PAIRS = [   # (plain playlist the app uses, Liquidsoap playlist with gains)
    (f"{PLAYLIST_DIR}/english.m3u", f"{PLAYLIST_DIR}/english_liq.m3u"),
    (f"{PLAYLIST_DIR}/hebrew.m3u",  f"{PLAYLIST_DIR}/hebrew_liq.m3u"),
]

_cache_lock = threading.Lock()


def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    d = os.path.dirname(CACHE_FILE) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.rocky_loudness.', dir=d)
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


def _stat_key(path):
    try:
        st = os.stat(path)
        return st.st_size, int(st.st_mtime)
    except OSError:
        return None


def measure(path):
    """Integrated loudness (LUFS) and peak (dBFS) of a file, or None if it can't be measured."""
    try:
        r = subprocess.run(
            ['nice', '-n', '15', 'ffmpeg', '-nostats', '-hide_banner', '-v', 'info',
             '-t', str(MEASURE_SECS), '-i', path, '-vn', '-af', 'ebur128=peak=true',
             '-f', 'null', '-'],
            capture_output=True, text=True, timeout=600)
        tail = r.stderr[-1500:]
        i = re.findall(r'I:\s+(-?[\d.]+) LUFS', tail)
        p = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', tail)
        if not i:
            return None
        lufs = float(i[-1])
        if lufs < -60:                      # silence / unreadable: do not "boost" it
            return None
        return lufs, (float(p[-1]) if p else None)
    except Exception:
        return None


def gain_db(entry):
    """dB to apply so the song reaches TARGET_LUFS, but never boost a song so far that its
    peaks would exceed PEAK_CEILING_DB (very dynamic songs then land a little under target
    instead of clipping)."""
    g = TARGET_LUFS - float(entry['lufs'])
    peak = entry.get('peak')
    if g > 0 and peak is not None:
        g = min(g, max(0.0, PEAK_CEILING_DB - float(peak)))
    return max(GAIN_MIN_DB, min(GAIN_MAX_DB, g))


def liq_line(path, cache):
    """Playlist line for one song: annotated with its gain when measured, else plain."""
    e = cache.get(path)
    key = _stat_key(path) if (e and e.get('lufs') is not None) else None
    if e and key and e.get('size') == key[0] and e.get('mtime') == key[1]:
        # annotate: uses ':' as a separator — leave paths that contain one un-annotated
        if ':' not in path and '"' not in path:
            return f'annotate:liq_amplify="{gain_db(e):.2f} dB":{path}'
    return path


def write_liq_playlist(plain_path, liq_path, cache):
    """Atomically write liq_path from the plain playlist. Returns (total, annotated)."""
    with open(plain_path, 'r', encoding='utf-8') as f:
        tracks = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    lines = [liq_line(t, cache) for t in tracks]
    d = os.path.dirname(liq_path) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.liq_pl.', dir=d)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, liq_path)
    return len(lines), sum(1 for l in lines if l.startswith('annotate:'))


def write_all(cache=None):
    cache = load_cache() if cache is None else cache
    out = []
    for plain, liq in PAIRS:
        if os.path.exists(plain):
            out.append((os.path.basename(liq),) + write_liq_playlist(plain, liq, cache))
    return out


def scan(paths, max_new=None, workers=2, log=print, save_every=25):
    """Measure every path not yet (validly) in the cache. Returns number newly measured."""
    with _cache_lock:
        cache = load_cache()
    todo = []
    for p in paths:
        e = cache.get(p)
        key = _stat_key(p)
        if key is None:
            continue
        if e and e.get('size') == key[0] and e.get('mtime') == key[1]:
            continue
        todo.append((p, key))
    if max_new is not None:
        todo = todo[:max_new]
    done = 0
    t0 = time.time()

    def one(item):
        p, key = item
        return p, key, measure(p)

    processed = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for p, key, m in ex.map(one, todo):
            processed += 1
            if m is not None:
                cache[p] = {'size': key[0], 'mtime': key[1], 'lufs': round(m[0], 2),
                            'peak': m[1], 'at': int(time.time())}
                done += 1
            else:
                # remember failures too (so they are not retried every pass); no gain is written for them
                cache[p] = {'size': key[0], 'mtime': key[1], 'lufs': None, 'peak': None,
                            'at': int(time.time()), 'failed': True}
            if processed % save_every == 0:
                with _cache_lock:
                    save_cache(cache)
                log(f"[loudness] {processed}/{len(todo)} processed ({time.time() - t0:.0f}s)")
    with _cache_lock:
        save_cache(cache)
    log(f"[loudness] scan finished: {done} measured of {len(todo)} pending in {time.time() - t0:.0f}s")
    return done


def _all_tracks():
    seen, out = set(), []
    for plain, _ in PAIRS:
        if not os.path.exists(plain):
            continue
        with open(plain, 'r', encoding='utf-8') as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith('#') and l not in seen:
                    seen.add(l)
                    out.append(l)
    return out


def stats():
    cache = load_cache()
    tracks = _all_tracks()
    meas = [cache[t]['lufs'] for t in tracks if t in cache and cache[t].get('lufs') is not None]
    failed = sum(1 for t in tracks if t in cache and cache[t].get('failed'))
    print(f"tracks in playlists: {len(tracks)} | measured: {len(meas)} | failed: {failed} | not yet: {len(tracks) - len(meas) - failed}")
    if meas:
        m = sum(meas) / len(meas)
        sd = (sum((x - m) ** 2 for x in meas) / len(meas)) ** 0.5
        print(f"raw loudness: mean {m:.1f} LUFS, stdev {sd:.2f}, min {min(meas):.1f}, max {max(meas):.1f} (spread {max(meas) - min(meas):.1f} LU)")
        clamped = [x for x in meas if not (GAIN_MIN_DB <= TARGET_LUFS - x <= GAIN_MAX_DB)]
        after = [t_lufs + gain_db(cache[t]) for t in tracks if t in cache and cache[t].get('lufs') is not None for t_lufs in [cache[t]['lufs']]]
        print(f"after leveling (target {TARGET_LUFS}): min {min(after):.1f}, max {max(after):.1f}; songs whose gain hits the limit: {len(clamped)}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    if cmd == 'scan':
        scan(_all_tracks())
        for name, total, ann in write_all():
            print(f"[loudness] wrote {name}: {ann}/{total} songs carry a gain")
    elif cmd == 'write':
        for name, total, ann in write_all():
            print(f"[loudness] wrote {name}: {ann}/{total} songs carry a gain")
    else:
        stats()
